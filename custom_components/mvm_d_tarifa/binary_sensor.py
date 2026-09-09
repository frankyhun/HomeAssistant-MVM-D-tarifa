"""Bináris szenzor: olcsó-e most az áram."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import DTarifaConfigEntry
from .calc import current_eur_mwh
from .coordinator import DTarifaCoordinator
from .entity import DTarifaEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DTarifaConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Bináris szenzor létrehozása."""
    async_add_entities([CheapPriceBinarySensor(entry.runtime_data)])


class CheapPriceBinarySensor(DTarifaEntity, BinarySensorEntity):
    """`on`, ha az aktuális bruttó ár az A1 referenciaár alatt van."""

    _attr_icon = "mdi:piggy-bank"

    def __init__(self, coordinator: DTarifaCoordinator) -> None:
        """Bináris szenzor létrehozása."""
        super().__init__(coordinator, "olcso")

    @property
    def _eur_mwh(self) -> float | None:
        data = self.coordinator.data
        return current_eur_mwh(data.times, data.prices, dt_util.utcnow().timestamp())

    @property
    def available(self) -> bool:
        """Érvényes aktuális ár nélkül nem érhető el."""
        return super().available and self._eur_mwh is not None

    @property
    def is_on(self) -> bool | None:
        """Olcsóbb-e a mostani negyedóra a fix A1 árnál."""
        eur_mwh = self._eur_mwh
        if eur_mwh is None:
            return None
        tariff = self.tariff
        return round(tariff.gross(eur_mwh, self.fx), 2) < tariff.a1_reference

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Az összehasonlítás két oldala."""
        eur_mwh = self._eur_mwh
        tariff = self.tariff
        return {
            "kuszob": tariff.a1_reference,
            "brutto_ar": round(tariff.gross(eur_mwh, self.fx), 2)
            if eur_mwh is not None
            else None,
        }
