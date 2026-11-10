"""Sammler für die Battle.net Game Data API (WoW: Forever).

Stand der Forever-API (inoffiziell, von der Community ermittelt, Nov. 2026):

* Namespaces: ``dynamic-forever-{region}`` (Rulesets, Auktionen),
  ``static-forever-{region}`` (Items, Medien). Regionen: eu, us, kr, tw.
* Forever hat keine Realms, sondern Rulesets:
  ``/data/wow/ruleset/index`` → ``/data/wow/ruleset/{id}``.
* Auktionshäuser je Ruleset (Schrägstrich am Ende ist Pflicht!):
  ``/data/wow/ruleset/{id}/auctions/`` → ``{auctions: [{key, name, id}]}``
* Auktionen eines Hauses: ``/data/wow/ruleset/{id}/auctions/{houseId}``
  → ``{auctions: [{id, item:{id,…}, buyout, bid?, quantity, time_left}]}``
  (``auctions`` kann fehlen).
* Commodities: ``/data/wow/ruleset/{id}/auctions/{houseId}/commodities``
  → ``{auctions: [{id, item:{id}, quantity, unit_price, time_left}]}``
* Alle Rulesets liefern dieselben Häuser mit identischen Auktionen –
  deshalb wird nur EIN Ruleset abgefragt.
* Rund 40 % der Item-IDs liefern beim Item-Endpunkt 404 → Metadaten optional.

Annahme: ``buyout`` bei Nicht-Commodities ist der Preis der gesamten Auktion;
der Stückpreis ist ``buyout / quantity`` (bei Menge 1 identisch).
"""
from __future__ import annotations

import base64
import gzip
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from email.utils import parsedate_to_datetime

from .. import db
from ..aggregate import aggregate_listings
from ..itemmeta import QUALITY_TYPES, faction_from_name
from . import ImportResult

SOURCE = "battlenet"
USER_AGENT = "wow-ah-analytics/1.0"


class ApiError(RuntimeError):
    def __init__(self, status, url, body=""):
        super().__init__(f"HTTP {status} für {url}")
        self.status = status
        self.url = url
        self.body = body


class BattleNetClient:
    def __init__(self, client_id, client_secret, region="eu", locale="de_DE", timeout=120,
                 opener=None):
        if not client_id or not client_secret:
            raise ValueError("Battle.net client_id/client_secret fehlen (config.toml oder "
                             "Umgebungsvariablen BLIZZARD_CLIENT_ID / BLIZZARD_CLIENT_SECRET).")
        self.client_id = client_id
        self.client_secret = client_secret
        self.region = region.lower()
        self.locale = locale
        self.timeout = timeout
        self._token = None
        self._token_exp = 0
        self._lock = threading.Lock()
        self._open = opener or urllib.request.urlopen

    @property
    def host(self):
        if self.region == "cn":
            return "https://gateway.battlenet.com.cn"
        return f"https://{self.region}.api.blizzard.com"

    def ns(self, kind):
        return f"{kind}-forever-{self.region}"

    def token(self):
        with self._lock:
            if self._token and time.time() < self._token_exp - 60:
                return self._token
            cred = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode()
            req = urllib.request.Request(
                "https://oauth.battle.net/token", data=b"grant_type=client_credentials",
                headers={"Authorization": f"Basic {cred}", "User-Agent": USER_AGENT,
                         "Content-Type": "application/x-www-form-urlencoded"})
            with self._open(req, timeout=self.timeout) as resp:
                payload = json.loads(resp.read())
            self._token = payload["access_token"]
            self._token_exp = time.time() + int(payload.get("expires_in", 3600))
            return self._token

    def get(self, path, namespace_kind="dynamic", params=None, if_modified_since=None,
            retries=3, all_locales=False):
        """GET mit Token, gzip und Wiederholungen. Gibt (json, headers) zurück.

        Bei 304 (nicht geändert) wird (None, headers) zurückgegeben.
        ``all_locales=True`` lässt ``locale`` weg: Texte kommen dann als
        {"de_DE": …, "en_US": …, …}.
        """
        q = {"namespace": self.ns(namespace_kind)}
        if not all_locales:
            q["locale"] = self.locale
        q.update(params or {})
        url = f"{self.host}{path}?{urllib.parse.urlencode(q)}"
        last_exc = None
        for attempt in range(retries):
            headers = {"Authorization": f"Bearer {self.token()}", "Accept-Encoding": "gzip",
                       "User-Agent": USER_AGENT}
            if if_modified_since:
                headers["If-Modified-Since"] = if_modified_since
            req = urllib.request.Request(url, headers=headers)
            try:
                with self._open(req, timeout=self.timeout) as resp:
                    raw = resp.read()
                    if resp.headers.get("Content-Encoding") == "gzip":
                        raw = gzip.decompress(raw)
                    return json.loads(raw), dict(resp.headers)
            except urllib.error.HTTPError as exc:
                if exc.code == 304:
                    return None, dict(exc.headers)
                if exc.code == 401:
                    self._token = None
                if exc.code in (401, 429, 500, 502, 503, 504) and attempt < retries - 1:
                    time.sleep(1.5 * (attempt + 1))
                    last_exc = exc
                    continue
                raise ApiError(exc.code, url) from exc
            except (urllib.error.URLError, TimeoutError) as exc:
                last_exc = exc
                if attempt < retries - 1:
                    time.sleep(2 * (attempt + 1))
                    continue
                raise ApiError("Netzwerk", url, str(exc)) from exc
        raise ApiError("?", url, str(last_exc))


# ------------------------------------------------------------- Auswertung

def listings_from_payload(payload: dict, commodity: bool):
    """Erzeugt (item_id, stückpreis, menge) aus einer Auktions-Antwort."""
    for a in (payload or {}).get("auctions") or []:
        item = a.get("item") or {}
        iid = item.get("id")
        qty = a.get("quantity") or 1
        if iid is None:
            continue
        if commodity or "unit_price" in a:
            unit = a.get("unit_price")
        else:
            buyout = a.get("buyout")
            unit = None if buyout is None else buyout // max(qty, 1)
        if unit:
            yield int(iid), int(unit), int(qty)


def _ts_from_headers(headers) -> int:
    lm = headers.get("Last-Modified") or headers.get("last-modified")
    if lm:
        try:
            return int(parsedate_to_datetime(lm).timestamp())
        except (TypeError, ValueError):
            pass
    return int(time.time())


def discover_houses(client: BattleNetClient, ruleset=None):
    if not ruleset:
        idx, _ = client.get("/data/wow/ruleset/index")
        rulesets = idx.get("rulesets") or idx.get("ruleset") or []
        if not rulesets:
            raise ApiError("leer", "/data/wow/ruleset/index", "Keine Rulesets gefunden")
        ruleset = rulesets[0].get("id")
    data, _ = client.get(f"/data/wow/ruleset/{ruleset}/auctions/")
    houses = []
    for h in data.get("auctions") or []:
        name = h.get("name")
        if isinstance(name, dict):  # Mehrsprachige Antwort ohne locale
            name = name.get("de_DE") or name.get("en_US") or next(iter(name.values()), "")
        houses.append({"id": h.get("id"), "name": name or f"Haus {h.get('id')}",
                       "ruleset": ruleset})
    return houses


def collect(con, client: BattleNetClient, ruleset=None, only_houses=None,
            ladder_keep=48, fetch_metadata=True, log=print) -> ImportResult:
    res = ImportResult(SOURCE)
    houses = discover_houses(client, ruleset)
    if only_houses:
        wanted = {int(h) for h in only_houses}
        houses = [h for h in houses if int(h["id"]) in wanted]
    touched = []
    commodity_items: set[int] = set()
    # Sammelgruppen: alle Einzelauktionen gemeinsam auswerten (exakter Median usw.)
    pools: dict[str, list] = {"alle": [], "neutral": []}
    pool_ts: dict[str, int] = {"alle": 0, "neutral": 0}
    pool_mins: dict[str, list] = {"alle": [], "neutral": []}  # je Haus {item: (min, menge)}
    for h in houses:
        hid_ext = f"{client.region}-{h['id']}"
        faction = faction_from_name(h["name"])
        with db.transaction(con):
            house_id = db.upsert_house(con, SOURCE, hid_ext, f"{h['name']} #{h['id']}",
                                       region=client.region, faction=faction,
                                       ruleset=h["ruleset"], granularity="hour")
        base = f"/data/wow/ruleset/{h['ruleset']}/auctions/{h['id']}"
        try:
            items_payload, hdr1 = client.get(base)
            comm_payload, hdr2 = client.get(base + "/commodities")
        except ApiError as exc:
            if exc.status == 404:
                res.messages.append(f"Haus {h['id']}: 404")
                res.skipped += 1
                continue
            raise
        ts = max(_ts_from_headers(hdr1), _ts_from_headers(hdr2))
        ts -= ts % 60
        listings = list(listings_from_payload(items_payload, False))
        comm = list(listings_from_payload(comm_payload, True))
        del items_payload, comm_payload
        commodity_items.update(i for i, _, _ in comm)
        stats, ladders = aggregate_listings(listings + comm)
        if stats:
            key = db.group_for_faction(faction)
            pools[key].extend(listings)
            pools[key].extend(comm)
            # günstige Preisstufe je Haus (10 %-Perzentil statt Einzel-Lockangebot)
            pool_mins[key].append({i: (s["p10"], s["quantity"]) for i, s in stats.items()})
            pool_ts[key] = max(pool_ts[key], ts)
        if db.snapshot_exists(con, house_id, ts):
            res.skipped += 1
            log(f"Haus {h['id']}: Snapshot {ts} bereits vorhanden – übersprungen")
            continue
        if not stats:  # manche Häuser liefern (noch) keine Auktionen
            res.skipped += 1
            log(f"Haus {h['id']} ({h['name']}): keine Auktionen – übersprungen")
            continue
        with db.transaction(con):
            db.write_snapshot(con, house_id, ts, SOURCE, stats, ladders)
            db.prune_ladders(con, ladder_keep)
        touched.append(house_id)
        res.houses += 1
        res.snapshots += 1
        res.price_points += len(stats)
        log(f"Haus {h['id']} ({h['name']}): {len(listings) + len(comm)} Auktionen, "
            f"{len(stats)} Items")
    for key, pool in pools.items():
        if not pool:
            continue
        with db.transaction(con):
            gid = db.upsert_group_house(con, SOURCE, client.region, key)
            if not db.snapshot_exists(con, gid, pool_ts[key]):
                stats, ladders = aggregate_listings(pool)
                db.apply_typical_min(stats, pool_mins[key])
                db.write_snapshot(con, gid, pool_ts[key], SOURCE, stats, ladders)
                db.prune_ladders(con, ladder_keep)
                touched.append(gid)
                log(f"Sammelgruppe {key}: {len(pool)} Auktionen, {len(stats)} Items")
            if db.build_group_snapshots(con, gid, log=log) and gid not in touched:
                touched.append(gid)
        pools[key] = []
        pool_mins[key] = []
    with db.transaction(con):
        if commodity_items:
            con.executemany("UPDATE items SET is_commodity=1 WHERE id=?",
                            ((i,) for i in commodity_items))
        if touched:
            db.refresh_summary(con, touched)
            db.bump_data_version(con)
        db.log_import(con, SOURCE, "ok", res.summary())
    res.items = con.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    if fetch_metadata:
        n = fetch_item_metadata(con, client, log=log)
        if n:
            res.messages.append(f"{n} Item-Metadaten geladen")
    return res


def _loc(v, locale="de_DE"):
    """Text aus einer Ein- oder Mehrsprachen-Antwort."""
    if isinstance(v, dict):
        return v.get(locale) or (v.get("en_US") or v.get("en_GB") if locale.startswith("en") else None)
    return v


def parse_item(payload: dict, locale="de_DE") -> dict:
    q = payload.get("quality") or {}
    ic = payload.get("item_class") or {}
    isc = payload.get("item_subclass") or {}
    out = {
        "name": _loc(payload.get("name"), locale),
        "quality": QUALITY_TYPES.get(q.get("type"), None),
        "class_id": ic.get("id"),
        "class_name": _loc(ic.get("name"), locale),
        "subclass_id": isc.get("id"),
        "subclass_name": _loc(isc.get("name"), locale),
        "item_level": payload.get("level"),
        "vendor_sell": payload.get("sell_price"),
        "required_level": payload.get("required_level") or None,
    }
    inv = payload.get("inventory_type") or {}
    if inv.get("type") and inv.get("type") != "NON_EQUIP":
        out["slot"] = _loc(inv.get("name"), locale)
    lines = tooltip_lines(payload.get("preview_item") or {}, locale)
    if payload.get("max_count") == 1 and not any(l["t"].startswith("Einzigartig") for l in lines):
        lines.insert(0, {"t": "Einzigartig", "c": "w"})
    out["details"] = json.dumps({"lines": lines, "stackable": payload.get("is_stackable"),
                                 "equippable": payload.get("is_equippable")},
                                ensure_ascii=False, separators=(",", ":"))
    if isinstance(payload.get("name"), dict):
        out["name_en"] = _loc(payload["name"], "en_US")
    return out


def tooltip_lines(pi: dict, locale="de_DE") -> list[dict]:
    """Tooltip-Zeilen wie im Spiel: {t: Text, c: Farbe (w weiß, g grün, y gelb, b blau, r rechts)}."""
    L = lambda v: _loc(v, locale)  # noqa: E731
    out: list[dict] = []

    def add(text, color="w", right=None):
        text = L(text)
        if text:
            row = {"t": str(text), "c": color}
            if right:
                row["r"] = str(L(right))
            out.append(row)

    add((pi.get("binding") or {}).get("name"))
    ue = pi.get("unique_equipped")
    add(ue if isinstance(ue, (str, dict)) else None)
    inv = pi.get("inventory_type") or {}
    if inv.get("type") and inv.get("type") != "NON_EQUIP":
        sub = None if pi.get("is_subclass_hidden") else (pi.get("item_subclass") or {}).get("name")
        add(inv.get("name"), "w", sub)
    w = pi.get("weapon") or {}
    if w:
        add((w.get("damage") or {}).get("display_string"), "w", (w.get("attack_speed") or {}).get("display_string"))
        add((w.get("dps") or {}).get("display_string"))
    add(((pi.get("armor") or {}).get("display") or {}).get("display_string"))
    for st in pi.get("stats") or []:
        add((st.get("display") or {}).get("display_string"), "g" if st.get("is_equip_bonus") else "w")
    for res in pi.get("resistances") or []:
        add((res.get("display") or {}).get("display_string"))
    add((pi.get("durability") or {}).get("display_string"))
    req = pi.get("requirements") or {}
    for k in ("playable_classes", "playable_races", "skill", "reputation", "level"):
        add((req.get(k) or {}).get("display_string"))
    for sp in pi.get("spells") or []:
        add(sp.get("description"), "g")
    cr = pi.get("crafting_reagent")
    add(cr if isinstance(cr, (str, dict)) else None, "b")
    sets = (pi.get("set") or {}).get("display_string")
    add(sets, "y")
    desc = L(pi.get("description"))
    if desc:
        out.append({"t": f"„{desc}“", "c": "y"})
    return out[:40]


def fetch_item_metadata(con, client: BattleNetClient, limit=4000, recheck_days=7,
                        workers=8, log=print) -> int:
    """Lädt deutschen + englischen Namen, Qualität, Klasse und Icon.

    Betrifft Items ohne Metadaten oder ohne englischen Namen. Vorhandene
    Werte (z. B. Icons von Wowhead) werden nur ergänzt, nicht überschrieben.
    Items, die Battle.net nicht kennt (404), werden 7 Tage nicht erneut gefragt.
    """
    cutoff = int(time.time()) - recheck_days * 86400
    rows = con.execute(
        """SELECT id, meta_status, icon FROM items
           WHERE ((meta_status != 'ok' OR name_en IS NULL) AND COALESCE(bn_checked, 0) < ?)
              OR COALESCE(details_v, 0) < 1
           ORDER BY id LIMIT ?""", (cutoff, limit)).fetchall()
    if not rows:
        return 0
    log(f"Lade Item-Daten (deutsch + englisch) von Battle.net für {len(rows)} Items …")

    def one(row):
        iid, status, icon = row[0], row[1], row[2]
        try:
            item, _ = client.get(f"/data/wow/item/{iid}", "static", all_locales=True)
        except ApiError as exc:
            return iid, status, None, None, exc.status
        new_icon = None
        if not icon:
            try:
                media, _ = client.get(f"/data/wow/media/item/{iid}", "static")
                for a in media.get("assets") or []:
                    if a.get("key") == "icon":
                        new_icon = a.get("value")
            except ApiError:
                pass
        return iid, status, item, new_icon, 200

    ok = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(one, rows))
    now = int(time.time())
    with db.transaction(con):
        for iid, status, item, icon, code in results:
            con.execute("UPDATE items SET bn_checked=?, details_v=1 WHERE id=?", (now, iid))
            if not item:
                continue
            fields = parse_item(item)
            if icon:
                fields["icon"] = icon
            if status == "ok":
                # nur ergänzen: englischer Name + leere Felder
                cur = dict(con.execute("SELECT * FROM items WHERE id=?", (iid,)).fetchone())
                fields = {k: v for k, v in fields.items() if v is not None and cur.get(k) in (None, "")}
                if fields:
                    sets = ", ".join(f"{k}=?" for k in fields)
                    con.execute(f"UPDATE items SET {sets} WHERE id=?", list(fields.values()) + [iid])
            else:
                db.upsert_item(con, iid, "ok", **{k: v for k, v in fields.items() if v is not None})
            ok += 1
        db.bump_data_version(con)
    log(f"Battle.net-Metadaten: {ok} von {len(rows)} Items geladen, {len(rows) - ok} dort unbekannt")
    return ok
