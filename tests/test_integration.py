"""Füst-teszt: config flow, entitások, díjtétel-módosítás.

Futtatás a tároló gyökeréből:

    pip install pytest-homeassistant-custom-component
    pytest
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from homeassistant.components.logbook import EVENT_LOGBOOK_ENTRY
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import async_capture_events
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)
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


async def test_flow_and_entities(
    hass: HomeAssistant, enable_custom_integrations, aioclient_mock, price_payload
):
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
    hass: HomeAssistant, enable_custom_integrations, aioclient_mock, price_payload
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


async def test_price_request_uses_local_date_after_midnight(
    hass: HomeAssistant, enable_custom_integrations, aioclient_mock, freezer
):
    """Éjfél és 02:00 helyi idő között is a mai helyi napra kérdezünk.

    Az energy-charts a paraméter nélküli lekérésre az UTC szerinti mai napot
    adja vissza, a naphatárt viszont helyi idő szerint húzza meg: 22:00 UTC
    után még a tegnapi, helyi éjfélkor véget érő napot küldte, amitől az
    árszenzorok elévülés miatt `unknown`-ba estek. A dátumot az API helyi idő
    szerint értelmezi, ezért kifejezetten a helyi mai napot kérjük.

    A mock csak a dátumos URL-re felel: ha a lekérés visszaesne a paraméter
    nélküli alakra, a teszt illesztési hibával bukna.
    """
    await hass.config.async_set_time_zone("Europe/Budapest")
    # 2026-09-09 22:30 UTC = 2026-09-10 00:30 helyi idő — az UTC és a helyi
    # dátum ilyenkor eltér.
    freezer.move_to("2026-09-09 22:30:00+00:00")
    quarter = dt_util.utcnow()
    price_url = f"{PRICE_URL}&start=2026-09-10&end=2026-09-11"
    aioclient_mock.get(
        price_url,
        json={
            "unix_seconds": [
                (quarter + timedelta(minutes=15 * i)).timestamp() for i in range(4)
            ],
            "price": [54.0, 55.0, 56.0, 57.0],
            "unit": "EUR / MWh",
            "license_info": "CC BY 4.0",
        },
    )
    aioclient_mock.get(FX_URL, json={"rates": {"HUF": 400.0}})

    entry = await _setup_entry(hass)
    assert entry.state is ConfigEntryState.LOADED

    assert [
        str(url)
        for _, url, _, _ in aioclient_mock.mock_calls
        if url.host == "api.energy-charts.info"
    ] == [price_url]

    states = _states_by_key(hass, entry.entry_id)
    assert states["brutto_energiadij"] == "57.15"
    assert states["mai_atlag_brutto"] != "unavailable"


def _mock_backfill_apis(aioclient_mock, price_payload, hour):
    """A pótlás dátumos ár-lekérdezése és napi árfolyama.

    ELŐBB kell regisztrálni, mint az általános ár-URL-t: a mock az első olyan
    bejegyzést használja, amelynek a query paraméterei megvannak a kérésben, és
    a `bzn=HU` önmagában a dátumos kérésre is illeszkedne.
    """
    yesterday = dt_util.now().date() - timedelta(days=1)
    aioclient_mock.get(
        f"{PRICE_URL}&start={yesterday}&end={yesterday + timedelta(days=2)}",
        json={
            "unix_seconds": [
                (hour + timedelta(minutes=15 * i)).timestamp() for i in range(4)
            ],
            "price": [50.0, 54.0, 58.0, 62.0],
            "unit": "EUR / MWh",
            "license_info": "CC BY 4.0",
        },
    )
    aioclient_mock.get(
        f"https://api.frankfurter.dev/v1/{dt_util.as_local(hour).date()}"
        "?from=EUR&to=HUF",
        json={"rates": {"HUF": 400.0}},
    )
    aioclient_mock.get(PRICE_URL, json=price_payload)
    aioclient_mock.get(FX_URL, json={"rates": {"HUF": 400.0}})


async def _setup_entry(hass, user_input=None):
    """Bejegyzés létrehozása a folyamaton keresztül."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input or USER_INPUT
    )
    await hass.async_block_till_done()
    return hass.config_entries.async_entries(DOMAIN)[0]


def _statistic_id(hass, entry, suffix):
    registry = er.async_get(hass)
    return next(
        entity.entity_id
        for entity in er.async_entries_for_config_entry(registry, entry.entry_id)
        if entity.unique_id.endswith(suffix)
    )


async def test_backfill_writes_logbook_entry(
    recorder_mock,
    hass: HomeAssistant,
    enable_custom_integrations,
    aioclient_mock,
    price_payload,
):
    """A pótlás eredménye az eszköz Napló paneljén is megjelenik."""
    # A logbook teljes betöltése frontendet kívánna; itt elég betöltöttnek
    # jelölni: az `async_log_entry` csak eseményt küld.
    hass.config.components.add("logbook")
    hour = dt_util.utcnow().replace(minute=0, second=0, microsecond=0) - timedelta(
        hours=2
    )
    _mock_backfill_apis(aioclient_mock, price_payload, hour)
    entries = async_capture_events(hass, EVENT_LOGBOOK_ENTRY)

    entry = await _setup_entry(hass)
    await hass.async_block_till_done()

    assert len(entries) == 1
    logged = entries[0].data
    assert logged["name"] == "MVM D tarifa"
    assert logged["entity_id"] == _statistic_id(hass, entry, "_netto_energiadij")
    assert "2 adatpont beírva" in logged["message"]


async def test_backfill_runs_on_startup(
    recorder_mock,
    hass: HomeAssistant,
    enable_custom_integrations,
    aioclient_mock,
    price_payload,
):
    """Induláskor magától pótol, ha van adat a mai napra."""
    hour = dt_util.utcnow().replace(minute=0, second=0, microsecond=0) - timedelta(
        hours=2
    )
    _mock_backfill_apis(aioclient_mock, price_payload, hour)

    entry = await _setup_entry(hass)
    await async_wait_recording_done(hass)

    netto_id = _statistic_id(hass, entry, "_netto_energiadij")
    stats = await get_instance(hass).async_add_executor_job(
        statistics_during_period,
        hass,
        hour - timedelta(hours=1),
        hour + timedelta(hours=1),
        {netto_id},
        "hour",
        None,
        {"mean", "min", "max"},
    )
    # Négy negyedóra egy órában: átlag 56 EUR/MWh -> nettó 56 * 0.4 + 23.4 = 45.8
    rows = stats[netto_id]
    assert len(rows) == 1
    assert rows[0]["mean"] == 45.8
    assert rows[0]["min"] == 43.4  # 50 * 0.4 + 23.4
    assert rows[0]["max"] == 48.2  # 62 * 0.4 + 23.4

    # A kézi hívás ezek után már nem ír felül semmit.
    again = await hass.services.async_call(
        DOMAIN, "backfill", {}, blocking=True, return_response=True
    )
    assert again == {"beirt_orak": 0, "kihagyott_orak": 2, "szamolt_orak": 1}


async def test_backfill_service_writes_when_auto_is_off(
    recorder_mock,
    hass: HomeAssistant,
    enable_custom_integrations,
    aioclient_mock,
    price_payload,
):
    """Kikapcsolt automatikus pótlás mellett a szolgáltatás írja be az órákat."""
    hour = dt_util.utcnow().replace(minute=0, second=0, microsecond=0) - timedelta(
        hours=2
    )
    _mock_backfill_apis(aioclient_mock, price_payload, hour)

    await _setup_entry(hass, {**USER_INPUT, "automatikus_potlas": False})

    response = await hass.services.async_call(
        DOMAIN, "backfill", {}, blocking=True, return_response=True
    )
    await hass.async_block_till_done()
    await async_wait_recording_done(hass)

    assert response == {"beirt_orak": 2, "kihagyott_orak": 0, "szamolt_orak": 1}
