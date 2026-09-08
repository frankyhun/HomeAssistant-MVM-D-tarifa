# MVM D (dinamikus) tarifa csomag

Home Assistant *package*, amely a magyar **MVM Next D (dinamikus) árszabás** aktuális
negyedórás árát számolja ki, natív `rest` és `template` integrációval — egyedi
komponens vagy HACS nélkül.

Fájl: [`packages/d_tarifa.yaml`](packages/d_tarifa.yaml)

![screenshot](./screenshot.jpg)

---

## Mit csinál?

Negyedóránként lekéri a magyar zóna day-ahead tőzsdei villamosenergia-árát, átváltja
forintra, hozzáadja a hálózati díjakat és az áfát, és ebből szenzorokat képez:

- mennyi **most** az áram ára Ft/kWh-ban (nettó és bruttó),
- mennyi a **mai átlag**, minimum és maximum, és mikor a legolcsóbb, illetve a
  legdrágább negyedóra,
- **olcsó-e most** az áram egy általad megadott küszöbhöz képest — bináris szenzor,
  automatizáláshoz.

## Számítás

```
nettó [Ft/kWh] = day-ahead ár [EUR/MWh] × EUR_HUF / 1000
                 + átviteli forgalmi díj + elosztói forgalmi díj

bruttó         = nettó × áfa szorzó
```

| Adat | Forrás | Frissítés |
|---|---|---|
| Negyedórás day-ahead ár (HU zóna, EUR/MWh) | [api.energy-charts.info](https://api.energy-charts.info/) (Fraunhofer ISE) | 15 percenként |
| EUR/HUF árfolyam | [api.frankfurter.dev](https://api.frankfurter.dev/v1/latest?from=EUR&to=HUF) (EKB referencia-árfolyam) | óránként |

Egyik sem igényel kulcsot vagy regisztrációt. Ugyanezt a forráspárost használja a
holadelej.hu is, így az értékek összevethetők. Az MVM az **MNB** középárfolyamával
számol, a csomag az **EKB**-ével — az eltérés tizedszázalékos.

A csomag inkább `Nem érhető el` állapotot ad, mint téves árat: ha az aktuális
negyedórára nincs publikált ár, ha az adat 30 percnél régebbi, vagy ha a helperek
nincsenek beállítva.

> **Forrásmegjelölés.** Az ár adat eredeti forrása a Bundesnetzagentur | SMARD.de,
> **CC BY 4.0** licenc alatt (<https://creativecommons.org/licenses/by/4.0/>),
> változtatás nélkül. A csomag ezért eltárolja a válasz `license_info` mezőjét a
> `sensor.hupx_arak` attribútumai között — ezt **ne távolítsd el**.

---

## Telepítés

Kell hozzá **Home Assistant 2024.10 vagy újabb** (a modern `trigger:` / `triggers:`
szintaxis miatt) és kimenő internetkapcsolat a fenti két API felé.

**1.** Másold a `d_tarifa.yaml` fájlt a `/config/packages/` mappába (ha a `packages`
mappa még nem létezik, hozd létre).

**2.** A `configuration.yaml`-ban a `homeassistant:` blokk alá vedd fel a `packages`
sort — ha már ott van, ugorj tovább:

```yaml
homeassistant:
  packages: !include_dir_named packages
```

**3.** **Fejlesztői eszközök → YAML → Konfiguráció ellenőrzése**, majd ha hibátlan:
**Újraindítás**. (Home Assistant OS-en parancssorból: `ha core check`.)

**4.** Állítsd be az öt `input_number` helpert a **Beállítások → Eszközök és
szolgáltatások → Helperek** alatt, lásd a következő szakaszt. A pontos díjtételek a
saját MVM-számládon szerepelnek, érdemes onnan átvenni őket.

> **Amíg a helpereket nem állítod be, az ár-szenzorok szándékosan `Nem érhető el`
> állapotban maradnak. Ez nem hiba.** A helperekben nincs `initial:` érték — az minden
> újraindításkor felülírná a beírt értékeket —, ezért létrehozáskor a `min` értéken
> állnak: árfolyam = 200, áfa = 1.0, díjak = 0. Ezek önmagukban érvényes számok, így a
> szenzor magától *nem* jelezne hibát, csak csendben kb. 36%-kal alacsonyabb árat
> mutatna. A csomag ezért külön ellenőrzi, hogy tényleg beállítottad-e őket.

---

## Bemenetek

| Helper | Mit állít be | Ajánlott |
|---|---|---|
| `input_number.d_atviteli_forgalmi_dij` | Átviteli forgalmi díj (nettó) | `3.39` Ft/kWh |
| `input_number.d_elosztoi_forgalmi_dij` | Elosztói forgalmi díj (nettó) | `20.01` Ft/kWh |
| `input_number.d_afa_kulcs` | Áfa szorzó | `1.27` |
| `input_number.d_arfolyam_kezi` | EUR/HUF tartalék, ha az árfolyam-szenzor kiesik | `395` Ft |
| `input_number.d_a1_referencia` | Az „olcsó” küszöb: az A1 sávhatár feletti bruttó ár | `70.1` Ft/kWh |
| `input_boolean.d_tarifa_auto_backfill` | Automatikus statisztika-pótlás (lásd lent) | ki |

Mind menet közben, újraindítás nélkül állítható — a szenzorok azonnal újraszámolnak.

## Szenzorok

| Entitás | Leírás |
|---|---|
| `sensor.d_tarifa_netto_energiadij` | Az aktuális negyedóra **nettó** ára Ft/kWh. Attribútumok: `hupx_eur_mwh`, `arfolyam`, `negyedora_index`, `szamitva` |
| `sensor.d_tarifa_brutto_energiadij` | Ugyanez **bruttó** |
| `sensor.d_tarifa_mai_atlag_brutto` | A mai nap átlagos bruttó ára. Attribútumok: `minimum`, `maximum`, `legolcsobb_idopont`, `legdragabb_idopont` |
| `binary_sensor.d_tarifa_olcso` | `on`, ha a bruttó ár a küszöb alatt van. Amíg a küszöb `0` (nincs beállítva), **nem érhető el** — nem `off`. Attribútumok: `kuszob`, `brutto`, `szamitva` |
| `sensor.hupx_arak` | Nyers ár adat: a negyedórás árak darabszáma — `192` (tegnap + ma) vagy `288` (a másnapi árakkal). Attribútumok: `unix_seconds`, `price`, `unit`, `license_info` |
| `sensor.eur_huf_arfolyam` | Aktuális EKB EUR/HUF árfolyam |

**Működik-e?** A **Fejlesztői eszközök → Állapotok** alatt a `sensor.hupx_arak`
állapota `192` vagy `288`, az `unit` attribútuma `EUR / MWh`, a
`sensor.eur_huf_arfolyam` 350–420 közötti szám, a `sensor.d_tarifa_brutto_energiadij`
pedig értelmes Ft/kWh értéket mutat.

**Ha `Nem érhető el`:** először a helpereket nézd meg (4. lépés) — messze ez a
leggyakoribb ok. Utána azt, hogy a `sensor.hupx_arak` `unix_seconds` attribútuma
tartalmazza-e az aktuális negyedórát: ha az adatforrásból hiányzik a mai nap, a
következő lekérdezések maguktól helyreállítják a szenzorokat.

---

## Opcionális: a kiesés utólagos pótlása

Ha az adatforrásból hiányzott egy időszak, a szenzorok maguktól helyreállnak, de a
kiesés **lyukként marad a grafikonon**. A
[`tools/d_tarifa_backfill.py`](tools/d_tarifa_backfill.py) visszamenőleg kiszámolja a
hiányzó órák árát, és beírja a hosszú távú statisztikákba: a `statistics-graph`
kártyán és a hosszabb időtávú Előzményekben a lyuk így eltűnik. A nyers
állapot-történetet a Home Assistant nem engedi visszamenőleg írni, ott a kiesés
`Nem érhető el` marad.

A csomag tartalmaz hozzá egy `shell_command`-ot és egy automatizálást, ami akkor
indítja, amikor az ár-szenzor kiesés után visszatér, plusz 6 óránként hálóként.
Három lépés kell hozzá, különben ez a rész nem csinál semmit:

1. Másold a `tools/d_tarifa_backfill.py` fájlt a `/config/tools/` mappába.
2. Hozz létre egy hosszú élettartamú tokent (**profil → Biztonság**), és tedd
   egyetlen sorként a `/config/.d_tarifa_token` fájlba. **Adminisztrátori jogú
   felhasználóé kell legyen**, mert a `recorder/import_statistics` parancs
   `require_admin`. (A token azért fájlból jön, mert a `shell_command` hiba esetén a
   teljes parancsot a naplóba írja — argumentumként kiszivárogna.)
3. Kapcsold be az `input_boolean.d_tarifa_auto_backfill` helpert.

Kézzel is kipróbálható: **Fejlesztői eszközök → Műveletek →
`shell_command.d_tarifa_backfill`**. A script csak azokat az órákat tölti fel,
amelyekre még nincs statisztika, ezért nyugodtan futhat ismételten.
