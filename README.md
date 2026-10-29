# ForældreIntra (SkoleIntra) i Home Assistant

Custom component, der henter **lektiebog, ugeplan, beskeder, kontaktbog og skolekalender** fra
ForældreIntra pr. barn, opdager nyt løbende og sender et event, som dine automatiseringer kan
lave mobilnotifikationer ud fra.

Skrevet til Vinderød Privatskole (`https://vinderoedprivatskole.m.skoleintra.dk`), men virker for
alle skoler på `*.m.skoleintra.dk`.

> **Vigtigt.** Koden er testet mod syntetiske sider, der er bygget ud fra fire uafhængige open
> source-projekter, som kører mod rigtige skoler. Den er **ikke** kørt mod Vinderød Privatskoles
> server, fordi den ikke kunne nås fra det miljø, koden blev skrevet i. Første skridt er derfor
> at køre `tools/probe.py` (se [Trin 1](#trin-1--test-mod-skolen-5-min)). Se også
> [Hvad er verificeret?](#hvad-er-verificeret-og-hvad-skal-du-selv-teste).

---

## 1. Undersøgelse: hvilke datakilder findes der?

| Kilde | Hvad dækker den | Pålidelighed | Vedligehold | Vurdering |
|---|---|---|---|---|
| **Officielt API fra itslearning** | – | – | – | **Findes ikke offentligt.** Der er hverken dokumentation eller udvikleradgang. |
| **RSS / iCal-abonnement** | – | – | – | **Ikke fundet.** Ingen af projekterne eller skolernes vejledninger nævner et feed. Kalenderen findes kun som et internt JSON-endpoint (nedenfor). |
| **ForældreIntra-appen** ("ParentIntra 2") | Det samme som web | Lukket | itslearning | Lukket, udokumenteret API. Ikke brugt. |
| **Mobilsiden `*.m.skoleintra.dk`** (den, du logger ind på) | Alt | God. Samme sider som browseren | itslearning | **Det er den, vi bruger.** Login med brugernavn/kode. Data ligger som HTML eller som JSON indlejret i siderne. |
| [CagosDk/ha-foraeldreintra](https://github.com/CagosDk/ha-foraeldreintra) (+ forks [SalikLP](https://github.com/SalikLP/ha-foraeldreintra), [kernest](https://github.com/kernest/ha-foraeldreintra)) | Lektiebog + ugeplan, Lovelace-kort | Brugt af HA-brugere | Aktiv (okt. 2026) | Eneste eksisterende HA-integration. **Mangler beskeder, kontaktbog, kalender og notifikationer om nyt.** Polling fast hver 60. minut. |
| [svalgaard/fskintra](https://github.com/svalgaard/fskintra) | Forside, beskeder, lektier, ugeplan, dokumenter, billeder → e-mail | Meget moden (siden 2011) | Aktiv (rettelse maj 2026) | Python 2 og e-mail-baseret. God reference for login (inkl. UNI-Login) og beskeder. |
| [fezdk/pyskoleintra](https://github.com/fezdk/pyskoleintra) | Næsten alt, inkl. **kontaktbog** og **kalender** | Ny (15 commits) | Aktiv (24. okt. 2026) | Python 3, bredest dækning. Kun almindeligt login. Ikke på PyPI. |
| [CavaleriDK/skoleintra](https://github.com/CavaleriDK/skoleintra) | Kalender, skema, ugeplan (Node.js) | OK | Seneste ændring 2024 | Bedste dokumentation af ugeplanens JSON og af skolens bot-beskyttelse. |
| [jona799t/SkoleintraSDK](https://github.com/jona799t/SkoleintraSDK) (PyPI `skoleintra`) | Kun ugeplan | – | Død siden 2022 | Ikke brugbar. |
| Aula | – | – | – | Ikke relevant. Vinderød bruger SkoleIntra. |

**Anbefaling:** En samlet custom component, der logger ind på mobilsiden med en almindelig
session og henter de interne endpoints, som tre af projekterne uafhængigt af hinanden har
verificeret. Det er den eneste måde at få alle fem områder med. Jeg har ikke bygget videre på
CagosDk-integrationen, fordi den dækker to ud af fem områder og ikke har ændringsdetektion. Den
bruger dog de samme endpoints til lektier og ugeplan. Hvis du kun vil have lektier og et pænt
lektiekort, er den et fint alternativ.

### Endpoints (pr. barn, `<barn>` = `/parent/<id>/<navn>`)

| Område | URL | Format | Bekræftet af |
|---|---|---|---|
| Login | `/Account/IdpLogin` (UserName, Password, RoleType=Parent) → SAML-formular → `/parent/<id>/<navn>/Index` | HTML-formularer | fskintra, pyskoleintra, CagosDk, CavaleriDK |
| Lektiebog | `<barn>item/weeklyplansandhomework/diary` → `…/diary/notes/<id>` (+ `/NextMonth`). Alternativt `<barn>/diaries/list` | HTML (`ul.sk-list`, tabel FAG/LEKTIER eller fritekst) | fskintra, CagosDk, pyskoleintra |
| Ugeplan | `<barn>item/weeklyplansandhomework/list` → `…/item/class/<uge>-<år>` | JSON i `#root[data-clientlogic-settings-WeeklyPlansApp]` | CagosDk, CavaleriDK, pyskoleintra |
| Beskeder | `<barn>/messages/conversations` (+ `…/loadmessagesforselectedconversation` for brødtekst) | JSON i `data-clientlogic-settings-*` | fskintra, pyskoleintra |
| Kontaktbog | `<barn>/contactbook/notes` | HTML (`div.sk-contactbook-note-container`) | pyskoleintra (kun ét projekt) |
| Kalender | `<barn>/calendareventsource/SchoolEvents?departmentIds=[0]&start=<unix>&end=<unix>` | Ren JSON | pyskoleintra, CavaleriDK |

Bemærk den manglende `/` før `item/` i lektiebog- og ugeplan-URL'erne. Sådan genererer serveren
dem selv.

### Hvad kan *ikke* hentes automatisk?

- **MitID.** Hvis skolen kræver MitID for forældre, kan ingen integration logge ind automatisk.
  Integrationen opdager det, opretter et "Reparationer"-punkt i HA og stopper i 2 timer ad gangen.
  Bedste alternativ: se om ForældreIntra kan sende **e-mailnotifikationer** (personlige
  indstillinger). Brug derefter HA's indbyggede [IMAP-integration](https://www.home-assistant.io/integrations/imap/),
  som sender et event pr. ny mail. Det er ikke undersøgt for Vinderød.
- **"Bekræft kontaktoplysninger".** Skolen beder om det med jævne mellemrum. fskintra klikker
  automatisk "Bekræft", men det gør denne integration bevidst ikke på dine vegne. Den opretter et
  reparationspunkt, og så logger du ind i en browser og bekræfter.
- **UNI-Login.** Det er understøttet som "best effort" (brugernavn/kode på `login.emu.dk`, samme
  flow som fskintra), men det er ikke testet. Forældre bruger normalt skolens eget login.
- **Personlige kalenderaftaler/samtaler.** Kun `SchoolEvents` (skolens kalender) er kendt og brugt.
- **Afsendelse** (svar i kontaktbog, sygemelding). Integrationen er skrivebeskyttet med vilje.

---

## 2. Installation (fra nul til kørende)

### Trin 1 – Test mod skolen (5 min)

Gøres på en hvilken som helst computer med Python 3.12+.

**Windows (PowerShell, ikke som administrator):**

1. Installer Python: `winget install Python.Python.3.13` (eller hent det fra
   [python.org](https://www.python.org/downloads/windows/)). Luk PowerShell og åbn den igen bagefter.
   Hvis du får beskeden *"Python blev ikke fundet … Microsoft Store"*, er Python ikke installeret
   endnu. Det er kun en genvej i Windows.
2. Hent koden: på GitHub → *Code* → *Download ZIP*, og pak den ud i fx `Downloads`.
3. Kør (brug `py`, ikke `python3`, på Windows):

```powershell
cd $HOME\Downloads\Foraeldreintra-skoleintra-integration   # mappen fra ZIP-filen
py -m pip install aiohttp beautifulsoup4 tzdata
py tools\probe.py https://vinderoedprivatskole.m.skoleintra.dk
```

**Mac/Linux:**

```bash
git clone https://github.com/myschetzky/Foraeldreintra && cd Foraeldreintra
pip install aiohttp beautifulsoup4 tzdata
python3 tools/probe.py https://vinderoedprivatskole.m.skoleintra.dk
```

Brugernavn og adgangskode spørges interaktivt. Adgangskoden vises ikke, mens du skriver.
Alternativt: `--secrets sti/til/secrets.yaml`.

Du får en rapport som denne (fra testserveren):

```
Login OK. Børn: Storebror (/parent/1111/Storebror), Lillesøster (/parent/2222/Lilles%C3%B8ster)
=== Storebror ===
  lektiebog   OK  5 lektier, datoer ['2026-10-27', '2026-10-28', '2026-11-02'], fag [...]
  ugeplan     FEJL [LayoutError] HTTP 404 … (funktionen findes måske ikke på skolen)
  beskeder    OK  2 tråde, 1 ulæste, 2 med brødtekst
  kontaktbog  OK  2 noter, seneste dato 26. okt. 2026
  kalender    OK  2 begivenheder
```

Rapporten viser kun antal, fag og datoer. `--verbose` viser indhold lokalt. `--dump mappe/` gemmer
de rå sider, så en parser kan rettes. De indeholder persondata, så del dem ikke offentligt.

**Brug resultatet:** Er der `FEJL [LayoutError]` på et område, du forventer virker, så er sidens
struktur anderledes på jeres skole. Se [Fejlsøgning](#6-fejlsøgning).

### Trin 2 – Installer komponenten

**HACS:** HACS → ⋮ → *Custom repositories* → `https://github.com/myschetzky/Foraeldreintra`,
type *Integration* → installer "SkoleIntra (ForældreIntra)" → genstart HA.

**Manuelt:** Kopiér `custom_components/skoleintra/` til `/config/custom_components/skoleintra/`
(fx via Samba- eller File editor-add-on'et) → genstart HA.

### Trin 3 – Log ind

*Indstillinger → Enheder og tjenester → Tilføj integration → "SkoleIntra"*. Adressen er udfyldt
med Vinderød. Indtast ForældreIntra-brugernavn og -adgangskode, og vælg login-type
"Almindeligt". Integrationen tester login og finder børnene.

**Med `secrets.yaml` i stedet for UI:** Tilføj til `/config/secrets.yaml`:

```yaml
skoleintra_username: "dit-brugernavn"
skoleintra_password: "din-adgangskode"
```

og til `configuration.yaml` (se `examples/configuration.yaml.example`):

```yaml
skoleintra:
  - school_url: https://vinderoedprivatskole.m.skoleintra.dk
    username: !secret skoleintra_username
    password: !secret skoleintra_password
```

Ved genstart importeres det som en almindelig integration. Ændrer du koden i `secrets.yaml`,
opdateres integrationen ved næste genstart.

> **Om sikkerhed:** Uanset metode gemmer Home Assistant loginoplysningerne i
> `/config/.storage/core.config_entries`, sådan som alle integrationer med login gør. `secrets.yaml`
> holder dem ude af `configuration.yaml` (som man deler/committer), men ikke ude af HA's
> backups. Adgangskoden sendes kun over HTTPS og kun til `*.skoleintra.dk` (eller UNI-Login, hvis
> valgt). Den skrives aldrig i loggen eller i diagnostikfilen.

### Trin 4 – Indstil pr. barn

*SkoleIntra → Konfigurer*:

| Felt | Anbefalet for jer |
|---|---|
| `kilde_Storebror` | **Lektiebog** |
| `kilde_Lillesøster` | **Ugeplan** |
| Jeres egne navne | Fx `Anna Jensen, Peter Jensen`, som de står i kontaktbogen. Så kan noter fra jer skelnes fra noter fra skolen. |
| Beskeder/kontaktbog | 15 min |
| Lektier/ugeplan | 60 min |
| Kalender | 6 timer |
| Stilletid | 22–6 (polling 4 gange sjældnere om natten) |

Valget af kilde styrer, hvilke sensorer der oprettes, og hvad "Lektier i dag" viser.

### Trin 5 – Dashboard og automatiseringer

- Dashboard: `examples/dashboard.yaml` (lektier pr. barn, beskeder, kontaktbog, kalender).
- Automatiseringer: `examples/automations.yaml`. Ret `notify.mobile_app_din_telefon` til din
  telefon. Begge filer er valideret mod Home Assistant 2026.2 i testene.

---

## 3. Hvad du får i Home Assistant

Én enhed pr. barn (navnet fra ForældreIntra), så data aldrig blandes:

| Entitet (eksempel) | Tilstand | Vigtige attributter |
|---|---|---|
| `sensor.storebror_lektier_i_dag` | antal | `lektier` (dato, fag, tekst, kilde, links), `markdown`, `lektiebog_status` |
| `sensor.storebror_lektier_naeste_skoledag` | antal | som ovenfor (springer weekend over) |
| `sensor.storebror_kommende_lektier` | antal | `lektier` (op til 40) |
| `sensor.lillesoster_ugeplan` | "Uge 44" | `denne_uge`, `næste_uge` (generelt + pr. dag), `ugens_opgaver_markdown` |
| `sensor.<barn>_ulaeste_beskeder` | antal ulæste | `seneste_emne`, `seneste_afsender`, `tråde` |
| `sensor.<barn>_kontaktbog` | dato for seneste note | `seneste_forfatter`, `seneste_tekst`, `noter` (med `from_us`) |
| `calendar.<barn>_skolekalender` | næste begivenhed | ture, fridage, møder (7 dage tilbage → 120 frem) |
| `sensor.skoleintra_status` | `ok` / `delvis fejl` / `fejl` | pr. barn og område: sidst OK, fejl, fejltype, næste kørsel |

Ugeplanens punkter for en bestemt dag tæller som "lektier" den dag, og ugens generelle punkter
vises som "Ugens opgaver". Store attributter gemmes ikke i historikken (recorder), så databasen
ikke vokser.

**Event `skoleintra_new_item`** sendes for hvert nyt eller ændret element:

```yaml
barn: Storebror
kategori: beskeder         # beskeder | kontaktbog | lektiebog | ugeplan | kalender
ændring: ny                # ny | ændret | mange (mere end 10 på én gang -> én samlet)
titel: Tur til Zoo fredag
tekst: "Husk madpakke og regntøj"
afsender: Mette Hansen (MH)
url: https://vinderoedprivatskole.m.skoleintra.dk/parent/1111/Storebror/messages/conversations
retning: til_os            # kun kontaktbog: til_os | fra_os | ukendt
```

Tjenesten `skoleintra.opdater` (valgfri `kategori`) henter med det samme.

---

## 4. Polling og belastning af skolen

Koordinatoren vågner hvert 5. minut, men laver kun HTTP-kald for de områder, der er "forfaldne":

| Område | Dag | Nat (22–6) | Kald pr. gang |
|---|---|---|---|
| Beskeder | 15 min | 60 min | 1 pr. barn + 1 pr. *ny* tråd (maks. 5) |
| Kontaktbog | 15 min | 60 min | 1 pr. barn |
| Lektiebog (Storebror) | 60 min | 4 t | 3 |
| Ugeplan (Lillesøster) | 60 min | 4 t | 2–3 |
| Kalender | 6 t | 6 t | 1 pr. barn |

Det giver omkring **20–25 sidevisninger i timen om dagen og 5–6 om natten**. Det svarer til en
forælder, der kigger ind et par gange. Derudover:

- Mindst 1 sekund mellem kald, ±10 % tilfældig spredning (ikke alle HA-installationer på samme sekund).
- Sessionen genbruges. Login sker kun ved opstart, eller når skolen har smidt sessionen ud.
- Brødtekst hentes kun for beskeder, vi ikke har set før.
- At hente en tråd ændrer ikke læst-status i ForældreIntra (verificeret af pyskoleintra).
- Med fejl i træk fordobles ventetiden (op til 4 timer).

---

## 5. Robusthed: hvad sker der når…

| Situation | Hvad integrationen gør |
|---|---|
| **Sessionen udløber** | Hvert kald tjekker, om svaret er login-siden. Hvis ja: log ind igen én gang og gentag kaldet. Det sker usynligt. |
| **Forkert/ændret adgangskode** | Prøver **én** gang. HA stopper polling og viser "Log ind igen" under Integrationer, så kontoen ikke låses af gentagne forsøg. |
| **Timeout / netværk / HTTP 5xx / vedligehold** | 30 sekunders timeout. Området markeres "midlertidig" fejl, med eksponentiel backoff. Andre områder kører videre. Sidst kendte data vises stadig. |
| **Login-fejl uden net** | Backoff 5 → 10 → 20 … op til 4 timer. Gamle data bevares. |
| **Skolens bot-beskyttelse** (HTTP 500-side) | Alle kald stoppes i 2 timer. |
| **Skolen ændrer layout** | Parsere tjekker for forventede elementer og melder `LayoutError` i stedet for "ingen lektier". Efter 3 fejl i træk oprettes et punkt under *Indstillinger → Reparationer*. Data og "set"-historik bevares, så der ikke kommer en storm af falske "nye" notifikationer, når det virker igen. |
| **MitID / bekræft kontaktoplysninger** | Reparationspunkt + pause i 2 timer. |
| **HA genstarter** | "Set"-listen gemmes i `.storage`. Beskeder, der kom mens HA var nede, giver stadig notifikation. Første opstart nogensinde er stille (ingen storm). |
| **Mange nye ting på én gang** (ny skoleårskalender) | Maks. 10 events pr. område pr. runde, plus ét samlet "mange"-event. En helt ny ugeplan giver ét event. |
| **Ukendt fejl i en parser** | Logges med stacktrace. Kun det område fejler. |

Overvåg selv med automatiseringen `skoleintra_overvaagning` (besked, hvis status er `fejl` i
2 timer).

---

## 6. Fejlsøgning

1. `sensor.skoleintra_status` → attributten `kategorier` viser præcis hvilket barn/område der fejler og hvorfor.
2. *Enheder og tjenester → SkoleIntra → ⋮ → Download diagnosticering*. Indeholder kun antal og fejl, ingen indhold.
3. Debug-log:
   ```yaml
   logger:
     logs:
       custom_components.skoleintra: debug
   ```
4. `python3 tools/probe.py … --dump sider/` og se på den side, der fejler. Selektorerne står samlet i
   `custom_components/skoleintra/parsers.py` med kommentarer om, hvor de kommer fra.

---

## Hvad er verificeret, og hvad skal du selv teste?

**✅ Verificeret (automatiske tests, 39 stk., Home Assistant 2026.2.3):**
- Parsere for alle fem områder mod syntetiske sider, der følger de selektorer og JSON-felter, som de nævnte projekter bruger mod rigtige skoler.
- Login-flowet mod en lokal falsk ForældreIntra-server: formular → SAML → børneliste, genlogin ved udløbet session, forkert kode (kun ét forsøg), MitID og "bekræft kontaktoplysninger" (manuel handling), bot-blokering, timeout.
- HA-delen: config flow, YAML-import fra `secrets.yaml`, entiteter pr. barn, kildevalg pr. barn, events (én notifikation selv om en besked ses under begge børn, én pr. ny ugeplan), layoutfejl bevarer data, auth-fejl starter re-autentificering, login-backoff.
- Eksempel-automatiseringerne valideres af HA, og dashboard-skabelonerne renderes mod rigtige entiteter.
- `tools/probe.py` køres ende-til-ende mod den falske server.

**⚠️ Skal testes mod Vinderød (kør `tools/probe.py` først):**
- **Login:** at skolen bruger almindeligt login (ikke kun MitID/UNI-Login), og at `RoleType=Parent` accepteres.
- **Lektiebog for 8.v:** hvilken af de to URL-varianter skolen bruger, og om noterne er en FAG/LEKTIER-tabel eller fritekst. Datoen i lektiebogen tolkes som den dag, lektien skal være lavet til. Tjek at det passer.
- **Ugeplan for 4.s:** at ugeplanen er JSON-varianten med dagsplaner. Hvis læreren kun skriver i den generelle del, kommer alt under "Ugens opgaver" og ikke under "Lektier i dag".
- **Kontaktbog:** kun ét projekt (pyskoleintra) dokumenterer selektorerne. Tjek at noter fra begge retninger kommer med, og udfyld "egne navne".
- **Beskeder:** om skolen bruger samtalevisningen (`conversations`) eller den gamle indbakke (understøttet, men mindre testet).
- **Kalender:** at `SchoolEvents` returnerer begivenheder for klasserne. Personlige aftaler er ikke med.
- **Børnenes navne** i entitets-id'erne: ret `storebror`/`lillesoster` i dashboard og automatiseringer.
- **E-mailnotifikationer** fra ForældreIntra som fallback ved MitID er ikke undersøgt.

---

## Udvikling

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install pytest-homeassistant-custom-component beautifulsoup4
pytest
```

Struktur: `api.py` (HTTP, login, sessioner), `parsers.py` (alle selektorer), `tracker.py`
(ændringsdetektion), `coordinator.py` (polling, backoff, events). De tre første importerer ikke
Home Assistant og kan bruges selvstændigt (som i `tools/probe.py`).

Tak til forfatterne af fskintra, pyskoleintra, ha-foraeldreintra og CavaleriDK/skoleintra. Deres
arbejde er grundlaget for endpoint-kendskabet her. Ingen kode er kopieret. Licens: MIT.
Uofficielt og ikke tilknyttet itslearning.
