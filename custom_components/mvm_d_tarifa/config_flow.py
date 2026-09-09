"""Konfigurációs folyamat az MVM D tarifa integrációhoz."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
)

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
    DOMAIN,
)

TITLE = "MVM D tarifa"


def _number(
    minimum: float, maximum: float, step: float, unit: str | None = None
) -> NumberSelector:
    """Szám-mező. Mértékegység nélkül a kulcsot ki KELL hagyni: a selector
    sémája `str`-t vár, `None`-tól `vol.Invalid`-ot dob, amit a HA HTTP rétege
    néma 400 Bad Request-té alakít — az űrlap meg sem jelenik."""
    config = NumberSelectorConfig(
        min=minimum,
        max=maximum,
        step=step,
        mode=NumberSelectorMode.BOX,
    )
    if unit is not None:
        config["unit_of_measurement"] = unit
    return NumberSelector(config)


def _schema(defaults: dict[str, Any]) -> vol.Schema:
    """Űrlap a díjtételekhez, a megadott alapértelmezésekkel."""
    return vol.Schema(
        {
            vol.Required(
                CONF_TRANSMISSION_FEE,
                default=defaults.get(CONF_TRANSMISSION_FEE, DEFAULT_TRANSMISSION_FEE),
            ): _number(0.01, 100, 0.01, CURRENCY_PER_KWH),
            vol.Required(
                CONF_DISTRIBUTION_FEE,
                default=defaults.get(CONF_DISTRIBUTION_FEE, DEFAULT_DISTRIBUTION_FEE),
            ): _number(0.01, 100, 0.01, CURRENCY_PER_KWH),
            vol.Required(
                CONF_VAT_MULTIPLIER,
                default=defaults.get(CONF_VAT_MULTIPLIER, DEFAULT_VAT_MULTIPLIER),
            ): _number(1, 2, 0.01),
            vol.Required(
                CONF_MANUAL_FX,
                default=defaults.get(CONF_MANUAL_FX, DEFAULT_MANUAL_FX),
            ): _number(200, 800, 0.01, CURRENCY_HUF),
            vol.Required(
                CONF_A1_REFERENCE,
                default=defaults.get(CONF_A1_REFERENCE, DEFAULT_A1_REFERENCE),
            ): _number(0, 500, 0.1, CURRENCY_PER_KWH),
        }
    )


class DTarifaConfigFlow(ConfigFlow, domain=DOMAIN):
    """Egyetlen bejegyzés, benne a számla szerinti díjtételekkel."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Díjtételek bekérése telepítéskor."""
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()

        if user_input is not None:
            return self.async_create_entry(title=TITLE, data={}, options=user_input)

        return self.async_show_form(step_id="user", data_schema=_schema({}))

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: Any) -> DTarifaOptionsFlow:
        """A díjtételek utólag is módosíthatók."""
        return DTarifaOptionsFlow()


class DTarifaOptionsFlow(OptionsFlow):
    """Díjtételek módosítása a Beállítás gombbal."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Az űrlap újra, a jelenlegi értékekkel."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        return self.async_show_form(
            step_id="init", data_schema=_schema(dict(self.config_entry.options))
        )
