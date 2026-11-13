# Forever AH-Analytics

> **© Levo006 – Alle Rechte vorbehalten.** Der Code ist öffentlich einsehbar, damit die Website über GitHub Pages abrufbar ist. Nutzung, Veränderung und Weitergabe des Codes sind ohne ausdrückliche Erlaubnis nicht gestattet. Spielinhalte (Item-Namen, Icons) gehören Blizzard Entertainment.

Lokale Website zur Analyse der Auktionshauspreise in **WoW: Forever**: Preisverläufe, Kennzahlen, Drilldown bis zum einzelnen Snapshot, Vergleiche, Top-Movers und Deals.

* **Keine Abhängigkeiten:** nur Python ≥ 3.11 (Standardbibliothek). Chart.js liegt lokal unter `static/vendor/`.
* **Zwei Datenquellen:** Auctionator-Preisdatenbank aus dem Spielclient (sofort nutzbar) und die Battle.net API (live, stündlich, mit API-Schlüssel). Weitere Quellen lassen sich anschließen.
* **Schnell:** SQLite mit WAL, vorberechnete Zusammenfassungen, Antwort-Cache, gzip. Typische API-Antworten brauchen 1–30 ms.

---

## Schnellstart

```bat
cd C:\Users\Master\Desktop\wow_ah_analytics
start_demo.bat
```
Startet mit **Beispieldaten** (4 Auktionshäuser, 45 Tage, stündlich, 84 Items) auf <http://127.0.0.1:8766>. Beim ersten Start werden die Daten erzeugt (ca. 40 s).

```bat
start.bat
```
Importiert die **echten Auctionator-Daten** aus `G:\World of Warcraft\_forever_\WTF\Account\*\SavedVariables\Auctionator.lua` und startet die Website auf <http://127.0.0.1:8765>.

Ohne Batch-Dateien:

```bash
python run.py import-auctionator          # echte Daten importieren
python run.py serve                       # Website starten (Port 8765)
python run.py --db data/demo.sqlite demo  # Beispieldaten erzeugen
python run.py --db data/demo.sqlite serve --port 8766
python run.py metadata                    # Item-Namen/Icons nachladen (Wowhead, DE + EN)
python run.py metadata --until-done       # dito, wartet bei Drosselung und macht weiter
python run.py info                        # was ist in der Datenbank?
python -m unittest discover -s tests      # Tests
```

### Öffentliche Version (GitHub Pages)

Live: <https://levo006.github.io/wow-forever-ah-analytics/> · Code: <https://github.com/Levo006/wow-forever-ah-analytics>

Die öffentliche Seite ist ein **statischer Export**, den GitHub Pages kostenlos ausliefert. Alle Ansichten funktionieren dort: Suche, Filter, Top-Movers, Deals, Kategorien und die Analysen (Trend, Volatilität, Ausreißer, Tageszeit-/Wochentagsmuster) rechnet `static/static-api.js` direkt im Browser. Nicht verfügbar sind dort nur Import und Live-Sammler.

**Automatische Aktualisierung (stündlich, kostenlos auf GitHub):** Der Workflow `.github/workflows/update.yml` läuft etwa stündlich auf GitHubs Servern: Jeder Lauf startet nach ~55 Minuten selbst den nächsten (GitHubs eigener Zeitplan lässt Läufe oft ausfallen und dient nur als Reserve) – dein PC muss dafür nicht laufen:

1. Datenbank aus dem Release **„datenbank“** laden (beim allerersten Lauf: `seed/ah.sqlite.gz`)
   und Rezepte, Quests und Sets prüfen (`python run.py gamedata`: lädt nur neu, wenn älter als 7 Tage oder bei neuer Spielversion, sonst je eine Anfrage)
2. Auktionen aller Häuser über die Battle.net API holen, Namen neuer Items nachladen (DE + EN)
3. Verdichten (`python run.py compact`): Fraktionsmärkte Allianz/Horde/Neutral 7 Tage stündlich, danach Tageswerte bis 180 Tage; einzelne Häuser nur 2 Tage stündlich und 14 Tage insgesamt
4. Website bauen und direkt auf GitHub Pages veröffentlichen (ohne Commits – das Repository wächst nicht)
5. Erst danach fehlende Namen in kleinen Portionen über Wowhead ergänzen (gedrosselt, dauert einige Minuten)
6. Datenbank zurück ins Release legen

**Größen im Dauerbetrieb** (gemessen mit 10 Tagen simulierter Vollhistorie): Datenbank ~210 MB (gepackt ~42 MB), Website-Export ~110 MB (gemessen am 12.11.2026 mit der Live-Datenbank: 112 MB), wächst danach nur noch um ~1 MB pro Tag. In der Website-Historie sind die letzten 48 h stündlich, bis 7 Tage 4-Stunden-Mittel, davor Tageswerte (Einzelhäuser: 48 h stündlich, bis 7 Tage 6-Stunden-Mittel).

Manuell starten: `publish.bat` öffnet die Workflow-Seite → **Run workflow**. Die Zugangsdaten liegen als Repository-Secrets `BLIZZARD_CLIENT_ID` / `BLIZZARD_CLIENT_SECRET` (*Settings → Secrets and variables → Actions*), nie im Code. Hinweis: GitHub pausiert geplante Workflows, wenn im Repository 60 Tage lang nichts passiert – dann unter *Actions* einmal „Enable workflow“ klicken.

**Auctionator-Scans** fließen seitdem nicht mehr automatisch auf die öffentliche Seite (die Battle.net-Daten decken alle Häuser stündlich ab). Lokal funktionieren `start.bat` und der Import weiterhin. `python run.py publish` (Veröffentlichung über den Zweig `gh-pages`) bleibt als Ausweichweg, falls die Pages-Quelle wieder auf „Deploy from a branch“ gestellt wird.

**Einmalige Einrichtung** (erledigt): *Settings → Pages* → Source **„GitHub Actions“**; Secrets wie oben.

---

## Märkte: Allianz, Horde, Neutral

Oben rechts wählt man die Fraktion – **Allianz** (Standard), **Horde** oder **Neutral** (Schwarzmeerräuber: Beutebucht, Gadgetzan, Everlook). Jede Fraktion ist ein eigener Sammelmarkt aus den Auktionshäusern aller Server-Verbünde. „Einzelnes AH …“ filtert optional auf ein Haus. Blizzard liefert Häuser in Dreiergruppen (Allianz/Horde/Neutral eines Server-Verbunds, z. B. #110/#113/#116); die Seite nennt sie „Verbund A, B, C, D“.

In den Sammelmärkten ist **Min der typische Tiefstpreis**: je Haus die günstige Preisstufe (10 %-Perzentil der angebotenen Menge), davon der mengengewichtete Median über die Häuser. So verfälscht ein einzelnes Lockangebot den Preis nicht. Median, Mittel, Menge und Auktionen werden exakt aus allen Einzelauktionen berechnet.

**Item-Seite:** oben drei Reiter mit dem Preis je Fraktion, darunter direkt die Preise der Fraktion gesamt und ihrer vier Verbünde (★ = günstigstes Haus; Klick zeigt den Verlauf dieses Hauses). Alles Weitere ist aufklappbar (der Zustand wird gemerkt): Kennzahlen, Angebotsmenge, Tageszeit & Wochentag, alle Auktionshäuser im Vergleich, Ausreißer, Snapshots, Item-Details mit Tooltip. Unter „Anzeige ▾“ am Chart: Kennzahlen ein-/ausblenden und **Verbünde vergleichen** (Mindestpreis aller Verbünde der Fraktion in einem Chart).

## Herkunft & Verwendung (Rezepte, Quests, Sets)

Auf jeder Item-Seite zeigt der aufklappbare Abschnitt **„Herkunft & Verwendung“**:

* **Hergestellt aus:** Rezepte, die das Item herstellen, mit Zutaten, Stückpreisen und Materialkosten je Stück. Herstellbare Zutaten lassen sich aufklappen (z. B. Bronzebarren → Kupfer- und Zinnbarren → Erz).
* **Verwendet in · daraus wird …:** alle Rezepte, in denen das Item Zutat ist, nach Beruf gruppiert. Jede Zeile lässt sich weiter aufklappen (Kupfererz → Kupferbarren → Bronzebarren → Schmiedeitems …). Kreisläufe werden mit ↻ markiert und nicht weiter verfolgt; „Alles aufklappen“ öffnet bis zu 3 Ebenen.
* **Materialkosten, AH-Preis und Spanne** je Stück für die gewählte Fraktion (typischer Tiefstpreis des Fraktionsmarkts; Spanne = AH-Preis nach 5 % Gebühr minus Materialkosten). Fehlende Preise stehen als „kein Preis“ da (meist Händlerware wie Faden oder Phiolen), die Kosten dann als „unvollständig“. Optional **„günstigster Weg“**: Zutaten, die sich billiger selbst herstellen lassen, werden mit ihren Herstellkosten gerechnet.
* **Questbelohnung:** Quests, die das Item als Belohnung geben – mit Gebiet, Typ (Dungeon, Schlachtzug, Elite, PvP), Fraktion, Klassen, „täglich/wiederholbar“ und „zur Wahl“. Bei Wahlbelohnungen stehen alle Alternativen mit AH-Preis daneben, ★ markiert die wertvollste. Quests der anderen Fraktion stehen hinten; der Titel verlinkt auf Wowhead.
* **Teil eines Sets:** alle Teile mit AH-Preis, die Set-Boni und die Summe aller angebotenen Teile.
* Filter nach Beruf (für Rezepte); jeder Eintrag führt zur Item-Seite. Items, die nie im AH auftauchen (z. B. Rüstung, die nur hergestellt wird, oder Questbelohnungen), bekommen eine Seite nur mit Herkunft/Verwendung.

**Quelle:** die offizielle Battle.net Static API für Forever (`static-forever-eu`: `/data/wow/profession/index` → `…/skill-tier/{id}` → `/data/wow/recipe/{id}`), deutsch. Stand 13.11.2026 (Build `static-1.60.1_70399`): **2486 Rezepte in 12 Berufen** (Schneiderei, Kochkunst, Lederverarbeitung, Kürschnerei, Verzauberkunst, Bergbau, Schmiedekunst, Ingenieurskunst, Angeln, Alchimie, Kräuterkunde, Erste Hilfe) mit 2701 beteiligten Items; Abruf ~40 s. Begründung: offiziell, Forever-spezifisch, deutsch und über dieselben Zugangsdaten verfügbar. Client-Datenbanken (wago.tools/DB2) bräuchten eine Build-Zuordnung und eigene Spell-Logik, Wowhead ist gedrosselt.

**Quests und Sets** kommen aus derselben API (`/data/wow/quest/area|category/{id}` → `/data/wow/quest/{id}`, `/data/wow/item-set/{id}`): Stand 13.11.2026 **6857 Quests, davon 2167 mit Item-Belohnung** (2632 verschiedene Items), und **154 Sets**; Abruf ~100 s bzw. ~3 s. 196 Quests ohne Titel (Platzhalter) werden verworfen. Website-Datei `data/quests.json` ~430 KB (gzip ~100 KB).

**Nicht abgedeckt** (liefert die API nicht): welche Items eine Quest *verlangt* (Questziele), Beute von Gegnern und aus Dungeons, Händlerangebote, Behälterinhalte; benötigte Fertigkeitsstufe je Rezept, Lehrer/Fundort von Rezepten, Entzauberungs-Ergebnisse; Händlerpreise von Zutaten (nur AH-Preise). Diese Daten gäbe es in der QuestieDB (Forever-Release auf GitHub), deren Inhalte laut Lizenz-Hinweis aber im Zweifel „all rights reserved“ sind – deshalb nicht eingebaut. 180 Verzauberungen und 3 Ingenieursrezepte haben kein Ergebnis-Item (erscheinen mit ✦, ohne Preis). Verworfen werden 7 Rezepte ohne Namen (Platzhalter) und ein doppeltes „Kupfer verhütten“ unter einem namenlosen Beruf.

**Robustheit:** Rezepte liegen in der Datenbank (`professions`, `recipes`, `recipe_reagents`) und werden höchstens wöchentlich bzw. bei einem neuen Namespace-Build neu geladen. Ein neuer Abruf ersetzt den Bestand nur, wenn höchstens 2 % (mind. 5) der Rezept-Abrufe fehlschlagen und mindestens die Hälfte des bisherigen Bestands ankommt. Der Website-Export enthält eine Datei `data/recipes.json` (~490 KB, gzip ~120 KB) mit den aktuellen Fraktionspreisen der Rezept-Items (nur Preise der letzten 48 h); Bäume und Kosten rechnet `static/recipes-core.js` im Browser.

## Ansichten

| Ansicht | Inhalt |
|---|---|
| **Übersicht** | KPIs des gewählten Auktionshauses (Items, Marktwert, Menge, Auktionen, Ø Preisänderung), Marktwert-Verlauf, Kategorien, Gewinner/Verlierer, Deals, volatilste Items |
| **Suche** | Suche zweisprachig (deutscher **oder** englischer Item-Name, z. B. „Silberblatt“ = „Silverleaf“), umlaut-tolerant („konigsblut“ findet „Königsblut“), Vorschläge mit Icon ab dem ersten Buchstaben. Filter nach Name/ID, Klasse, Unterklasse, Qualität, Preisbereich (Gold), Menge, Ø Menge (Liquidität), Markt, Handelsware/Einzelitem. Sortierbare Spalten, 14-Tage-Sparkline, Seitenweise |
| **Kategorien** | Drilldown **Kategorie → Unterkategorie → Item → Snapshot** mit Marktwert und Ø Änderung je Ebene |
| **Item** | Preis-Chart (Min, 10 %-Perzentil, Median, Mittel, Max) für 24 h / 7 T / 30 T / Alles, Angebotsmenge als eigener Chart, Ausreißer markiert, Trend, Volatilität, 30-T-Tief/-Hoch, Tageszeit- und Wochentagsmuster, Häuservergleich, Liste aller Snapshots. Klick in den Chart öffnet den Snapshot |
| **Snapshot** | Alle Items eines Zeitpunkts mit Änderung zum vorherigen Snapshot; für ein Item die **Preisstaffel** (Menge je Preisstufe) inkl. Kosten für 20/100 Stück; Vor/Zurück-Navigation |
| **Vergleich** | Bis zu 8 Items in einem Chart, absolut oder **indexiert (Start = 100)**, wählbare Kennzahl, Kennzahlentabelle, **Korrelationsmatrix** der Preisänderungen |
| **Top-Movers** | Gewinner/Verlierer über 24 h oder 7 T, nach Mindestpreis oder Median, Mindest-Liquidität und Klasse filterbar |
| **Deals** | Items, deren aktueller Mindestpreis X % unter dem Ø 7 T / Ø 30 T liegt (Referenz Mindestpreis oder Median), mit Ersparnis pro Stück |
| **Daten** | Auctionator-Import per Knopf oder Datei-Upload (Drag & Drop), Battle.net-Status und manueller Abruf, Häuserliste, Import-Protokoll |

Der Markt wird oben rechts gewählt; Hell/Dunkel per ◐. Items kommen über „+ Vgl.“ in jeder Tabelle in den Vergleich.

### Kennzahlen – Definitionen

* **Min / 10 %-Perzentil / Median / Mittel / Max:** Stückpreise je Snapshot, mengengewichtet (ein 200er-Stapel zählt 200-mal).
* **Δ 24 h / Δ 7 T:** aktueller Mindestpreis ggü. dem letzten Snapshot vor 24 h bzw. 7 Tagen. Zeitfenster beziehen sich auf den jüngsten Snapshot des Hauses.
* **Volatilität:** Standardabweichung der Log-Renditen von Snapshot zu Snapshot, auf einen Tag skaliert; zusätzlich CV = Standardabweichung / Mittelwert (in Tabellen: 7-Tage-CV des Mindestpreises).
* **Trend:** lineare Regression auf ln(Preis) → % pro Tag und R². Bei R² < 0,1 oder < 0,25 %/Tag gilt der Verlauf als „seitwärts“.
* **Ausreißer:** Abweichung vom gleitenden 48-h-Median um mehr als 4 · MAD **und** mindestens 15 %.
* **Tageszeit-/Wochentagsmuster:** jeder Punkt geteilt durch den zentrierten 24-h- bzw. 7-Tage-Median (entfernt den Trend), gemittelt je Stunde (Ortszeit) bzw. Wochentag. Braucht stündliche Daten (≥ 2 Tage) bzw. ≥ 14 Tage Historie.
* **Marktwert:** Mindestpreis × angebotene Menge (Obergrenzen-Schätzung des Angebotswerts).
* **Deals:** aktueller Mindestpreis ≤ Referenz · (1 − Schwelle).

---

## Datenquellen

### Ergebnis der Prüfung (Stand 10.11.2026)

Die Battle.net **Game Data API deckt WoW: Forever ab**, allerdings nicht offiziell dokumentiert (Stand laut Community-Tests im Blizzard-API-Forum, Thread „WoW: Forever API: what works so far“, 5./6.11.2026):

* Eigene Namespaces: `dynamic-forever-{region}` (Auktionen, Rulesets) und `static-forever-{region}` (Items, Medien); Regionen eu, us, kr, tw.
* Forever hat **keine Realms**, sondern **Rulesets**. Die klassischen Endpunkte `/data/wow/connected-realm/{id}/auctions` und `/data/wow/auctions/commodities` liefern **404**.
* Auktionen: `/data/wow/ruleset/{id}/auctions/` (Schrägstrich am Ende nötig) listet 15 Häuser (je 5 × Allianz, Horde, Schwarzwasser); `/data/wow/ruleset/{id}/auctions/{haus}` liefert Auktionen, `…/{haus}/commodities` die Handelswaren.
* Alle Rulesets liefern dieselben Häuser mit identischen Auktionen → der Sammler fragt nur ein Ruleset ab.
* Rund 40 % der Item-IDs liefern beim Item-Endpunkt 404 → solche Items erscheinen als „Item #ID“ und werden nach 7 Tagen erneut versucht.

Da die API inoffiziell ist und sich ändern kann, ist **Auctionator die robuste Grundlage** (funktioniert ohne Schlüssel), die **Battle.net API die empfohlene Hauptquelle**, sobald ein API-Client angelegt ist: stündliche Snapshots aller Häuser mit Median/Mittelwert/Preisstaffel statt nur Tages-Mindestpreisen.

### 1. Auctionator (Spielclient)

Auctionator speichert pro Realm (in Forever: „PvE“, „PvP“) eine CBOR-kodierte Preisdatenbank in der `Auctionator.lua`. Pro Item und Tag: niedrigster und höchster gesehener Mindestpreis sowie die maximal gesehene Menge, Historie standardmäßig 21 Tage (Auctionator-Option „price_history_days“).

1. Im Spiel das Auktionshaus öffnen und einen **vollständigen Scan** in Auctionator ausführen.
2. `/reload` oder ausloggen – erst dann schreibt WoW die Datei.
3. `start.bat`, `python run.py import-auctionator` oder auf der Seite **Daten** „Jetzt importieren“ bzw. Datei hochladen.

Importe sind idempotent (gleicher Tag wird überschrieben). Ausrüstungsvarianten (`g:ID:ilvl`) werden zum Item zusammengeführt (Minimum der Preise, Summe der Mengen), Kampfhaustiere (`p:`) übersprungen. Tageswerte haben kein Median/Mittel und keine Tageszeitmuster.

**Item-Namen und Icons:** Auctionator speichert keine Namen. Nach jedem Import lädt das Tool Name, Qualität, Klasse/Unterklasse und Icon für neue Items aus der **Wowhead-Datenbank für WoW: Forever** (`wowhead.com/forever/de/item=ID&xml`, gedrosselt, dauerhaft zwischengespeichert – jedes Item nur einmal). Zusätzlich wird der **englische Name** geladen, damit die Suche beide Sprachen versteht (angezeigt wird der deutsche). Wowhead begrenzt die Zahl der Abrufe: Pro Import werden höchstens 300 Items geholt; `python run.py metadata --until-done` holt alles und wartet bei einer Sperre 20 Minuten. Manuell: `python run.py metadata`. Mit Battle.net-Zugang werden fehlende Items zusätzlich dort gesucht (`[metadata] source` in `config.toml`).

### Wann erscheinen neue Preise auf der Website?

| Schritt | Was passiert | Dauer |
|---|---|---|
| Scan im Spiel | Auctionator merkt sich pro Item den günstigsten Preis und die Menge **des Tages** (Vollscan oder Suche im Auktionshaus) | sofort |
| `/reload` / Ausloggen | WoW schreibt `Auctionator.lua` | sofort |
| `publish.bat` | Import → Item-Namen → Export → Push zu GitHub | ca. 1 Minute |
| GitHub Pages | Seite wird neu bereitgestellt; Browser speichern bis zu 10 Minuten zwischen | 1–10 Minuten |

Eine **selbst eingestellte Auktion** taucht also erst auf, wenn ein Scan sie danach erfasst hat **und** veröffentlicht wurde – theoretisch nach wenigen Minuten, praktisch beim nächsten `publish.bat`. Da Auctionator nur Tageswerte (günstigster Preis, maximale Menge) speichert, verändert sie den Wert nur, wenn sie der günstigste Preis ist oder die Tagesmenge erhöht. Mit der Battle.net API läuft das automatisch stündlich und umfasst alle Auktionen.

### 2. Battle.net API (live)

1. Unter <https://develop.battle.net/access/clients> einen Client anlegen.
2. `config.example.toml` nach `config.toml` kopieren, `client_id` und `client_secret` eintragen (oder Umgebungsvariablen `BLIZZARD_CLIENT_ID` / `BLIZZARD_CLIENT_SECRET`).
3. Starten:
   ```bash
   python run.py serve --collect      # Website + stündlicher Sammler in einem Prozess
   # oder getrennt:
   python run.py collect --loop       # nur Sammler
   python run.py collect              # einmaliger Snapshot (z. B. per Windows-Aufgabenplanung)
   ```

Der Sammler holt pro Haus Auktionen und Handelswaren, nutzt `Last-Modified` als Snapshot-Zeit (doppelte Snapshots werden übersprungen), verdichtet je Item zu Min/10 %/Median/Mittel/Max/Menge/Auktionen und speichert die Preisstaffel der letzten 48 Snapshots. Stückpreis = `unit_price` (Handelswaren) bzw. `buyout / quantity`; reine Gebotsauktionen werden ignoriert.

**Windows-Aufgabenplanung** (Alternative zu `--collect`): Aufgabe „stündlich“ mit Programm `python`, Argumente `run.py collect`, Starten in `C:\Users\Master\Desktop\wow_ah_analytics`.

### 3. Eigene Quelle anschließen

Eine Quelle muss nur drei Funktionen aus `ahanalytics/db.py` aufrufen (siehe `ahanalytics/sources/__init__.py`):

```python
hid = db.upsert_house(con, "meinequelle", ext_id, name, region="eu", faction="Horde", granularity="hour")
db.write_snapshot(con, hid, ts, "meinequelle", {item_id: {"min": …, "median": …, "quantity": …}}, ladders=None)
db.refresh_summary(con, [hid]); db.bump_data_version(con)
```
Einzelne Auktionen lassen sich mit `aggregate.aggregate_listings([(item_id, stückpreis, menge), …])` verdichten. Beispiele: `sources/auctionator.py` (Tagesdaten), `sources/battlenet.py` (Live), `sources/demo.py` (synthetisch).

---

## Architektur

```
 Auctionator.lua ──► sources/auctionator.py ─┐   (Lua-Parser + CBOR-Decoder)
 Battle.net API  ──► sources/battlenet.py  ──┼──► db.py (SQLite, WAL) ──► api.py ──► server.py ──► static/ (SPA)
 Beispieldaten   ──► sources/demo.py       ──┘        prices (Zeitreihe)      JSON      HTTP+gzip     Chart.js
                                                      item_summary (vorberechnet)        Cache
```

| Baustein | Wahl | Begründung |
|---|---|---|
| Speicher | **SQLite** (WAL, `WITHOUT ROWID`, PK = Haus/Item/Zeit) | Eine Datei, keine Installation, Zeitreihen eines Items liegen physisch zusammen → Range-Scans in Millisekunden. 15 Häuser × ~5 000 Items × 24 Snapshots ≈ 1,8 Mio. Zeilen/Tag sind für SQLite unproblematisch. DuckDB/Postgres wären erst bei Mehrbenutzerbetrieb oder Jahren an Rohdaten nötig. |
| Vorberechnung | `item_summary` nach jedem Import | Suche, Movers, Deals und Kategorien lesen nur diese Tabelle (eine Zeile pro Haus und Item) → konstant schnell. |
| Backend | Python-Standardbibliothek (`http.server`, `sqlite3`, `tomllib`) | Keine Abhängigkeiten, läuft mit dem vorhandenen Python. Threading-Server, eine DB-Verbindung pro Thread, LRU-Cache, der bei jedem Import über `data_version` ungültig wird. |
| Frontend | Vanilla-JS-SPA (Hash-Routing) + Chart.js 4.4.1 (lokal) | Kein Build-Schritt, alle Ansichten sind per URL teil- und verlinkbar; lange Zeiträume werden serverseitig auf ≤ 800 Punkte verdichtet. |

**Tabellen:** `houses` (Auktionshäuser/Realms je Quelle), `items` (Metadaten), `professions`/`recipes`/`recipe_reagents` (Rezepte), `quests`/`quest_rewards`, `item_sets`/`item_set_items`, `snapshots` (ein Eintrag pro Haus und Zeitpunkt), `prices` (Kennzahlen pro Haus/Item/Zeitpunkt, Kupfer), `ladders` (Preisstaffel, nur jüngste Snapshots), `item_summary`, `import_log`, `meta`.

**API** (alle `GET`, JSON): `/api/houses`, `/api/overview`, `/api/classes`, `/api/search`, `/api/item/{id}`, `/api/item/{id}/history`, `/api/item/{id}/analysis`, `/api/item/{id}/snapshots`, `/api/snapshot/{haus}/{ts}`, `/api/compare`, `/api/movers`, `/api/deals`, `/api/status`, `/api/recipes`, `/api/quests`, `/api/import/log`; `POST /api/import/auctionator` (Datei im Body), `POST /api/import/auctionator/auto`, `POST /api/collect`.

**Projektstruktur**

```
ahanalytics/
  __main__.py      Kommandozeile (serve, demo, import-auctionator, collect, recipes, gamedata, refresh, info)
  config.py        config.toml + Umgebungsvariablen
  db.py            Schema, Upserts, item_summary
  aggregate.py     Auktionen → Kennzahlen (mengengewichtete Perzentile)
  export.py        statischer Export (verdichtete Historie, spaltenweise JSON)
  recipes.py       Berufe/Rezepte von Battle.net (wöchentlich), Export-Struktur
  gamedata.py      Quests (Belohnungen) und Item-Sets von Battle.net (wöchentlich)
  publish.py       Veröffentlichung auf den Zweig gh-pages
  metadata.py      Item-Namen/Icons: Battle.net (DE+EN), sonst Wowhead (Forever)
  metrics.py       Volatilität, Trend, Ausreißer, Saisonalität, Verdichtung
  api.py           Endpunkte
  server.py        HTTP-Server, Cache, Hintergrund-Sammler
  lua.py, cbor.py  Parser für SavedVariables und Auctionator-CBOR
  sources/         auctionator.py, battlenet.py, wowhead.py, demo.py
static/            index.html, app.css, app.js, static-api.js, recipes-core.js, vendor/chart.umd.min.js
tests/             73 Tests (Parser, Ingest, Battle.net mit Fake-HTTP, Kennzahlen, API, Rezepte, Quests/Sets; recipes_core.test.js über Node)
data/              SQLite-Datenbanken (ah.sqlite = echt, demo.sqlite = Beispieldaten)
site/              Export für GitHub Pages (wird im Workflow gebaut, nicht in main)
seed/              Startdatenbank für den allerersten Workflow-Lauf
.github/workflows/ update.yml – stündliche Aktualisierung
```

## Hinweise

* Die Website lauscht nur auf `127.0.0.1`. Für Zugriff aus dem Heimnetz `--host 0.0.0.0` (ohne Authentifizierung!).
* Item-Icons werden von `wow.zamimg.com` (Wowhead) bzw. `render.worldofwarcraft.com` geladen; ohne Internet erscheint ein Platzhalter.
* Preise werden intern in Kupfer gespeichert (1 g = 100 s = 10 000 c).
* Datenbank zurücksetzen: Server beenden und `data\ah.sqlite*` löschen.
