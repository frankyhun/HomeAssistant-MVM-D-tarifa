"""Közös teszt-beállítások."""

import pathlib
import sys

import pytest
import pytest_socket

# A pytest-homeassistant-custom-component minden teszt előtt letiltja a
# socketeket (`disable_socket(allow_unix_socket=True)`). Windowson viszont a
# ProactorEventLoop már a létrehozásakor sima socketet nyit, ezért a tesztek el
# sem indulnának. A tiltást ezért kikapcsoljuk. Valódi hálózat így sem
# keletkezik: a HTTP hívásokat az `aioclient_mock` fixture fogja el.
pytest_socket.disable_socket = lambda *args, **kwargs: None
pytest_socket.enable_socket()

if sys.platform == "win32":
    # Az aiohttp alap DNS-feloldója (aiodns) Windowson SelectorEventLoopot vagy
    # winloopot vár, a teszt-loop viszont Proactor. A hálózat amúgy is mockolt,
    # ezért a szálas feloldóra váltunk - Linuxon (CI) semmi sem változik.
    import aiohttp.connector
    import aiohttp.resolver

    aiohttp.connector.DefaultResolver = aiohttp.resolver.ThreadedResolver

# A Home Assistant az importálható `custom_components` csomagból szedi az egyedi
# integrációkat. A p-h-c-c saját `testing_config/custom_components` csomagja
# viszont rendes (van `__init__.py`), ezért az importban legyőzi a tárolóét, és
# az `mvm_d_tarifa` nem látszana. Ezért a sajátunkat hozzáfűzzük a csomag
# keresési útjához.
_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

import custom_components  # noqa: E402

if str(_ROOT / "custom_components") not in custom_components.__path__:
    custom_components.__path__.append(str(_ROOT / "custom_components"))


@pytest.fixture(autouse=True)
def _custom_integrations(enable_custom_integrations):
    """A custom_components mappa betöltése minden tesztben."""
    return None
