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
| Negyedórás day-ahead ár (HU zóna, EUR/MWh) | [api.energy-charts.info](https://api.energy-charts.info/price?bzn=HU) (Fraunhofer ISE) | 15 percenként |
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
  Régebbi verzión írd vissza mind a három `trigger:` sort `platform:`-ra.
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
| `binary_sensor.d_tarifa_olcso` | `on`, ha az aktuális bruttó ár az A1 referenciaár alatt van |

### Nyers adat (REST)

| Entitás | Leírás |
|---|---|
| `sensor.hupx_arak` | A negyedórás árak darabszáma: `96` (csak a mai nap) vagy `192` (a másnapiakkal együtt). Attribútumok: `unix_seconds`, `price`, `unit`, `license_info` |
| `sensor.eur_huf_arfolyam` | Aktuális EKB EUR/HUF árfolyam |

### Helperek

`input_number.d_atviteli_forgalmi_dij`, `input_number.d_elosztoi_forgalmi_dij`,
`input_number.d_afa_kulcs`, `input_number.d_arfolyam_kezi`,
`input_number.d_a1_referencia`

---

## A működés ellenőrzése

**Fejlesztői eszközök → Állapotok**, majd:

1. `sensor.hupx_arak` állapota `96` vagy `192`.
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
2. a `d_tarifa.yaml`-ban az `fx` változóban kommenteld ki a `sensor.eur_huf_arfolyam`
   ágát.

### Az „olcsó” küszöb hangolása

A `binary_sensor.d_tarifa_olcso` az `input_number.d_a1_referencia` értékéhez
hasonlítja az aktuális bruttó árat. A küszöb menet közben, újraindítás nélkül
állítható — a szenzor azonnal újraszámol.

### Frissítési gyakoriság

`scan_interval: 900` (energy-charts) és `scan_interval: 3600` (árfolyam). A számított
szenzorok emellett minden negyedóra fordulóján, HA-indításkor, és bármelyik helper
módosításakor újraszámolnak.
