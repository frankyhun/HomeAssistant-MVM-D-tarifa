"""Hiányzó órák pótlása a hosszú távú statisztikákban.

Ha az adatforrásból kiesett egy időszak, az ár-szenzorok szándékosan
`unavailable` állapotban maradnak — inkább semmit, mint rossz árat. Amikor az
adat később megjön, a kiesés lyukként marad a grafikonon. Ez a modul
visszamenőleg kiszámolja a hiányzó órák árát az utólag publikált day-ahead
adatból, és beírja a recorder óránkénti statisztikáiba.

Amit tud és amit nem:

  IGEN  A `statistics` tábla órás átlag/min/max sorai — ezt használja a
        `statistics-graph` kártya és a hosszabb időtávra zoomolt Előzmények.
  NEM   A `states` tábla nyers állapot-története. Azt a Home Assistant nem
        engedi visszamenőleg írni, ott a kiesés `Nem érhető el` marad.

Az írás idempotens: alapból csak azokat az órákat töltjük fel, amelyekre még
nincs statisztika — a HA saját, állapotokból számolt átlagát nem írjuk felül.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import logging

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import (
    async_import_statistics,
    statistics_during_period,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from .calc import tariff_from_options
from .const import CURRENCY_PER_KWH

_LOGGER = logging.getLogger(__name__)

# A recorder metaadat-sémája verzióról verzióra változik: a `has_mean` bool
# helyét a `mean_type` vette át, és időközben megjelent a `unit_class` is. Nem
# találgatunk: a TypedDict deklarált kulcsaiból derül ki, mit fogad el ez a
# Home Assistant. Ismeretlen kulccsal az import a recorder szálán szállna el.
try:
    from homeassistant.components.recorder.models import StatisticMetaData

    _META_KEYS = frozenset(StatisticMetaData.__annotations__)
except ImportError:  # pragma: no cover - nagyon régi HA
    _META_KEYS = frozenset()

try:
    from homeassistant.components.recorder.models import StatisticMeanType

    _ARITHMETIC_MEAN: int | None = StatisticMeanType.ARITHMETIC
except ImportError:  # HA < 2025.5
    _ARITHMETIC_MEAN = None


@dataclass(slots=True)
class BackfillResult:
    """A pótlás eredménye — a szolgáltatás ezt adja vissza."""

    imported: int = 0
    skipped: int = 0
    hours: int = 0

    def as_dict(self) -> dict[str, int]:
        """Szolgáltatás-válasz."""
        return {
            "beirt_orak": self.imported,
            "kihagyott_orak": self.skipped,
            "szamolt_orak": self.hours,
        }


def _metadata(statistic_id: str) -> dict:
    """Statisztika-metaadat az aktuális Home Assistant sémája szerint."""
    metadata: dict = {
        "has_sum": False,
        "name": None,
        "source": "recorder",
        "statistic_id": statistic_id,
        "unit_of_measurement": CURRENCY_PER_KWH,
    }
    if "unit_class" in _META_KEYS:
        metadata["unit_class"] = None
    if _ARITHMETIC_MEAN is not None and "mean_type" in _META_KEYS:
        metadata["mean_type"] = _ARITHMETIC_MEAN
    else:
        metadata["has_mean"] = True
    return metadata


def _entity_ids(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, str]:
    """A nettó és bruttó szenzor entitás-azonosítója a nyilvántartásból.

    A neveket a felhasználó átírhatja, ezért a `unique_id`-ból indulunk ki.
    """
    registry = er.async_get(hass)
    found: dict[str, str] = {}
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        for key in ("netto_energiadij", "brutto_energiadij"):
            if entity.unique_id == f"{entry.entry_id}_{key}":
                found[key] = entity.entity_id
    return found


async def _hourly_net(
    coordinator, quarters: list[tuple[int, float]]
) -> dict[int, tuple[float, float, float]]:
    """Negyedórás EUR/MWh → órás nettó Ft/kWh (átlag, min, max), óra-kezdetenként.

    Az árfolyam napra pontos: minden negyedóra a saját napjának EKB
    árfolyamával számol, nem a maival.
    """
    tariff = tariff_from_options(coordinator.config_entry.options)
    buckets: dict[int, list[float]] = {}
    for timestamp, eur_mwh in quarters:
        moment = datetime.fromtimestamp(timestamp, timezone.utc)
        fx = await coordinator.async_fetch_fx_for_day(dt_util.as_local(moment).date())
        buckets.setdefault(timestamp - timestamp % 3600, []).append(
            tariff.net(eur_mwh, fx)
        )
    return {
        hour: (sum(values) / len(values), min(values), max(values))
        for hour, values in sorted(buckets.items())
    }


async def _existing_hours(
    hass: HomeAssistant, statistic_id: str, start: datetime, end: datetime
) -> set[datetime]:
    """Azok az órák, amelyekre már van statisztika."""
    rows = await get_instance(hass).async_add_executor_job(
        statistics_during_period,
        hass,
        start,
        end + timedelta(hours=1),
        {statistic_id},
        "hour",
        None,
        {"mean"},
    )
    return {
        datetime.fromtimestamp(row["start"], timezone.utc)
        for row in rows.get(statistic_id, [])
    }


async def async_backfill(
    hass: HomeAssistant,
    entry: ConfigEntry,
    start_day: date,
    end_day: date,
    overwrite: bool = False,
) -> BackfillResult:
    """A megadott napok hiányzó óráinak pótlása. Visszaadja, mi történt."""
    coordinator = entry.runtime_data
    result = BackfillResult()

    # Az energy-charts `end` napja nem esik bele a válaszba, ezért nyújtunk egyet.
    quarters = await coordinator.async_fetch_price_range(
        start_day, end_day + timedelta(days=1)
    )
    if not quarters:
        _LOGGER.debug(
            "Nincs publikált ár erre a tartományra: %s .. %s", start_day, end_day
        )
        return result

    hours = await _hourly_net(coordinator, quarters)

    # A még futó órát kihagyjuk: azt a Home Assistant magától lezárja a valódi
    # állapotokból, a fél óra adatunk csak rontana rajta.
    now = datetime.now(timezone.utc)
    current_hour = now.replace(minute=0, second=0, microsecond=0).timestamp()
    hours = {hour: values for hour, values in hours.items() if hour < current_hour}
    if not hours:
        return result
    result.hours = len(hours)

    entities = _entity_ids(hass, entry)
    if len(entities) != 2:
        _LOGGER.warning("Nincs meg mindkét ár-szenzor, a pótlás kimarad")
        return result

    tariff = tariff_from_options(entry.options)
    first = datetime.fromtimestamp(min(hours), timezone.utc)
    last = datetime.fromtimestamp(max(hours), timezone.utc)

    for key, factor in (
        ("netto_energiadij", 1.0),
        ("brutto_energiadij", tariff.vat_multiplier),
    ):
        statistic_id = entities[key]
        have: set[datetime] = set()
        if not overwrite:
            have = await _existing_hours(hass, statistic_id, first, last)

        rows = [
            {
                "start": moment,
                "mean": round(values[0] * factor, 2),
                "min": round(values[1] * factor, 2),
                "max": round(values[2] * factor, 2),
            }
            for hour, values in sorted(hours.items())
            if (moment := datetime.fromtimestamp(hour, timezone.utc)) not in have
        ]
        result.skipped += len(hours) - len(rows)
        if not rows:
            continue

        async_import_statistics(hass, _metadata(statistic_id), rows)
        result.imported += len(rows)
        _LOGGER.info("%s: %d óra statisztikája pótolva", statistic_id, len(rows))

    return result
