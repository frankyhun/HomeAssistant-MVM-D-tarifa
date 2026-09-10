"""Konstansok az MVM D (dinamikus) tarifa integrációhoz."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "mvm_d_tarifa"

# Adatforrások. Egyik sem igényel kulcsot vagy regisztrációt.
PRICE_URL: Final = "https://api.energy-charts.info/price?bzn=HU"
FX_URL: Final = "https://api.frankfurter.dev/v1/latest?from=EUR&to=HUF"
# Adott időszak árai. Ezt kéri a koordinátor is (a mai és a másnapi napra) és
# a statisztika-pótlás is. Az API a dátumot helyi (közép-európai) idő szerint
# értelmezi, a paraméter nélküli lekérés viszont az UTC szerinti mai napot adja
# — lásd a `DTarifaCoordinator._fetch_prices` magyarázatát.
PRICE_RANGE_URL: Final = PRICE_URL + "&start={start}&end={end}"
# A statisztika-pótláshoz az adott napon érvényes árfolyam. A frankfurter a
# legközelebbi korábbi munkanap árfolyamát adja — pontosan azt, amit az
# árfolyam-szenzor is mutatott aznap.
FX_DAY_URL: Final = "https://api.frankfurter.dev/v1/{day}?from=EUR&to=HUF"

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
CONF_AUTO_BACKFILL: Final = "automatikus_potlas"

DEFAULT_TRANSMISSION_FEE: Final = 3.39
DEFAULT_DISTRIBUTION_FEE: Final = 20.01
DEFAULT_VAT_MULTIPLIER: Final = 1.27
DEFAULT_MANUAL_FX: Final = 395.0
DEFAULT_A1_REFERENCE: Final = 70.1
DEFAULT_AUTO_BACKFILL: Final = True

SERVICE_BACKFILL: Final = "backfill"
# Az automatikus pótlás hálója: ennyi időnként újrapróbáljuk, hogy a HA
# leállása alatt keletkezett lyukak is betöltődjenek.
AUTO_BACKFILL_INTERVAL_HOURS: Final = 6

CURRENCY_PER_KWH: Final = "Ft/kWh"
CURRENCY_HUF: Final = "Ft"
