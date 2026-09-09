"""Közös entitás-alaposztály."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .calc import Tariff, tariff_from_options
from .const import DOMAIN
from .coordinator import DTarifaCoordinator


class DTarifaEntity(CoordinatorEntity[DTarifaCoordinator]):
    """Minden entitás ugyanahhoz a szolgáltatás-eszközhöz tartozik."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: DTarifaCoordinator, key: str) -> None:
        """Entitás létrehozása."""
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_translation_key = key
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="MVM D tarifa",
            manufacturer="MVM Next",
            model="D (dinamikus) árszabás",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url="https://api.energy-charts.info/price?bzn=HU",
        )

    @property
    def tariff(self) -> Tariff:
        """A bejegyzésben tárolt díjtételek."""
        return tariff_from_options(self.coordinator.config_entry.options)

    @property
    def fx(self) -> float:
        """Az EKB árfolyam, vagy a kézi tartalék, ha az API nem elérhető."""
        rate = self.coordinator.data.fx
        if rate is not None and rate > 0:
            return rate
        return self.tariff.manual_fx
