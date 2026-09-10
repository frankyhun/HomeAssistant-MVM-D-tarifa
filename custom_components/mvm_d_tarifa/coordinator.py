"""Adatlekérés az energy-charts és a frankfurter API-ból."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import logging
from typing import Any

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN,
    FX_DAY_URL,
    FX_TTL,
    FX_URL,
    PRICE_RANGE_URL,
    PRICE_TTL,
    REQUEST_TIMEOUT,
    UPDATE_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class DTarifaData:
    """A két forrás legutóbbi érvényes válasza."""

    times: list[float]
    prices: list[float | None]
    unit: str | None
    license_info: str | None
    prices_updated: datetime | None
    fx: float | None
    fx_updated: datetime | None


class DTarifaCoordinator(DataUpdateCoordinator[DTarifaData]):
    """Percenként fut, de hálózatra csak lejárt gyorsítótár esetén megy."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Koordinátor létrehozása."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
            config_entry=entry,
        )
        self._session = async_get_clientsession(hass)
        self._times: list[float] = []
        self._prices: list[float | None] = []
        self._unit: str | None = None
        self._license_info: str | None = None
        self._prices_ts: float = 0.0
        self._prices_updated: datetime | None = None
        self._fx: float | None = None
        self._fx_ts: float = 0.0
        self._fx_updated: datetime | None = None
        self._fx_by_day: dict[date, float] = {}

    async def _async_update_data(self) -> DTarifaData:
        now = dt_util.utcnow().timestamp()

        if now - self._prices_ts >= PRICE_TTL:
            try:
                times, prices, unit, license_info = await self._fetch_prices()
            except (aiohttp.ClientError, TimeoutError) as err:
                if not self._times:
                    raise UpdateFailed(f"Nem érhető el az ár-API: {err}") from err
                _LOGGER.debug("Ár-lekérés sikertelen, marad a gyorsítótár: %s", err)
            except ValueError as err:
                if not self._times:
                    raise UpdateFailed(f"Váratlan ár-válasz: {err}") from err
                _LOGGER.warning("Váratlan ár-válasz, marad a gyorsítótár: %s", err)
            else:
                self._times = times
                self._prices = prices
                self._unit = unit
                self._license_info = license_info
                self._prices_ts = now
                self._prices_updated = dt_util.utcnow()

        # Az árfolyam kiesése nem végzetes: a szenzorok ilyenkor a kézi
        # tartalék árfolyammal számolnak tovább.
        if now - self._fx_ts >= FX_TTL:
            try:
                fx = await self._fetch_fx()
            except (aiohttp.ClientError, TimeoutError, ValueError) as err:
                _LOGGER.debug("Árfolyam-lekérés sikertelen: %s", err)
            else:
                self._fx = fx
                self._fx_ts = now
                self._fx_updated = dt_util.utcnow()

        return DTarifaData(
            times=self._times,
            prices=self._prices,
            unit=self._unit,
            license_info=self._license_info,
            prices_updated=self._prices_updated,
            fx=self._fx,
            fx_updated=self._fx_updated,
        )

    async def _fetch_prices(
        self,
    ) -> tuple[list[float], list[float | None], str | None, str | None]:
        """Negyedórás day-ahead árak a HU zónára, EUR/MWh-ban.

        Mindig kifejezett dátumtartományt kérünk. A paraméter nélküli lekérés
        egyetlen napot ad vissza, és a „mai nap” alatt az UTC szerinti mai
        napot érti, a naphatárt viszont helyi idő szerint húzza meg: éjfél és
        02:00 (télen 01:00) helyi idő között ezért még a tegnapi napot küldte,
        ami épp helyi éjfélkor véget ér — az ár így elévültnek látszott, az
        entitások pedig `unknown` állapotba kerültek.

        A dátumot az API helyi (közép-európai) idő szerint értelmezi, tehát a
        `start=ma` pontosan a helyi éjféltől indul. Az `end` napja is beleesik,
        így a másnapi árak is megjönnek, amint publikálják őket.

        404 esetén a tartományra nincs publikált ár. Ezt nem kezeljük külön: a
        hívó a gyorsítótárat tartja meg, az entitások pedig az elévülés miatt
        lesznek elérhetetlenek — rossz árat nem mutatunk.
        """
        today = dt_util.now().date()
        payload = await self._get_json(
            PRICE_RANGE_URL.format(start=today, end=today + timedelta(days=1))
        )
        times = payload.get("unix_seconds")
        prices = payload.get("price")
        if not isinstance(times, list) or not isinstance(prices, list):
            raise ValueError("hiányzó unix_seconds vagy price mező")
        if not times or len(times) != len(prices):
            raise ValueError(
                f"eltérő tömbhossz: {len(times)} időbélyeg, {len(prices)} ár"
            )
        return (
            [float(value) for value in times],
            [None if price is None else float(price) for price in prices],
            payload.get("unit"),
            payload.get("license_info"),
        )

    async def async_fetch_price_range(
        self, start: date, end: date
    ) -> list[tuple[int, float]]:
        """(unix_seconds, EUR/MWh) párok egy időszakra, a null árak nélkül.

        A statisztika-pótlás használja. Az `end` napja is beleesik, ezért a
        hívó egy nappal túlnyúlva kér. 404 esetén a tartományra egyszerűen
        nincs publikált ár — üres listát adunk vissza.
        """
        try:
            payload = await self._get_json(
                PRICE_RANGE_URL.format(start=start.isoformat(), end=end.isoformat())
            )
        except aiohttp.ClientResponseError as err:
            if err.status == 404:
                return []
            raise
        times = payload.get("unix_seconds") or []
        prices = payload.get("price") or []
        return [
            (int(time), float(price))
            for time, price in zip(times, prices, strict=False)
            if price is not None
        ]

    async def async_fetch_fx_for_day(self, day: date) -> float:
        """Az adott napon érvényes EKB EUR/HUF árfolyam, gyorsítótárazva."""
        if day not in self._fx_by_day:
            payload = await self._get_json(FX_DAY_URL.format(day=day.isoformat()))
            rate = payload.get("rates", {}).get("HUF")
            if not isinstance(rate, (int, float)) or rate <= 0:
                raise ValueError(f"nincs EUR/HUF árfolyam erre a napra: {day}")
            self._fx_by_day[day] = float(rate)
        return self._fx_by_day[day]

    async def _fetch_fx(self) -> float:
        """EKB EUR/HUF referencia-árfolyam."""
        payload = await self._get_json(FX_URL)
        rate = payload.get("rates", {}).get("HUF")
        if not isinstance(rate, (int, float)) or rate <= 0:
            raise ValueError(f"érvénytelen EUR/HUF árfolyam: {rate!r}")
        return float(rate)

    async def _get_json(self, url: str) -> dict[str, Any]:
        async with (
            asyncio.timeout(REQUEST_TIMEOUT),
            self._session.get(url) as response,
        ):
            response.raise_for_status()
            # Az energy-charts néha text/plain típussal küldi a JSON-t.
            payload = await response.json(content_type=None)
        if not isinstance(payload, dict):
            raise ValueError(f"nem objektum a válasz: {type(payload).__name__}")
        return payload
