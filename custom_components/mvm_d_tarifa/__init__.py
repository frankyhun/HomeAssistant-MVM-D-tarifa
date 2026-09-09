"""MVM D (dinamikus) tarifa integráció."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .coordinator import DTarifaCoordinator

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.NUMBER,
    Platform.SENSOR,
]

DTarifaConfigEntry = ConfigEntry[DTarifaCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: DTarifaConfigEntry) -> bool:
    """Beállítás a felhasználói felületen létrehozott bejegyzésből."""
    coordinator = DTarifaCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_update_options))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: DTarifaConfigEntry) -> bool:
    """Bejegyzés eltávolítása."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_update_options(hass: HomeAssistant, entry: DTarifaConfigEntry) -> None:
    """Díjtétel módosult: elég újraszámolni, újratölteni nem kell.

    Az entitások minden számításkor a bejegyzés beállításaiból olvassák a
    díjtételeket, ezért csak új állapotot kell íratni velük.
    """
    entry.runtime_data.async_update_listeners()
