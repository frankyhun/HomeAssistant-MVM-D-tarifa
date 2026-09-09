"""A díjtételek entitásként, a korábbi input_number helperek helyett."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.number import (
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import DTarifaConfigEntry
from .const import (
    CONF_A1_REFERENCE,
    CONF_DISTRIBUTION_FEE,
    CONF_MANUAL_FX,
    CONF_TRANSMISSION_FEE,
    CONF_VAT_MULTIPLIER,
    CURRENCY_HUF,
    CURRENCY_PER_KWH,
    DEFAULT_A1_REFERENCE,
    DEFAULT_DISTRIBUTION_FEE,
    DEFAULT_MANUAL_FX,
    DEFAULT_TRANSMISSION_FEE,
    DEFAULT_VAT_MULTIPLIER,
)
from .coordinator import DTarifaCoordinator
from .entity import DTarifaEntity


@dataclass(frozen=True, kw_only=True)
class DTarifaNumberDescription(NumberEntityDescription):
    """Egy díjtétel leírása, alapértelmezett értékkel."""

    default: float


NUMBERS: tuple[DTarifaNumberDescription, ...] = (
    DTarifaNumberDescription(
        key=CONF_TRANSMISSION_FEE,
        translation_key=CONF_TRANSMISSION_FEE,
        icon="mdi:cash",
        native_min_value=0.01,
        native_max_value=100,
        native_step=0.01,
        native_unit_of_measurement=CURRENCY_PER_KWH,
        mode=NumberMode.BOX,
        default=DEFAULT_TRANSMISSION_FEE,
    ),
    DTarifaNumberDescription(
        key=CONF_DISTRIBUTION_FEE,
        translation_key=CONF_DISTRIBUTION_FEE,
        icon="mdi:transmission-tower",
        native_min_value=0.01,
        native_max_value=100,
        native_step=0.01,
        native_unit_of_measurement=CURRENCY_PER_KWH,
        mode=NumberMode.BOX,
        default=DEFAULT_DISTRIBUTION_FEE,
    ),
    DTarifaNumberDescription(
        key=CONF_VAT_MULTIPLIER,
        translation_key=CONF_VAT_MULTIPLIER,
        icon="mdi:percent",
        native_min_value=1,
        native_max_value=2,
        native_step=0.01,
        mode=NumberMode.BOX,
        default=DEFAULT_VAT_MULTIPLIER,
    ),
    DTarifaNumberDescription(
        key=CONF_MANUAL_FX,
        translation_key=CONF_MANUAL_FX,
        icon="mdi:currency-eur",
        native_min_value=200,
        native_max_value=800,
        native_step=0.01,
        native_unit_of_measurement=CURRENCY_HUF,
        mode=NumberMode.BOX,
        default=DEFAULT_MANUAL_FX,
    ),
    DTarifaNumberDescription(
        key=CONF_A1_REFERENCE,
        translation_key=CONF_A1_REFERENCE,
        icon="mdi:scale-balance",
        native_min_value=0,
        native_max_value=500,
        native_step=0.1,
        native_unit_of_measurement=CURRENCY_PER_KWH,
        mode=NumberMode.BOX,
        default=DEFAULT_A1_REFERENCE,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DTarifaConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Díjtétel-entitások létrehozása."""
    coordinator = entry.runtime_data
    async_add_entities(
        DTarifaNumber(coordinator, description) for description in NUMBERS
    )


class DTarifaNumber(DTarifaEntity, NumberEntity):
    """Egy díjtétel. Az érték a bejegyzés beállításai közt tárolódik."""

    entity_description: DTarifaNumberDescription

    def __init__(
        self, coordinator: DTarifaCoordinator, description: DTarifaNumberDescription
    ) -> None:
        """Entitás létrehozása."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        """Beállítás, nem mért adat: az API kiesésétől függetlenül elérhető."""
        return True

    @property
    def native_value(self) -> float:
        """A jelenleg érvényes érték."""
        options = self.coordinator.config_entry.options
        return float(
            options.get(self.entity_description.key, self.entity_description.default)
        )

    async def async_set_native_value(self, value: float) -> None:
        """Új érték mentése; a számított entitások azonnal újraszámolnak."""
        entry = self.coordinator.config_entry
        self.hass.config_entries.async_update_entry(
            entry, options={**entry.options, self.entity_description.key: value}
        )
