"""Közös entitás-alaposztály."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .calc import Tariff
from .const import (
    CONF_A1_REFERENCE,
    CONF_DISTRIBUTION_FEE,
    CONF_MANUAL_FX,
    CONF_TRANSMISSION_FEE,
    CONF_VAT_MULTIPLIER,
    DEFAULT_A1_REFERENCE,
    DEFAULT_DISTRIBUTION_FEE,
    DEFAULT_MANUAL_FX,
    DEFAULT_TRANSMISSION_FEE,
    DEFAULT_VAT_MULTIPLIER,
    DOMAIN,
)
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
        options = self.coordinator.config_entry.options
        return Tariff(
            transmission_fee=float(
                options.get(CONF_TRANSMISSION_FEE, DEFAULT_TRANSMISSION_FEE)
            ),
            distribution_fee=float(
                options.get(CONF_DISTRIBUTION_FEE, DEFAULT_DISTRIBUTION_FEE)
            ),
            vat_multiplier=float(
                options.get(CONF_VAT_MULTIPLIER, DEFAULT_VAT_MULTIPLIER)
            ),
            manual_fx=float(options.get(CONF_MANUAL_FX, DEFAULT_MANUAL_FX)),
            a1_reference=float(options.get(CONF_A1_REFERENCE, DEFAULT_A1_REFERENCE)),
        )

    @property
    def fx(self) -> float:
        """Az EKB árfolyam, vagy a kézi tartalék, ha az API nem elérhető."""
        rate = self.coordinator.data.fx
        if rate is not None and rate > 0:
            return rate
        return self.tariff.manual_fx
