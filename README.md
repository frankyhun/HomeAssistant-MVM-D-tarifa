# MVM D (dinamikus) tarifa csomag

Home Assistant *package*, amely a magyar **MVM Next D (dinamikus) árszabás** aktuális
negyedórás árát számolja ki, natív `rest` és `template` integrációval — egyedi
komponens vagy HACS nélkül.

Fájl: [`d_tarifa.yaml`](packages/d_tarifa.yaml)

![screenshot](./screenshot.jpg)

---

## Mit csinál?

Negyedóránként lekéri a magyar zóna másnapi (day-ahead) tőzsdei villamosenergia-árát,
átváltja forintra, hozzáadja a hálózati díjakat és az áfát, majd ebből Home Assistant
szenzorokat képez:

- mennyi **most** az áram ára Ft/kWh-ban (nettó és bruttó),
- mennyi a **mai átlag**, minimum és maximum, és mikor van a legolcsóbb, illetve a
  legdrágább negyedóra,
- **olcsó-e most** az áram az A1 sávhatár feletti fix árhoz képest — bináris szenzor,
  automatizáláshoz.

### Képlet

```
nettó [Ft/kWh] = HUPX_negyedóra [EUR/MWh] * EUR_HUF / 1000
                 + átviteli forgalmi díj + elosztói forgalmi díj

bruttó         = nettó * áfa szorzó (1.27)
```

### Adatforrások

| Adat | Forrás | Frissítés |
|---|---|---|
| Negyedórás day-ahead ár (HU zóna, EUR/MWh) | [api.energy-charts.info](https://api.energy-charts.info/) (Fraunhofer ISE) | 15 percenként |
| EUR/HUF árfolyam | [api.frankfurter.dev](https://api.frankfurter.dev/v1/latest?from=EUR&to=HUF) (EKB referencia-árfolyam) | óránként |

Egyik sem igényel kulcsot vagy regisztrációt.

Ugyanezt a forráspárost (energy-charts + EKB árfolyam) használja a holadelej.hu is,
így az értékek egymással összevethetők.

> **Licenc és forrásmegjelölés.** Az ár adat eredeti forrása a
> Bundesnetzagentur | SMARD.de, **CC BY 4.0** licenc alatt
> (<https://creativecommons.org/licenses/by/4.0/>), változtatás nélkül.
> A csomag ezért eltárolja a válasz `license_info` mezőjét a `sensor.hupx_arak`
> attribútumai között — ezt **ne távolítsd el**.

---

## Követelmények

- **Home Assistant 2024.10 vagy újabb.** A trigger blokkokban a modern `trigger:`
  kulcsot használjuk a régi `platform:` helyett, és ez 2024.10-ben jelent meg.
  Régebbi verzión mind az **öt** `trigger:` sort `platform:`-ra kell írni (három a
  `template` blokkban, kettő az automatizálásban), az automatizálásban ezen felül a
  `triggers:` / `conditions:` / `actions:` kulcsokat egyes számba (`trigger:` /
  `condition:` / `action:`), a szolgáltatáshívó `action:` sorokat pedig `service:`-re.
- Kimenő internetkapcsolat a fenti két API felé.
- Semmilyen HACS komponens vagy egyedi integráció nem szükséges.

---

## Telepítés

### 1. lépés — a fájl helye

Másold a `d_tarifa.yaml` fájlt a Home Assistant konfigurációs könyvtárán belül a
`packages` mappába:

```
/config/packages/d_tarifa.yaml
```

Ha a `packages` mappa még nem létezik, hozd létre.

### 2. lépés — a packages betöltése a `configuration.yaml`-ban

A `homeassistant:` blokk alá vedd fel a `packages` sort:

```yaml
homeassistant:
  packages: !include_dir_named packages
```

Ha a `homeassistant:` blokkban már szerepel a `packages:` sor, ezt a lépést
átugorhatod.

### 3. lépés — konfiguráció ellenőrzése és újraindítás

**Fejlesztői eszközök → YAML → Konfiguráció ellenőrzése**, majd ha hibátlan:
**Fejlesztői eszközök → YAML → Újraindítás**.

Home Assistant OS-en parancssorból is ellenőrizhető:

```bash
ha core check
```

### 4. lépés — az öt helper beállítása (kötelező)

Újraindítás után a **Beállítások → Eszközök és szolgáltatások → Helperek** alatt
állítsd be az öt `input_number` értéket:

| Helper | Ajánlott kezdőérték |
|---|---|
| Átviteli forgalmi díj (nettó) | `3.39` Ft/kWh |
| Elosztói forgalmi díj (nettó) | `20.01` Ft/kWh |
| ÁFA szorzó | `1.27` |
| EUR/HUF (kézi tartalék) | `395` Ft |
| A1 sávhatár feletti bruttó ár | `70.1` Ft/kWh |

A pontos díjtételek a saját MVM-számládon szerepelnek, érdemes onnan átvenni őket.

> **Amíg ezt nem teszed meg, az ár-szenzorok szándékosan `Nem érhető el`
> (unavailable) állapotban maradnak. Ez nem hiba.**
>
> A helperekben nincs `initial:` érték — az kikapcsolná az állapot-visszatöltést, és
> minden HA-újraindításkor felülírná a kézzel beírt értékeket. Emiatt viszont a
> helperek létrehozáskor a `min` értéken állnak: árfolyam = 200, áfa = 1.0,
> díjak = 0. Ezek önmagukban érvényes számok, így a szenzor magától *nem* jelezne
> hibát — csak csendben kb. 36%-kal alacsonyabb árat mutatna. Ezért a csomag
> explicit ellenőrzi (`beallitva` változó), hogy tényleg beállítottad-e őket.

---

## Létrehozott entitások

### Ár-szenzorok

| Entitás | Leírás |
|---|---|
| `sensor.d_tarifa_netto_energiadij` | Az aktuális negyedóra **nettó** ára Ft/kWh. Attribútumok: `hupx_eur_mwh`, `arfolyam`, `negyedora_index`, `szamitva` |
| `sensor.d_tarifa_brutto_energiadij` | Ugyanez **bruttó** (× áfa szorzó) |
| `sensor.d_tarifa_mai_atlag_brutto` | A mai nap átlagos bruttó ára. Attribútumok: `minimum`, `maximum`, `legolcsobb_idopont`, `legdragabb_idopont` |
| `binary_sensor.d_tarifa_olcso` | `on`, ha az aktuális bruttó ár az A1 referenciaár alatt van. Amíg az `input_number.d_a1_referencia` `0` (nincs beállítva), **nem érhető el** — nem `off`. Attribútumok: `kuszob`, `brutto`, `szamitva` |

### Nyers adat (REST)

| Entitás | Leírás |
|---|---|
| `sensor.hupx_arak` | A negyedórás árak darabszáma: `192` (tegnap + ma) vagy `288` (a másnapiakkal együtt). `96` = csak tegnap van meg, azaz **hiányzik a mai adat a forrásból**; `0` = a válasz nem is volt JSON. Attribútumok: `unix_seconds`, `price`, `unit`, `license_info` |
| `sensor.eur_huf_arfolyam` | Aktuális EKB EUR/HUF árfolyam |

### Helperek

`input_number.d_atviteli_forgalmi_dij`, `input_number.d_elosztoi_forgalmi_dij`,
`input_number.d_afa_kulcs`, `input_number.d_arfolyam_kezi`,
`input_number.d_a1_referencia`

`input_boolean.d_tarifa_auto_backfill` — az automatikus statisztika-pótlás
kapcsolója, lásd [A kiesés utólagos pótlása](#a-kiesés-utólagos-pótlása).
Alapból **ki** van kapcsolva.

### Szolgáltatás és automatizálás

| Név | Leírás |
|---|---|
| `shell_command.d_tarifa_backfill` | Elindítja a pótló scriptet. Kézzel is hívható a **Fejlesztői eszközök → Műveletek** alatt. |
| `automation.d_tarifa_hianyzo_ar_statisztikak_potlasa` | Kiesés után, illetve 6 óránként lefuttatja a fentit |

---

## A működés ellenőrzése

**Fejlesztői eszközök → Állapotok**, majd:

1. `sensor.hupx_arak` állapota `192` vagy `288`.
2. Ugyanennek az `unit` attribútuma `EUR / MWh`.
3. `sensor.eur_huf_arfolyam` egy nagyjából 350–420 közötti szám.
4. `sensor.d_tarifa_brutto_energiadij` értelmes Ft/kWh értéket mutat.

---

## Hibaelhárítás

### Az ár-szenzorok `Nem érhető el` állapotban vannak

Sorrendben ezeket nézd meg:

1. **Nincsenek beállítva a helperek.** Friss telepítés után messze ez a leggyakoribb
   ok. A szenzorok csak akkor érhetők el, ha egyszerre teljesül: árfolyam > 250,
   áfa szorzó > 1, átviteli díj > 0 **és** elosztói díj > 0. Lásd a telepítés
   4. lépését.

2. **Elavult ár adat.** Ha az aktuális negyedóra kezdete 30 percnél régebbi — vagyis
   beragadt vagy leállt az adatlekérés —, a szenzor inkább elérhetetlen lesz, mint
   hogy egy tegnapi árat mutasson aktuálisként. Ellenőrizd a `sensor.hupx_arak`
   `unix_seconds` attribútumát.

3. **Publikálatlan negyedóra.** Ha az API `null` árat ad az aktuális negyedórára, a
   szenzor elérhetetlen lesz — nem pedig 0 Ft/kWh.

4. **Az adatforrásból hiányzik az egész mai nap.** Ez nem elméleti: 2026-09-08-án a
   HU (és az SK) zónára az energy-charts egyetlen negyedórát sem adott, miközben a
   DE-LU és az AT zónának megvolt a teljes napja. Felismerése: `sensor.hupx_arak`
   állapota `96`, és az `unix_seconds` utolsó eleme tegnap 23:45.

   Ilyenkor nincs mit tenni a HA oldalán — az árak akkor jönnek vissza, amikor a
   forrás pótolja az adatot; a következő 15 perces lekérdezés magától helyreállítja
   a szenzorokat. A kiesés alatt keletkezett grafikon-lyuk utólag betölthető, lásd
   [A kiesés utólagos pótlása](#a-kiesés-utólagos-pótlása).

### Csak a `binary_sensor.d_tarifa_olcso` nem érhető el

Az `input_number.d_a1_referencia` nincs beállítva, ezért `0`-n áll. A csomag a `0`
küszöböt szándékosan „nincs beállítva”-ként kezeli, és a szenzort inkább
`Nem érhető el` állapotban tartja — korábban ilyenkor a `0 Ft/kWh`-hoz hasonlított,
tehát félrevezetően **mindig `Ki`** volt, 43 Ft/kWh-nál is.

**Fejlesztői eszközök → Állapotok**-ban ellenőrizd az értékét, és állítsd be
(ajánlott: `70.1`) — a szenzor azonnal életre kel. A `kuszob` attribútum mutatja,
mivel hasonlít éppen össze.

### Csak a `sensor.d_tarifa_mai_atlag_brutto` nem érhető el

Akkor fordul elő, ha az ár-tömbben egyetlen mai (helyi idő szerinti) negyedóra sincs.
Jellemzően szintén beragadt adatlekérés áll mögötte.

### Konfigurációs hiba induláskor

- `invalid slug ...` — valamelyik `input_number` kulcsban ékezetes betű van. A kulcs
  csak `a–z`, `0–9` és `_` karaktert tartalmazhat; a `name:` viszont nyugodtan lehet
  ékezetes.
- `Invalid config for [template]` a `trigger:` kulcsnál — 2024.10-nél régebbi Home
  Assistant, lásd a *Követelmények* részt.

---

## Testreszabás

### MNB árfolyam használata EKB helyett

Az MVM az **MNB** napi hivatalos középárfolyamával számol, a csomag viszont az **EKB**
referencia-árfolyamot használja. Az eltérés tizedszázalékos nagyságrendű, de ha pontos
számlaellenőrzést szeretnél:

1. írd be kézzel az MNB árfolyamot az `input_number.d_arfolyam_kezi` mezőbe,
2. a `d_tarifa.yaml`-ban írd át az `fx` változót úgy, hogy csak a kézi helpert
   olvassa. Az `fx` egyetlen `if`/`else` kifejezés, tehát nem egy sort kell
   kikommentelni, hanem az egészet lecserélni erre:

```yaml
fx: "{{ states('input_number.d_arfolyam_kezi') | float(395) }}"
```

### Az „olcsó” küszöb hangolása

A `binary_sensor.d_tarifa_olcso` az `input_number.d_a1_referencia` értékéhez
hasonlítja az aktuális bruttó árat. A küszöb menet közben, újraindítás nélkül
állítható — a szenzor azonnal újraszámol.

A helper `initial:` nélkül a létrehozásakor a `min` értéken, azaz **0-n** áll. Ezért a
`0` küszöböt a csomag szándékosan „nincs beállítva”-ként kezeli, és a binary_sensor
ilyenkor `Nem érhető el` — különben csendben „0 Ft/kWh alatt olcsó”-t számolna, tehát
akkor is `Ki` lenne, amikor az ár 43 Ft/kWh. Írd be a küszöböt (pl. `70.1`), és a
szenzor azonnal életre kel.

### Frissítési gyakoriság

`scan_interval: 900` (energy-charts) és `scan_interval: 3600` (árfolyam). A számított
szenzorok emellett minden negyedóra fordulóján, HA-indításkor, és bármelyik helper
módosításakor újraszámolnak.

---

## Miért dátumtartományt kérünk az API-tól?

A rövid `?bzn=HU` alak mindig a **mai** napot kéri, és `404 no content available`
hibával elszáll, ha a HU zónára még egyetlen negyedóra sincs publikálva. Ilyenkor a
`sensor.hupx_arak` `unavailable` lesz, és minden lekérdezésnél három hiba kerül a
naplóba:

```
REST request to https://api.energy-charts.info/price?bzn=HU returned status 404
REST result could not be parsed as JSON
Template variable error: 'value_json' is undefined
```

Ezért a csomag `resource_template`-tel **tegnaptól holnapig** kér adatot:

```
https://api.energy-charts.info/price?bzn=HU&start={{ (now() - timedelta(days=1)).strftime('%Y-%m-%d') }}&end={{ (now() + timedelta(days=1)).strftime('%Y-%m-%d') }}
```

Így a válasz akkor is `200`, ha a mai nap hiányzik — csak rövidebb tömb jön, és a
napló tiszta marad. **Az árat ettől nem találjuk ki:** az `eur_mwh` frissesség-
ellenőrzése (30 perc) továbbra is gondoskodik róla, hogy a tegnapi 23:45-ös ár ne
szivárogjon át mai árként, tehát a szenzorok helyesen `Nem érhető el` állapotban
maradnak.

A `resource_template` minden frissítés előtt újrarenderelődik, tehát a dátumok
maguktól továbblépnek éjfélkor — nem kell újraindítás.

> A dátumos végpont az API-nál **nincs cache-elve** (a rövid alakkal ellentétben), és
> gyors egymásutánban `429 Too Many Requests`-et ad. A 15 perces `scan_interval`
> (óránként 4 kérés) bőven a limit alatt van, de ne vidd lejjebb meggondolatlanul.
> A `value_template` a nem-JSON válaszokat (404, 429, karbantartási HTML) `0`-ként
> kezeli, hibadobás helyett.

---

## A kiesés utólagos pótlása

Amikor az adatforrás pótolja a hiányzó napot, a szenzorok maguktól helyreállnak — de
a kiesés ideje **lyukként marad a grafikonon**. Ezt tölti ki a
[`tools/d_tarifa_backfill.py`](tools/d_tarifa_backfill.py): visszamenőleg
kiszámolja a hiányzó órák árát az utólag publikált day-ahead adatból, és beírja a
Home Assistant hosszú távú statisztikáiba a `recorder/import_statistics` websocket
paranccsal.

### Mit tud és mit nem

|  | |
|---|---|
| ✅ **Statisztikák** (`statistics` tábla, órás átlag/min/max) | Ezt használja a `statistics-graph` kártya és a hosszabb időtávra zoomolt Előzmények nézet. A lyuk ott eltűnik. |
| ❌ **Nyers állapot-történet** (`states` tábla) | A Home Assistant semmilyen támogatott módon nem engedi visszamenőleg írni, így a rövid időtávú Előzmények nézetben a kiesés `Nem érhető el` sávként megmarad. |

A package tartalmaz egy `shell_command`-ot és egy automatizálást, ami elindítja a
scriptet, amikor az ár-szenzor kiesés után visszatér (`unavailable` → érték, 2 perc
stabilitás után), plusz **6 óránként** hálóként. A script maga dönti el, mit kell
pótolni, ezért a fölösleges futás nem kerül semmibe.

A script a Home Assistant saját konténerében fut, és websocket kliensnek az
`aiohttp`-t használja — az a HA-ban mindig ott van, **nem kell semmit telepíteni**.
Ez az egyetlen támogatott futtatási mód: a HA-t így mindig a loopbackon
(`http://127.0.0.1:8123`) éri el, nem kell hozzá se hosztnév, se LAN IP. Ha a HA
nem a 8123-as porton figyel, a `shell_command` sorát írd át a
`d_tarifa.yaml`-ban.

Három lépés kell hozzá, különben ez a rész nem csinál semmit:

1. Másold a `tools/d_tarifa_backfill.py` fájlt a `/config/tools/` mappába.
2. Hozz létre egy hosszú élettartamú tokent (**profil → Biztonság → Hosszú
   élettartamú hozzáférési tokenek**), és tedd egyetlen sorként a
   `/config/.d_tarifa_token` fájlba. **Adminisztrátori jogú felhasználóé kell
   legyen**, mert a `recorder/import_statistics` websocket parancs `require_admin` —
   egy sima felhasználó tokenjével a bejelentkezés még sikerül, csak az írás bukik el.
3. Kapcsold be az `input_boolean.d_tarifa_auto_backfill` helpert.

> **Miért fájlból jön a token?** A `shell_command` nem nulla visszatérési érték esetén
> a **teljes parancsot beleírja a naplóba** — parancssori argumentumként a token
> kiszivárogna. A `.d_tarifa_token` a `.gitignore`-ban van.

Először érdemes kézzel kipróbálni: **Fejlesztői eszközök → Műveletek →
`shell_command.d_tarifa_backfill`**, és megnézni a válaszban a `returncode`-ot.

| `returncode` | Jelentés |
|---|---|
| `0` | Lefutott (akár úgy is, hogy nem volt pótolnivaló) |
| `1` | Hiba — az automatizálás figyelmeztetést ír a naplóba |
| `2` | Az adatforrás még mindig nem ad adatot — nem hiba, csendben várunk |

Ha ténylegesen pótolt valamit, egy naplóbejegyzés készül `D tarifa` néven.

### Biztonságos ismételt futtatás

A script alapból csak azokat az órákat tölti fel, amelyekre **egyáltalán nincs még
statisztika** — a Home Assistant saját, állapotokból számolt (idővel súlyozott)
átlagát nem írja felül. Ezt a `--overwrite` kapcsolja ki. Emiatt fut nyugodtan
6 óránként is: bármilyen kiesés magától beheged, amint a forrás pótolja az adatot.

A még futó (le nem zárt) órát szándékosan kihagyja — azt a Home Assistant maga
számolja ki a valódi állapotokból.

A `shell_command` 60 másodperc után levágja a folyamatot és megállítja az
automatizálást, ezért a scriptnek van egy saját, ennél rövidebb időkerete
(`--budget 45`): inkább álljon le értelmes üzenettel. Az energy-charts dátumos
végpontjának `429`-eire legfeljebb kétszer, 5-5 másodperc szünettel újrapróbálkozik
— de csak akkor, ha az újrapróbálkozás belefér a hátralévő időkeretbe.

### A pótolt adat megjelenítése

A hosszú távú statisztika `statistics-graph` kártyán látszik biztosan:

```yaml
type: statistics-graph
title: D tarifa bruttó energiadíj
entities:
  - sensor.d_tarifa_brutto_energiadij
stat_types:
  - mean
  - min
  - max
period: hour
days_to_show: 7
```
