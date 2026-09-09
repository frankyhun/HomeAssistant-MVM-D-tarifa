"""Konstansok az MVM D (dinamikus) tarifa integrációhoz."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "mvm_d_tarifa"

# Adatforrások. Egyik sem igényel kulcsot vagy regisztrációt.
PRICE_URL: Final = "https://api.energy-charts.info/price?bzn=HU"
FX_URL: Final = "https://api.frankfurter.dev/v1/latest?from=EUR&to=HUF"

REQUEST_TIMEOUT: Final = 30

# A koordinátor percenként fut, de hálózatra csak akkor megy, ha a gyorsítótár
# elévült: így az aktuális negyedóra váltása legfeljebb egy percet késik,
# közben viszont nem terheljük az API-kat.
UPDATE_INTERVAL: Final = timedelta(minutes=1)
PRICE_TTL: Final = 900  # 15 perc
FX_TTL: Final = 3600  # 1 óra

# Ha az aktuális negyedóra kezdete ennél régebbi, beragadt az ár-adat, és
# inkább ne mutassunk semmit, mint egy tegnapi árat aktuálisként. A határ azért
# 1800 és nem 900, hogy egy negyedóra tartalék maradjon a futási késésre.
STALE_SECONDS: Final = 1800

# Konfigurációs kulcsok
CONF_TRANSMISSION_FEE: Final = "atviteli_forgalmi_dij"
CONF_DISTRIBUTION_FEE: Final = "elosztoi_forgalmi_dij"
CONF_VAT_MULTIPLIER: Final = "afa_szorzo"
CONF_MANUAL_FX: Final = "arfolyam_kezi"
CONF_A1_REFERENCE: Final = "a1_referencia"

DEFAULT_TRANSMISSION_FEE: Final = 3.39
DEFAULT_DISTRIBUTION_FEE: Final = 20.01
DEFAULT_VAT_MULTIPLIER: Final = 1.27
DEFAULT_MANUAL_FX: Final = 395.0
DEFAULT_A1_REFERENCE: Final = 70.1

CURRENCY_PER_KWH: Final = "Ft/kWh"
CURRENCY_HUF: Final = "Ft"
