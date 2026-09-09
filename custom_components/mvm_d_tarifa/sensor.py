"""Szenzorok az MVM D tarifához."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from homeassistant.components.sensor import (
    SensorEntity,
    SensorStateClass,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import DTarifaConfigEntry
from .calc import current_eur_mwh, current_index, day_slice
from .const import CURRENCY_HUF, CURRENCY_PER_KWH
from .coordinator import DTarifaCoordinator
from .entity import DTarifaEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DTarifaConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Szenzorok létrehozása."""
    coordinator = entry.runtime_data
    async_add_entities(
        [
            NetPriceSensor(coordinator),
            GrossPriceSensor(coordinator),
            DailyAverageSensor(coordinator),
            HupxRawSensor(coordinator),
            ExchangeRateSensor(coordinator),
        ]
    )


class _PriceSensorBase(DTarifaEntity, SensorEntity):
    """Az aktuális negyedóra árából számolt szenzorok közös őse."""

    _attr_native_unit_of_measurement = CURRENCY_PER_KWH
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 2

    @property
    def _eur_mwh(self) -> float | None:
        data = self.coordinator.data
        return current_eur_mwh(data.times, data.prices, dt_util.utcnow().timestamp())

    @property
    def available(self) -> bool:
        """Beragadt, hiányzó vagy publikálatlan ár esetén nem érhető el."""
        return super().available and self._eur_mwh is not None


class NetPriceSensor(_PriceSensorBase):
    """Az aktuális negyedóra nettó energiadíja."""


    def __init__(self, coordinator: DTarifaCoordinator) -> None:
        """Szenzor létrehozása."""
        super().__init__(coordinator, "netto_energiadij")

    @property
    def native_value(self) -> float | None:
        """Nettó ár Ft/kWh-ban."""
        eur_mwh = self._eur_mwh
        if eur_mwh is None:
            return None
        return round(self.tariff.net(eur_mwh, self.fx), 2)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """A számítás bemenetei — ellenőrzéshez."""
        data = self.coordinator.data
        return {
            "hupx_eur_mwh": self._eur_mwh,
            "arfolyam": round(self.fx, 2),
            "negyedora_index": current_index(
                data.times, dt_util.utcnow().timestamp()
            ),
            "atviteli_forgalmi_dij": self.tariff.transmission_fee,
            "elosztoi_forgalmi_dij": self.tariff.distribution_fee,
            "szamitva": dt_util.now().isoformat(),
        }


class GrossPriceSensor(_PriceSensorBase):
    """Az aktuális negyedóra bruttó energiadíja."""


    def __init__(self, coordinator: DTarifaCoordinator) -> None:
        """Szenzor létrehozása."""
        super().__init__(coordinator, "brutto_energiadij")

    @property
    def native_value(self) -> float | None:
        """Bruttó ár Ft/kWh-ban."""
        eur_mwh = self._eur_mwh
        if eur_mwh is None:
            return None
        return round(self.tariff.gross(eur_mwh, self.fx), 2)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Az alkalmazott áfa szorzó."""
        return {"afa_szorzo": self.tariff.vat_multiplier}


class DailyAverageSensor(DTarifaEntity, SensorEntity):
    """A mai naptári nap átlagos bruttó ára."""

    _attr_native_unit_of_measurement = CURRENCY_PER_KWH
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator: DTarifaCoordinator) -> None:
        """Szenzor létrehozása."""
        super().__init__(coordinator, "mai_atlag_brutto")

    @property
    def _today(self) -> list[tuple[float, float]]:
        """A mai nap (időbélyeg, EUR/MWh) párjai, null árak nélkül."""
        data = self.coordinator.data
        start = dt_util.start_of_local_day()
        # NEM start + 86400: DST-kor a helyi nap 23 vagy 25 órából áll.
        end = dt_util.start_of_local_day(dt_util.now() + timedelta(days=1))
        return day_slice(data.times, data.prices, start.timestamp(), end.timestamp())

    @property
    def available(self) -> bool:
        """Üres szelet esetén nem érhető el — nem nullával osztunk."""
        return super().available and bool(self._today)

    @property
    def native_value(self) -> float | None:
        """A mai átlagár Ft/kWh-ban."""
        today = self._today
        if not today:
            return None
        average = sum(price for _, price in today) / len(today)
        return round(self.tariff.gross(average, self.fx), 2)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """A mai szélsőértékek és azok időpontja."""
        today = self._today
        if not today:
            return {}
        cheapest = min(today, key=lambda item: item[1])
        priciest = max(today, key=lambda item: item[1])
        return {
            "minimum": round(self.tariff.gross(cheapest[1], self.fx), 2),
            "maximum": round(self.tariff.gross(priciest[1], self.fx), 2),
            "legolcsobb_idopont": _local_hhmm(cheapest[0]),
            "legdragabb_idopont": _local_hhmm(priciest[0]),
            "negyedorak_szama": len(today),
        }


class HupxRawSensor(DTarifaEntity, SensorEntity):
    """A nyers ár-válasz: hány negyedóra van publikálva."""

    _attr_native_unit_of_measurement = "db"
    # A két hosszú tömb nem kerül az adatbázisba; a license_info igen, mert azt
    # a CC BY 4.0 forrásmegjelölés miatt meg kell őrizni.
    _unrecorded_attributes = frozenset({"unix_seconds", "price"})

    def __init__(self, coordinator: DTarifaCoordinator) -> None:
        """Szenzor létrehozása."""
        super().__init__(coordinator, "hupx_arak")

    @property
    def native_value(self) -> int | None:
        """96 (csak a mai nap) vagy 192 (a másnapiakkal együtt)."""
        return len(self.coordinator.data.prices) or None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """A nyers válasz mezői, köztük a kötelező forrásmegjelölés."""
        data = self.coordinator.data
        return {
            "unix_seconds": data.times,
            "price": data.prices,
            "unit": data.unit,
            "license_info": data.license_info,
            "frissitve": data.prices_updated.isoformat()
            if data.prices_updated
            else None,
        }


class ExchangeRateSensor(DTarifaEntity, SensorEntity):
    """Az EKB EUR/HUF referencia-árfolyam."""

    _attr_native_unit_of_measurement = CURRENCY_HUF
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator: DTarifaCoordinator) -> None:
        """Szenzor létrehozása."""
        super().__init__(coordinator, "eur_huf_arfolyam")

    @property
    def native_value(self) -> float | None:
        """Az utoljára lekért árfolyam."""
        return self.coordinator.data.fx

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """A tényleg használt árfolyam és annak forrása."""
        api_rate = self.coordinator.data.fx
        return {
            "hasznalt_arfolyam": round(self.fx, 2),
            "forras": "EKB" if api_rate else "kezi",
        }


def _local_hhmm(timestamp: float) -> str:
    """Unix időbélyeg óra:perc alakban, helyi idő szerint."""
    return dt_util.as_local(dt_util.utc_from_timestamp(timestamp)).strftime("%H:%M")
