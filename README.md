# Forever AH-Analytics

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
python run.py info                        # was ist in der Datenbank?
python -m unittest discover -s tests      # Tests
```

### Öffentliche Version (GitHub Pages)

Live: <https://levo006.github.io/wow-forever-ah-analytics/> · Code: <https://github.com/Levo006/wow-forever-ah-analytics>

Die öffentliche Seite ist ein **statischer Export** (Ordner `docs/`), den GitHub Pages kostenlos ausliefert. Alle Ansichten funktionieren dort ebenfalls: Suche, Filter, Top-Movers, Deals und Kategorien rechnet `static/static-api.js` direkt im Browser aus den exportierten JSON-Dateien. Nicht verfügbar sind dort nur Import und Live-Sammler; die Historie ist auf 90 Tage begrenzt, Detaildaten (Preisstaffeln) gibt es für die jüngsten 72 Snapshots je Haus.

**Aktualisieren:** Im Spiel scannen, `/reload`, dann `publish.bat` ausführen. Das Skript importiert die Auctionator-Daten, erzeugt `docs/` neu (`python run.py export`) und pusht; nach 1–2 Minuten ist die Seite aktuell.

**Einmalige Einrichtung** (bereits erledigt, falls die Links oben funktionieren): Repository auf GitHub anlegen, `git push -u origin main`, dann unter *Settings → Pages* „Deploy from a branch“, Branch `main`, Ordner `/docs` wählen.

---

## Ansichten

| Ansicht | Inhalt |
|---|---|
| **Übersicht** | KPIs des gewählten Auktionshauses (Items, Marktwert, Menge, Auktionen, Ø Preisänderung), Marktwert-Verlauf, Kategorien, Gewinner/Verlierer, Deals, volatilste Items |
| **Suche** | Filter nach Name/ID, Klasse, Unterklasse, Qualität, Preisbereich (Gold), Menge, Ø Menge (Liquidität), Region, Fraktion, Auktionshaus, Handelsware/Einzelitem. Sortierbare Spalten, 14-Tage-Sparkline, Seitenweise |
| **Kategorien** | Drilldown **Kategorie → Unterkategorie → Item → Snapshot** mit Marktwert und Ø Änderung je Ebene |
| **Item** | Preis-Chart (Min, 10 %-Perzentil, Median, Mittel, Max) für 24 h / 7 T / 30 T / Alles, Angebotsmenge als eigener Chart, Ausreißer markiert, Trend, Volatilität, 30-T-Tief/-Hoch, Tageszeit- und Wochentagsmuster, Häuservergleich, Liste aller Snapshots. Klick in den Chart öffnet den Snapshot |
| **Snapshot** | Alle Items eines Zeitpunkts mit Änderung zum vorherigen Snapshot; für ein Item die **Preisstaffel** (Menge je Preisstufe) inkl. Kosten für 20/100 Stück; Vor/Zurück-Navigation |
| **Vergleich** | Bis zu 8 Items in einem Chart, absolut oder **indexiert (Start = 100)**, wählbare Kennzahl, Kennzahlentabelle, **Korrelationsmatrix** der Preisänderungen |
| **Top-Movers** | Gewinner/Verlierer über 24 h oder 7 T, nach Mindestpreis oder Median, Mindest-Liquidität und Klasse filterbar |
| **Deals** | Items, deren aktueller Mindestpreis X % unter dem Ø 7 T / Ø 30 T liegt (Referenz Mindestpreis oder Median), mit Ersparnis pro Stück |
| **Daten** | Auctionator-Import per Knopf oder Datei-Upload (Drag & Drop), Battle.net-Status und manueller Abruf, Häuserliste, Import-Protokoll |

Das Auktionshaus wird oben rechts gewählt; Hell/Dunkel per ◐. Items kommen über „+ Vgl.“ in jeder Tabelle in den Vergleich.

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

**Item-Namen:** Auctionator speichert keine Namen. Ohne Battle.net-Zugang erscheinen Items als „Item #ID“ (mit Wowhead-Link); mit konfiguriertem Zugang werden Name, Qualität, Klasse und Icon nach dem Import automatisch geladen.

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

**Tabellen:** `houses` (Auktionshäuser/Realms je Quelle), `items` (Metadaten), `snapshots` (ein Eintrag pro Haus und Zeitpunkt), `prices` (Kennzahlen pro Haus/Item/Zeitpunkt, Kupfer), `ladders` (Preisstaffel, nur jüngste Snapshots), `item_summary`, `import_log`, `meta`.

**API** (alle `GET`, JSON): `/api/houses`, `/api/overview`, `/api/classes`, `/api/search`, `/api/item/{id}`, `/api/item/{id}/history`, `/api/item/{id}/analysis`, `/api/item/{id}/snapshots`, `/api/snapshot/{haus}/{ts}`, `/api/compare`, `/api/movers`, `/api/deals`, `/api/status`, `/api/import/log`; `POST /api/import/auctionator` (Datei im Body), `POST /api/import/auctionator/auto`, `POST /api/collect`.

**Projektstruktur**

```
ahanalytics/
  __main__.py      Kommandozeile (serve, demo, import-auctionator, collect, refresh, info)
  config.py        config.toml + Umgebungsvariablen
  db.py            Schema, Upserts, item_summary
  aggregate.py     Auktionen → Kennzahlen (mengengewichtete Perzentile)
  metrics.py       Volatilität, Trend, Ausreißer, Saisonalität, Verdichtung
  api.py           Endpunkte
  server.py        HTTP-Server, Cache, Hintergrund-Sammler
  lua.py, cbor.py  Parser für SavedVariables und Auctionator-CBOR
  sources/         auctionator.py, battlenet.py, demo.py
static/            index.html, app.css, app.js, vendor/chart.umd.min.js
tests/             36 Tests (Parser, Ingest, Battle.net mit Fake-HTTP, Kennzahlen, API)
data/              SQLite-Datenbanken (ah.sqlite = echt, demo.sqlite = Beispieldaten)
```

## Hinweise

* Die Website lauscht nur auf `127.0.0.1`. Für Zugriff aus dem Heimnetz `--host 0.0.0.0` (ohne Authentifizierung!).
* Item-Icons werden von `wow.zamimg.com` (Wowhead) bzw. `render.worldofwarcraft.com` geladen; ohne Internet erscheint ein Platzhalter.
* Preise werden intern in Kupfer gespeichert (1 g = 100 s = 10 000 c).
* Datenbank zurücksetzen: Server beenden und `data\ah.sqlite*` löschen.
