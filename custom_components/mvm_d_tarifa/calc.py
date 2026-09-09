"""Ár-számítás. Tiszta függvények, Home Assistant nélkül tesztelhetők.

Képlet (MVM Next hivatalos árképzés):

    nettó [Ft/kWh] = HUPX_negyedóra [EUR/MWh] * EUR_HUF / 1000
                     + átviteli forgalmi díj + elosztói forgalmi díj
    bruttó         = nettó * áfa szorzó
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass

from .const import STALE_SECONDS


@dataclass(frozen=True, slots=True)
class Tariff:
    """A felhasználó által megadott díjtételek."""

    transmission_fee: float
    distribution_fee: float
    vat_multiplier: float
    manual_fx: float
    a1_reference: float

    def net(self, eur_mwh: float, fx: float) -> float:
        """Nettó energiadíj Ft/kWh-ban."""
        return eur_mwh * fx / 1000 + self.transmission_fee + self.distribution_fee

    def gross(self, eur_mwh: float, fx: float) -> float:
        """Bruttó energiadíj Ft/kWh-ban."""
        return self.net(eur_mwh, fx) * self.vat_multiplier


def current_index(times: list[float], now_ts: float) -> int:
    """Az aktuális negyedóra indexe: hány időbélyeg van már mögöttünk.

    -1, ha az első publikált negyedóra még el sem kezdődött.
    """
    return bisect_right(times, now_ts) - 1


def current_eur_mwh(
    times: list[float],
    prices: list[float | None],
    now_ts: float,
    max_age: float = STALE_SECONDS,
) -> float | None:
    """Az aktuális negyedóra ára EUR/MWh-ban, vagy None.

    None, ha nincs érvényes index, ha az API publikálatlan (null) árat ad — ilyenkor
    a 0 Ft/kWh valós, olcsó árnak látszana —, vagy ha az adat beragadt.
    """
    idx = current_index(times, now_ts)
    if idx < 0 or idx >= len(prices):
        return None
    price = prices[idx]
    if price is None:
        return None
    if now_ts - times[idx] >= max_age:
        return None
    return float(price)


def day_slice(
    times: list[float],
    prices: list[float | None],
    start_ts: float,
    end_ts: float,
) -> list[tuple[float, float]]:
    """A [start_ts, end_ts) intervallumba eső (időbélyeg, ár) párok.

    A publikálatlan (null) árak kimaradnak: különben a min/max/átlag hibás lenne.
    A határokat a hívó számolja a helyi naptári napból — NEM fix 86400 másodperc,
    mert DST-kor a helyi nap 23 vagy 25 órából (92 / 100 negyedórából) áll.
    """
    lo = bisect_left(times, start_ts)
    hi = bisect_left(times, end_ts)
    return [
        (times[i], float(prices[i]))
        for i in range(lo, min(hi, len(prices)))
        if prices[i] is not None
    ]
