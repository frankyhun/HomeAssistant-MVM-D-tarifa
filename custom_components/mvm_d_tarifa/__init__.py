"""MVM D (dinamikus) tarifa integráció."""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .backfill import async_backfill
from .calc import current_eur_mwh
from .const import (
    AUTO_BACKFILL_INTERVAL_HOURS,
    CONF_AUTO_BACKFILL,
    DEFAULT_AUTO_BACKFILL,
    DOMAIN,
    SERVICE_BACKFILL,
)
from .coordinator import DTarifaCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.NUMBER,
    Platform.SENSOR,
]

DTarifaConfigEntry = ConfigEntry[DTarifaCoordinator]

BACKFILL_SCHEMA = vol.Schema(
    {
        vol.Optional("start"): cv.date,
        vol.Optional("end"): cv.date,
        vol.Optional("overwrite", default=False): cv.boolean,
    }
)


async def async_setup_entry(hass: HomeAssistant, entry: DTarifaConfigEntry) -> bool:
    """Beállítás a felhasználói felületen létrehozott bejegyzésből."""
    coordinator = DTarifaCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_update_options))

    _async_register_service(hass)
    _async_setup_auto_backfill(hass, entry)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: DTarifaConfigEntry) -> bool:
    """Bejegyzés eltávolítása."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded and not hass.config_entries.async_loaded_entries(DOMAIN):
        hass.services.async_remove(DOMAIN, SERVICE_BACKFILL)
    return unloaded


async def async_update_options(hass: HomeAssistant, entry: DTarifaConfigEntry) -> None:
    """Díjtétel módosult: elég újraszámolni, újratölteni nem kell.

    Az entitások minden számításkor a bejegyzés beállításaiból olvassák a
    díjtételeket, ezért csak új állapotot kell íratni velük.
    """
    entry.runtime_data.async_update_listeners()


def _async_register_service(hass: HomeAssistant) -> None:
    """A `mvm_d_tarifa.backfill` szolgáltatás regisztrálása."""
    if hass.services.has_service(DOMAIN, SERVICE_BACKFILL):
        return

    async def _handle_backfill(call: ServiceCall) -> ServiceResponse:
        """Statisztika-pótlás kézi indítása."""
        # A recorder puha függőség: nélküle nincs hova írni a statisztikát.
        if "recorder" not in hass.config.components:
            raise ServiceValidationError(
                "A statisztika-pótláshoz a recorder integráció kell, "
                "ez a rendszeren nincs betöltve."
            )
        entries = hass.config_entries.async_loaded_entries(DOMAIN)
        if not entries:
            return {"beirt_orak": 0, "kihagyott_orak": 0, "szamolt_orak": 0}

        today = dt_util.now().date()
        start_day: date = call.data.get("start", today - timedelta(days=1))
        end_day: date = call.data.get("end", today)
        if end_day < start_day:
            raise vol.Invalid("Az `end` nem lehet korábbi, mint a `start`.")

        result = await async_backfill(
            hass,
            entries[0],
            start_day,
            end_day,
            call.data["overwrite"],
            reason="kézi indítás",
        )
        return result.as_dict()

    hass.services.async_register(
        DOMAIN,
        SERVICE_BACKFILL,
        _handle_backfill,
        schema=BACKFILL_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )


def _async_setup_auto_backfill(hass: HomeAssistant, entry: DTarifaConfigEntry) -> None:
    """Automatikus pótlás: kiesés után, plusz néhány óránként hálóként.

    A kézi szolgáltatás ettől függetlenül mindig hívható; ez a rész a
    beállításokban kikapcsolható.
    """
    coordinator = entry.runtime_data
    running = asyncio.Lock()
    had_price = _has_current_price(coordinator)

    async def _run(reason: str) -> None:
        """Automatikus pótlás. Minden kimenetet naplóz, kivételt nem enged ki."""
        if not entry.options.get(CONF_AUTO_BACKFILL, DEFAULT_AUTO_BACKFILL):
            _LOGGER.debug(
                "Statisztika-pótlás (%s) kihagyva: az automatikus pótlás ki van "
                "kapcsolva",
                reason,
            )
            return
        if "recorder" not in hass.config.components:
            _LOGGER.warning(
                "Statisztika-pótlás (%s) kihagyva: a recorder integráció nincs "
                "betöltve",
                reason,
            )
            return
        if running.locked():
            _LOGGER.debug(
                "Statisztika-pótlás (%s) kihagyva: már fut egy pótlás", reason
            )
            return
        async with running:
            today = dt_util.now().date()
            try:
                await async_backfill(
                    hass, entry, today - timedelta(days=1), today, reason=reason
                )
            except Exception:  # noqa: BLE001 - a háttérfutás ne dőljön be
                _LOGGER.exception(
                    "Statisztika-pótlás (%s) hibára futott", reason
                )

    @callback
    def _on_coordinator_update() -> None:
        """Kiesés után, az első érvényes áron indítunk pótlást."""
        nonlocal had_price
        has_price = _has_current_price(coordinator)
        if has_price and not had_price:
            entry.async_create_background_task(
                hass, _run("adat visszatért"), f"{DOMAIN}_backfill"
            )
        had_price = has_price

    # Induláskor azonnal: ha már van érvényes ár a mai napra, a HA leállása
    # alatt keletkezett lyukat rögtön betöltjük.
    if _has_current_price(coordinator):
        entry.async_create_background_task(hass, _run("indulás"), f"{DOMAIN}_backfill")
    else:
        _LOGGER.debug(
            "Statisztika-pótlás (indulás) kihagyva: nincs érvényes ár a mai napra, "
            "az adat visszatérésekor újrapróbáljuk"
        )

    entry.async_on_unload(coordinator.async_add_listener(_on_coordinator_update))
    entry.async_on_unload(
        async_track_time_interval(
            hass,
            lambda _now: entry.async_create_background_task(
                hass, _run("időzített ellenőrzés"), f"{DOMAIN}_backfill"
            ),
            timedelta(hours=AUTO_BACKFILL_INTERVAL_HOURS),
        )
    )


def _has_current_price(coordinator: DTarifaCoordinator) -> bool:
    """Van-e most érvényes, friss ár — ugyanaz a feltétel, mint a szenzoroknál."""
    data = coordinator.data
    if data is None:
        return False
    return (
        current_eur_mwh(data.times, data.prices, dt_util.utcnow().timestamp())
        is not None
    )
