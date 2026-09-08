#!/usr/bin/env python3
"""Visszamenoleg potolja a D tarifa ar-szenzorok hosszu tavu statisztikait.

MIERT KELL EZ
=============
A `packages/d_tarifa.yaml` szandekosan `unavailable` allapotba teszi az
ar-szenzorokat, ha az energy-charts API-bol hianyzik az aktualis negyedora ara
(pl. 2026-09-08-an a HU zonara egesz ejjel nem volt publikalt adat). Ez helyes:
inkabb ne mutasson semmit, mint rossz arat. Viszont amikor az adat kesobb
megjon, a kieses ideje lyukkent marad a grafikonon.

Ez a script visszamenoleg kiszamolja a hianyzo orak arat az utolag publikalt
day-ahead adatbol, es beimportalja oket a Home Assistant hosszu tavu
statisztikaiba (`recorder/import_statistics` websocket parancs). A recorder
NEM ad erre szolgaltatast (csak `purge`, `purge_entities`, `enable`, `disable`,
`get_statistics`), ezert megy websocketen.

MIT TUD ES MIT NEM
==================
  IGEN  A `statistics` tabla oras atlag/min/max sorai - ezt hasznalja a
        `statistics-graph` kartya es a hosszabb idotavra zoomolt Elozmenyek
        nezet. A lyuk ott eltunik.
  NEM   A `states` tabla nyers allapot-tortenete. Azt a HA semmilyen tamogatott
        modon nem engedi visszamenoleg irni, tehat a rovid idotavu (nyers)
        Elozmenyek nezetben a kieses "Nem erheto el" savkent latszik tovabbra is.

Az importalas idempotens: ugyanarra az orara ugyanazt az erteket beirva a sor
egyszeruen felulirodik. Alapbol viszont csak azokat az orakat toltjuk fel,
amelyekre meg egyaltalan nincs statisztika - a HA sajat, allapotokbol szamolt
(idovel sulyozott) atlagat nem irjuk felul. Ezt a `--overwrite` kapcsolja ki.

FUTTATAS KEZZEL (asztali gepen)
===============================
    pip install websockets

    set HA_URL=http://192.168.1.240:8123
    set HA_TOKEN=<hosszu elettartamu hozzaferesi token>

    python tools/d_tarifa_backfill.py --dry-run --verbose
    python tools/d_tarifa_backfill.py

FUTTATAS A HOME ASSISTANTBOL (automatikusan)
============================================
A `packages/d_tarifa.yaml` tartalmaz egy `shell_command.d_tarifa_backfill`
bejegyzest es egy automatizalast, ami akkor inditja, amikor az ar-szenzor
`unavailable`-bol visszater, plusz 6 orankent halokent. Ilyenkor a script a HA
sajat kontenereben fut, es a `websockets` csomag helyett az `aiohttp`-t
hasznalja, ami a HA-ban mindig ott van - nem kell semmit telepiteni.

A token NEM mehet a parancssorba: a `shell_command` nem nulla visszateresi
ertek eseten a teljes parancsot beleirja a naploba. Ezert `--token-file`:

    /config/.d_tarifa_token      <- egyetlen sor, maga a token

A tokent a HA-ban a profilodnal tudod letrehozni (Biztonsag -> Hosszu
elettartamu hozzaferesi tokenek). Adminisztratori jog kell hozza: az
`import_statistics` parancs `require_admin`.

VISSZATERESI ERTEKEK
====================
    0   lefutott (akar ugy is, hogy nem volt potolnivalo)
    1   hiba
    2   az adatforras meg mindig nem ad adatot a kert tartomanyra
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone

# A HU zona a helyi (budapesti) idot koveti, es a CET/CEST eltolas mindig egesz
# ora, ezert az UTC oras hatarok egybeesnek a helyi oras hatarokkal. Az oras
# csoportositas emiatt nyugodtan mehet UTC-ben, kulon zoneinfo nelkul.
PRICE_URL = "https://api.energy-charts.info/price?bzn=HU&start={start}&end={end}"
FX_URL = "https://api.frankfurter.dev/v1/{day}?from=EUR&to=HUF"

NETTO_ID = "sensor.d_tarifa_netto_energiadij"
BRUTTO_ID = "sensor.d_tarifa_brutto_energiadij"
UNIT = "Ft/kWh"

DEFAULT_TOKEN_FILE = "/config/.d_tarifa_token"

HELPERS = {
    "afd": "input_number.d_atviteli_forgalmi_dij",
    "efd": "input_number.d_elosztoi_forgalmi_dij",
    "afa": "input_number.d_afa_kulcs",
    "fx_kezi": "input_number.d_arfolyam_kezi",
}


class Fail(Exception):
    """Felhasznalonak szant, mar megfogalmazott hiba."""


class HttpFail(Fail):
    """HTTP hiba, a statuszkoddal egyutt - a hivo dontheti el, mit kezd vele."""

    def __init__(self, message: str, code: int) -> None:
        super().__init__(message)
        self.code = code


class NoData(Exception):
    """Az adatforras meg mindig nem ad adatot - nem hiba, csak nincs mit tenni."""


class Deadline:
    """Idokeret.

    A `shell_command` 60 masodperc utan levagja a folyamatot (COMMAND_TIMEOUT),
    es HomeAssistantError-t dob, ami megallitja az automatizalast. Inkabb mi
    alljunk le elotte ertelmes uzenettel.
    """

    def __init__(self, budget: float) -> None:
        self._end = time.monotonic() + budget if budget > 0 else None

    def left(self) -> float:
        if self._end is None:
            return 1e9
        return self._end - time.monotonic()

    def check(self, what: str) -> None:
        if self.left() <= 0:
            raise Fail(f"Lejart az idokeret ({what}). Emeld meg a --budget erteket.")


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
def http_json(
    url: str,
    token: str | None = None,
    timeout: int = 20,
    deadline: Deadline | None = None,
    retries: int = 2,
) -> dict:
    # A frankfurter Cloudflare mogott ul, es a python-urllib alapertelmezett
    # User-Agent-jet 403 "Error 1010"-zel eldobja. Ezert kell sajat UA.
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "d-tarifa-backfill/1.0 (home-assistant config)",
        },
    )
    if token:
        req.add_header("Authorization", "Bearer " + token)

    attempt = 0
    while True:
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as err:
            body = err.read().decode("utf-8", "replace").strip()
            # Az energy-charts datumos vegpontja nincs cache-elve es konnyen ad
            # 429-et. Egy rovid varakozas utan altalaban atmegy.
            if err.code == 429 and attempt < retries:
                wait = 5.0
                if deadline is not None and deadline.left() < wait + timeout:
                    raise HttpFail(
                        f"HTTP 429 (Too Many Requests) - {url}\n"
                        "  Nincs eleg ido az ujraprobalasra az idokereten belul.",
                        err.code,
                    ) from err
                attempt += 1
                time.sleep(wait)
                continue
            raise HttpFail(
                f"HTTP {err.code} - {url}\n  valasz: {body[:200]}", err.code
            ) from err
        except urllib.error.URLError as err:
            raise Fail(f"Nem sikerult elerni: {url}\n  {err.reason}") from err
        except json.JSONDecodeError as err:
            raise Fail(f"A valasz nem JSON: {url}\n  {err}") from err


# --------------------------------------------------------------------------
# Adatok osszeszedese
# --------------------------------------------------------------------------
def fetch_prices(start: date, end: date, deadline: Deadline) -> list[tuple[int, float]]:
    """(unix_seconds, EUR/MWh) parok, a null arak kihagyva."""
    deadline.check("ar-lekerdezes")
    try:
        data = http_json(
            PRICE_URL.format(start=start.isoformat(), end=end.isoformat()),
            deadline=deadline,
        )
    except HttpFail as err:
        # Ha a kert tartomany EGYETLEN napjara sincs adat, az energy-charts nem
        # ures tombot ad, hanem 404 "no content available"-t. Ez nem hiba, csak
        # meg nincs mit potolni - kulon visszateresi ertekkel jelezzuk, hogy az
        # automatizalas ne figyelmeztetesnek vegye.
        if err.code == 404:
            raise NoData(
                "Az energy-charts nem ad HU adatot erre a tartomanyra "
                f"({start} .. {end}) - 404 no content available."
            ) from err
        raise
    times = data.get("unix_seconds") or []
    prices = data.get("price") or []
    unit = data.get("unit")
    if unit != "EUR / MWh":
        raise Fail(f"Varatlan mertekegyseg az API-tol: {unit!r} (EUR / MWh helyett)")
    # A publikalatlan negyedorak null-kent jonnek. Ezeket kihagyjuk, nem
    # 0 EUR/MWh-kent szamoljuk - ugyanaz a logika, mint a package-ben.
    return [(int(t), float(p)) for t, p in zip(times, prices) if p is not None]


def fetch_fx_for_day(day: date, cache: dict[date, float], deadline: Deadline) -> float:
    """Az adott napra ervenyes EKB EUR/HUF arfolyam.

    A frankfurter a legkozelebbi korabbi munkanap arfolyamat adja vissza, ami
    pontosan az, amit a `sensor.eur_huf_arfolyam` is mutatott volna aznap.
    """
    if day not in cache:
        deadline.check("arfolyam-lekerdezes")
        data = http_json(FX_URL.format(day=day.isoformat()), deadline=deadline)
        rate = data.get("rates", {}).get("HUF")
        if not rate:
            raise Fail(f"Nincs EUR/HUF arfolyam erre a napra: {day}")
        cache[day] = float(rate)
    return cache[day]


def fetch_params(
    ha_url: str, token: str, args: argparse.Namespace, deadline: Deadline
) -> dict[str, float]:
    """Dijtetelek a HA helperekbol, parancssori feluldefinialassal."""
    params: dict[str, float] = {}
    for key, entity in HELPERS.items():
        override = getattr(args, key, None)
        if override is not None:
            params[key] = float(override)
            continue
        deadline.check(f"{entity} lekerdezese")
        state = http_json(
            f"{ha_url}/api/states/{entity}", token=token, deadline=deadline
        ).get("state")
        try:
            params[key] = float(state)
        except (TypeError, ValueError):
            raise Fail(f"{entity} allapota nem szam: {state!r}") from None

    # Ugyanaz az ellenorzes, mint a package `beallitva` valtozoja: a helperek
    # `initial:` nelkul a `min` erteken allnak (fx=200, afa=1, dijak=0), ami
    # ervenyes szam, csak csendben rossz arat adna.
    if not (params["afa"] > 1 and params["afd"] > 0 and params["efd"] > 0):
        raise Fail(
            "A dij-helperek nincsenek beallitva (afa > 1, atviteli > 0, "
            "elosztoi > 0 kellene).\n  Beolvasott ertekek: "
            f"afa={params['afa']}, atviteli={params['afd']}, elosztoi={params['efd']}"
        )
    return params


def read_token(args: argparse.Namespace) -> str:
    """Token: --token > HA_TOKEN > --token-file > /config/.d_tarifa_token."""
    if args.token:
        return args.token.strip()
    env = os.environ.get("HA_TOKEN")
    if env:
        return env.strip()

    path = args.token_file or (
        DEFAULT_TOKEN_FILE if os.path.isfile(DEFAULT_TOKEN_FILE) else None
    )
    if path:
        try:
            with open(path, encoding="utf-8") as handle:
                token = handle.read().strip()
        except OSError as err:
            raise Fail(f"A token fajl nem olvashato: {path}\n  {err}") from err
        if not token:
            raise Fail(f"A token fajl ures: {path}")
        return token

    raise Fail(
        "Nincs token. Add meg a --token kapcsoloval, a HA_TOKEN kornyezeti "
        f"valtozoban, vagy tedd egy fajlba (--token-file, alapbol {DEFAULT_TOKEN_FILE})."
    )


# --------------------------------------------------------------------------
# Szamitas
# --------------------------------------------------------------------------
def hourly_stats(
    quarters: list[tuple[int, float]],
    params: dict[str, float],
    fx_override: float | None,
    fx_cache: dict[date, float],
    deadline: Deadline,
) -> dict[int, dict[str, float]]:
    """Negyedoras EUR/MWh -> oras netto Ft/kWh atlag/min/max, ora-kezdet szerint."""
    buckets: dict[int, list[float]] = {}
    for ts, eur_mwh in quarters:
        if fx_override is not None:
            fx = fx_override
        else:
            day = datetime.fromtimestamp(ts, timezone.utc).date()
            fx = fetch_fx_for_day(day, fx_cache, deadline)
        netto = eur_mwh * fx / 1000 + params["afd"] + params["efd"]
        buckets.setdefault(ts - ts % 3600, []).append(netto)

    return {
        hour: {
            "mean": sum(values) / len(values),
            "min": min(values),
            "max": max(values),
            "count": float(len(values)),
        }
        for hour, values in sorted(buckets.items())
    }


# --------------------------------------------------------------------------
# Websocket - ket hatterrel
# --------------------------------------------------------------------------
def ws_url(ha_url: str) -> str:
    base = ha_url.rstrip("/")
    if base.startswith("https://"):
        return "wss://" + base[len("https://"):] + "/api/websocket"
    if base.startswith("http://"):
        return "ws://" + base[len("http://"):] + "/api/websocket"
    raise Fail(f"A HA cim http:// vagy https:// kell legyen: {ha_url!r}")


class HaWs:
    """Minimalis HA websocket kliens: auth + parancsok.

    Ket hattere van, mert ketfele helyen fut:
      * `aiohttp`    - a HA sajat kontenereben (shell_command), ott biztosan van
      * `websockets` - asztali gepen, kezi futtataskor
    """

    def __init__(self, url: str, token: str, insecure: bool = False) -> None:
        self._url = url
        self._token = token
        self._insecure = insecure
        self._id = 0
        self._backend = ""
        self._conn = None
        self._session = None

    def _ssl_ctx(self):
        if not (self._insecure and self._url.startswith("wss://")):
            return None
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx

    async def __aenter__(self) -> "HaWs":
        errors = []
        try:
            import aiohttp

            self._backend = "aiohttp"
            # Sajat feloldo: Windowson az aiohttp alapertelmezett aiodns
            # feloldoja SelectorEventLoop-ot kovetel, es a Python 3.8+ ott
            # ProactorEventLoop-ot hasznal - a ClientSession letrehozasa mar
            # RuntimeError-rel elszallna. A HA kontenereben (Linux) ennek nincs
            # jelentosege, itt viszont ez teszi kezzel futtathatova.
            connector = aiohttp.TCPConnector(resolver=aiohttp.ThreadedResolver())
            self._session = aiohttp.ClientSession(connector=connector)
            ctx = self._ssl_ctx()
            kwargs = {"max_msg_size": 32 * 1024 * 1024}
            if ctx is not None:
                kwargs["ssl"] = ctx
            self._conn = await self._session.ws_connect(self._url, **kwargs)
        except Exception as err:  # noqa: BLE001 - barmi jon, van masik hatterunk
            errors.append(f"aiohttp: {err}")
            self._backend = ""
            if self._session is not None:
                await self._session.close()
                self._session = None
            self._conn = None

        if self._conn is None:
            connect = None
            try:
                from websockets.asyncio.client import connect  # websockets >= 13
            except ImportError:
                try:
                    from websockets.client import connect  # type: ignore[no-redef]
                except ImportError as err:
                    errors.append(f"websockets: {err}")
            if connect is not None:
                try:
                    self._backend = "websockets"
                    kwargs = {"max_size": 32 * 1024 * 1024}
                    ctx = self._ssl_ctx()
                    if ctx is not None:
                        kwargs["ssl"] = ctx
                    self._conn = await connect(self._url, **kwargs)
                except Exception as err:  # noqa: BLE001
                    errors.append(f"websockets: {err}")
                    self._backend = ""
                    self._conn = None

        if self._conn is None:
            raise Fail(
                f"Nem sikerult websocket kapcsolatot nyitni: {self._url}\n  "
                + "\n  ".join(errors)
                + "\n  (asztali gepen, ha egyik konyvtar sincs meg: pip install websockets)"
            )

        hello = json.loads(await self._recv())
        if hello.get("type") != "auth_required":
            raise Fail(f"Varatlan udvozlo uzenet a HA-tol: {hello}")
        await self._send(json.dumps({"type": "auth", "access_token": self._token}))
        reply = json.loads(await self._recv())
        if reply.get("type") != "auth_ok":
            raise Fail(
                "A token nem fogadhato el. Adminisztratori, hosszu elettartamu "
                f"tokenre van szukseg.\n  valasz: {reply}"
            )
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._conn is not None:
            await self._conn.close()
        if self._session is not None:
            await self._session.close()

    async def _send(self, text: str) -> None:
        if self._backend == "aiohttp":
            await self._conn.send_str(text)
        else:
            await self._conn.send(text)

    async def _recv(self) -> str:
        if self._backend == "aiohttp":
            msg = await self._conn.receive()
            import aiohttp

            if msg.type is not aiohttp.WSMsgType.TEXT:
                raise Fail(f"A websocket kapcsolat megszakadt: {msg.type!r}")
            return msg.data
        return await self._conn.recv()

    async def cmd(self, payload: dict) -> dict:
        self._id += 1
        msg_id = self._id
        await self._send(json.dumps({"id": msg_id, **payload}))
        while True:
            reply = json.loads(await self._recv())
            if reply.get("id") == msg_id and reply.get("type") == "result":
                return reply


def _stat_start_to_epoch(value: object) -> int:
    """A statistics_during_period `start` mezoje ms epoch vagy ISO string."""
    if isinstance(value, (int, float)):
        return int(value // 1000)
    return int(datetime.fromisoformat(str(value)).timestamp())


async def existing_hours(ws: HaWs, stat_id: str, start: int, end: int) -> set[int]:
    reply = await ws.cmd(
        {
            "type": "recorder/statistics_during_period",
            "start_time": datetime.fromtimestamp(start, timezone.utc).isoformat(),
            "end_time": datetime.fromtimestamp(end + 3600, timezone.utc).isoformat(),
            "statistic_ids": [stat_id],
            "period": "hour",
            "types": ["mean"],
        }
    )
    if not reply.get("success"):
        raise Fail(f"A meglevo statisztikak lekerdezese nem sikerult: {reply}")
    rows = (reply.get("result") or {}).get(stat_id) or []
    return {_stat_start_to_epoch(row["start"]) for row in rows}


async def import_stats(ws: HaWs, stat_id: str, rows: list[dict]) -> None:
    """Import a modern metaadat-semaval, szukseg eseten a regire visszaesve."""
    base = {
        "has_sum": False,
        "name": None,
        "source": "recorder",
        "statistic_id": stat_id,
        "unit_of_measurement": UNIT,
    }
    # A `mean_type` es `unit_class` kulcs 2026.11-tol kotelezo lesz, de regebbi
    # HA-n meg nem letezik - ott a voluptuous sema "extra keys not allowed"
    # hibaval bukna. Ezert eloszor az ujjal probalunk, aztan a regivel.
    variants = [
        {**base, "mean_type": 1, "has_mean": True, "unit_class": None},
        {**base, "has_mean": True},
    ]
    last = None
    for metadata in variants:
        reply = await ws.cmd(
            {"type": "recorder/import_statistics", "metadata": metadata, "stats": rows}
        )
        if reply.get("success"):
            return
        last = reply
    raise Fail(f"Az import nem sikerult ({stat_id}): {last}")


# --------------------------------------------------------------------------
# Fo folyamat
# --------------------------------------------------------------------------
async def run(args: argparse.Namespace) -> int:
    deadline = Deadline(args.budget)
    ha_url = args.ha_url.rstrip("/")
    token = read_token(args)

    today = date.today()
    start_day = date.fromisoformat(args.start) if args.start else today - timedelta(days=1)
    end_day = date.fromisoformat(args.end) if args.end else today
    if end_day < start_day:
        raise Fail("A --end nem lehet korabbi, mint a --start.")

    params = fetch_params(ha_url, token, args, deadline)
    fx_override = args.fx
    if fx_override is None and args.use_manual_fx:
        fx_override = params["fx_kezi"]
    # Ugyanaz a hatar, mint a package `beallitva` valtozojaban: a kezi arfolyam
    # helper `min` erteke 200, ami ervenyes szam, csak nyilvanvaloan rossz.
    if fx_override is not None and fx_override <= 250:
        raise Fail(
            f"Az EUR/HUF arfolyam gyanusan alacsony: {fx_override}. Allitsd be az "
            "input_number.d_arfolyam_kezi erteket, vagy add meg a --fx kapcsoloval."
        )

    quarters = fetch_prices(start_day, end_day, deadline)
    if not quarters:
        raise NoData(
            "Az energy-charts meg mindig nem ad HU adatot erre a tartomanyra "
            f"({start_day} .. {end_day})."
        )

    fx_cache: dict[date, float] = {}
    hours = hourly_stats(quarters, params, fx_override, fx_cache, deadline)

    # A meg futo (aktualis) orat nem toltjuk fel: azt a HA magatol lezarja es
    # kiszamolja a valodi allapotokbol - a fel oranyi adatunk csak rontana.
    now_hour = int(datetime.now(timezone.utc).timestamp()) // 3600 * 3600
    hours = {h: v for h, v in hours.items() if h < now_hour}
    if not hours:
        print("KESZ: nincs potolnivalo (nincs mar lezart ora a tartomanyban)")
        return 0

    afa = params["afa"]
    lo, hi = min(hours), max(hours)
    written = 0

    deadline.check("websocket kapcsolodas")
    async with HaWs(ws_url(ha_url), token, insecure=args.insecure) as ws:
        for stat_id, factor in ((NETTO_ID, 1.0), (BRUTTO_ID, afa)):
            have = set() if args.overwrite else await existing_hours(ws, stat_id, lo, hi)
            rows = [
                {
                    "start": datetime.fromtimestamp(hour, timezone.utc).isoformat(),
                    "mean": round(values["mean"] * factor, 2),
                    "min": round(values["min"] * factor, 2),
                    "max": round(values["max"] * factor, 2),
                }
                for hour, values in sorted(hours.items())
                if hour not in have
            ]
            skipped = len(hours) - len(rows)

            if not args.quiet or rows:
                label = f"{stat_id}: {len(rows)} ora importalando"
                if skipped:
                    label += f", {skipped} kihagyva (mar van statisztika)"
                print(label)

            if args.verbose or args.dry_run:
                for row in rows:
                    local = datetime.fromisoformat(row["start"]).astimezone()
                    print(
                        f"    {local:%Y-%m-%d %H:%M}  atlag {row['mean']:>7.2f}  "
                        f"min {row['min']:>7.2f}  max {row['max']:>7.2f}  {UNIT}"
                    )

            if not rows:
                continue
            if args.dry_run:
                print("    (--dry-run: nem irtunk semmit)")
                continue
            await import_stats(ws, stat_id, rows)
            written += len(rows)
            print(f"    kesz: {len(rows)} ora beirva")

    if args.dry_run:
        print("KESZ: dry-run, nem irtunk semmit")
    elif written:
        print(f"KESZ: {written} ora potolva")
        if not args.quiet:
            print(
                "\nA lyuk a `statistics-graph` kartyan es a hosszabb idotavu Elozmenyek\n"
                "nezetben most mar kitoltott. A nyers (states) tortenet valtozatlanul\n"
                "'Nem erheto el' marad a kieses idejere - azt a HA nem engedi visszairni."
            )
    else:
        print("KESZ: nincs potolnivalo")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="D tarifa ar-statisztikak visszamenoleges potlasa Home Assistantban.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--ha-url", default=os.environ.get("HA_URL", "http://192.168.1.240:8123")
    )
    parser.add_argument(
        "--token", default=None, help="HA hosszu elettartamu token (vagy HA_TOKEN)"
    )
    parser.add_argument(
        "--token-file",
        dest="token_file",
        default=None,
        help=f"a tokent tartalmazo fajl (alapbol {DEFAULT_TOKEN_FILE}, ha letezik)",
    )
    parser.add_argument(
        "--start", default=None, metavar="YYYY-MM-DD", help="alapertelmezes: tegnap"
    )
    parser.add_argument(
        "--end", default=None, metavar="YYYY-MM-DD", help="alapertelmezes: ma"
    )
    parser.add_argument("--dry-run", action="store_true", help="csak megmutatja, mit irna be")
    parser.add_argument(
        "--overwrite", action="store_true", help="a meglevo oras statisztikakat is felulirja"
    )
    parser.add_argument("--verbose", action="store_true", help="orankenti bontas kiirasa")
    parser.add_argument("--quiet", action="store_true", help="csak a lenyeget irja ki")
    parser.add_argument(
        "--budget",
        type=float,
        default=45.0,
        help="idokeret masodpercben (0 = nincs); a shell_command 60 s utan levag",
    )
    parser.add_argument(
        "--insecure", action="store_true", help="onalairt HTTPS tanusitvany elfogadasa"
    )
    parser.add_argument(
        "--fx", type=float, default=None, help="fix EUR/HUF arfolyam a napi EKB helyett"
    )
    parser.add_argument(
        "--use-manual-fx",
        action="store_true",
        help="az input_number.d_arfolyam_kezi erteket hasznalja arfolyamkent",
    )
    parser.add_argument("--afd", type=float, default=None, help="atviteli forgalmi dij")
    parser.add_argument("--efd", type=float, default=None, help="elosztoi forgalmi dij")
    parser.add_argument("--afa", type=float, default=None, help="afa szorzo")
    parser.add_argument(
        "--fx-kezi", dest="fx_kezi", type=float, default=None, help=argparse.SUPPRESS
    )
    args = parser.parse_args()

    try:
        return asyncio.run(run(args))
    except NoData as err:
        # Nem hiba: az automatizalas ebbol tudja, hogy csak varni kell.
        print(f"NINCS ADAT: {err}")
        return 2
    except Fail as err:
        print(f"HIBA: {err}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
