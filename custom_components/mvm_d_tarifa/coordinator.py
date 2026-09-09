"""Adatlekérés az energy-charts és a frankfurter API-ból."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
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
    FX_TTL,
    FX_URL,
    PRICE_TTL,
    PRICE_URL,
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
        """Negyedórás day-ahead árak a HU zónára, EUR/MWh-ban."""
        try:
            payload = await self._get_json(PRICE_URL)
        except aiohttp.ClientResponseError as err:
            if err.status != 404:
                raise
            # Az API 404-et ad, ha a kért időszakra nincs publikált ár. Az
            # alapértelmezett időszakot nem dokumentálják, ezért ilyenkor
            # kifejezetten a mai és a másnapi napra kérdezünk rá. Ha erre is
            # 404 jön, tényleg nincs adat: a hívó ilyenkor a gyorsítótárat
            # tartja meg, az entitások pedig az elévülés miatt lesznek
            # elérhetetlenek — rossz árat nem mutatunk.
            today = dt_util.now().date()
            payload = await self._get_json(
                f"{PRICE_URL}&start={today}&end={today + timedelta(days=2)}"
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
