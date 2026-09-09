"""Füst-teszt: config flow, entitások, díjtétel-módosítás.

Futtatás a tároló gyökeréből:

    pip install pytest-homeassistant-custom-component
    pytest
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

DOMAIN = "mvm_d_tarifa"

PRICE_URL = "https://api.energy-charts.info/price?bzn=HU"
FX_URL = "https://api.frankfurter.dev/v1/latest?from=EUR&to=HUF"

# 400 Ft/EUR mellett az aktuális negyedóra 54 EUR/MWh ára:
#   nettó  = 54 * 400 / 1000 + 3.39 + 20.01 = 45.00 Ft/kWh
#   bruttó = 45.00 * 1.27                   = 57.15 Ft/kWh
USER_INPUT = {
    "atviteli_forgalmi_dij": 3.39,
    "elosztoi_forgalmi_dij": 20.01,
    "afa_szorzo": 1.27,
    "arfolyam_kezi": 395.0,
    "a1_referencia": 70.1,
}


@pytest.fixture
def price_payload() -> dict:
    """Nyolc negyedóra úgy időzítve, hogy a mostani az ötödik (54 EUR/MWh)."""
    now = dt_util.utcnow()
    quarter = now.replace(minute=now.minute // 15 * 15, second=0, microsecond=0)
    start = quarter - timedelta(hours=1)
    return {
        "unix_seconds": [
            (start + timedelta(minutes=15 * i)).timestamp() for i in range(8)
        ],
        "price": [50.0 + i for i in range(8)],
        "unit": "EUR / MWh",
        "license_info": "CC BY 4.0",
    }


def _states_by_key(hass: HomeAssistant, entry_id: str) -> dict[str, str]:
    """Entitás-állapotok a unique_id végződése szerint — a név nyelvfüggő."""
    registry = er.async_get(hass)
    return {
        entity.unique_id.removeprefix(f"{entry_id}_"): hass.states.get(
            entity.entity_id
        ).state
        for entity in er.async_entries_for_config_entry(registry, entry_id)
    }


async def test_flow_and_entities(hass: HomeAssistant, aioclient_mock, price_payload):
    """A folyamat űrlapot ad, a bejegyzés betölt, az árak stimmelnek."""
    aioclient_mock.get(PRICE_URL, json=price_payload)
    aioclient_mock.get(FX_URL, json={"rates": {"HUF": 400.0}})

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert entry.state is ConfigEntryState.LOADED

    states = _states_by_key(hass, entry.entry_id)
    assert states["netto_energiadij"] == "45.0"
    assert states["brutto_energiadij"] == "57.15"
    assert states["hupx_arak"] == "8"
    assert states["eur_huf_arfolyam"] == "400.0"
    assert states["olcso"] == "on"  # 57.15 < 70.1
    assert states["afa_szorzo"] == "1.27"


async def test_fee_change_recalculates(
    hass: HomeAssistant, aioclient_mock, price_payload
):
    """A number entitás átírása azonnal új árat ad, újratöltés nélkül."""
    aioclient_mock.get(PRICE_URL, json=price_payload)
    aioclient_mock.get(FX_URL, json={"rates": {"HUF": 400.0}})

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    await hass.async_block_till_done()

    entry = hass.config_entries.async_entries(DOMAIN)[0]
    registry = er.async_get(hass)
    vat = next(
        entity
        for entity in er.async_entries_for_config_entry(registry, entry.entry_id)
        if entity.unique_id.endswith("_afa_szorzo")
    )

    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": vat.entity_id, "value": 2.0},
        blocking=True,
    )
    await hass.async_block_till_done()

    states = _states_by_key(hass, entry.entry_id)
    assert states["brutto_energiadij"] == "90.0"  # 45.00 * 2
    assert states["olcso"] == "off"  # 90.0 > 70.1
