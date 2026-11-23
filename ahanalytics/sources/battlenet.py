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
* Es wird EIN Ruleset abgefragt. Gleichheit anderer Rulesets ist nicht
  nachgewiesen und wird im Laufmanifest nicht behauptet.
* Rund 40 % der Item-IDs liefern beim Item-Endpunkt 404 → Metadaten optional.

Annahme: ``buyout`` bei Nicht-Commodities ist der Preis der gesamten Auktion;
der Stückpreis ist ``buyout / quantity`` (bei Menge 1 identisch).
"""
from __future__ import annotations

import base64
import gzip
import json
import random
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


def _retry_delay(headers, attempt):
    value = (headers or {}).get("Retry-After")
    if value:
        try:
            delay = max(0, float(value))
            if delay > 120: raise ApiError("Retrybudget", "", "Retry-After über 120 Sekunden; kein früherer Retry")
            return delay
        except ValueError:
            try:
                delay = max(0, parsedate_to_datetime(value).timestamp()-time.time())
                if delay > 120: raise ApiError("Retrybudget", "", "Retry-After über 120 Sekunden; kein früherer Retry")
                return delay
            except (TypeError, ValueError):
                pass
    return min(30, 2 ** attempt + random.uniform(0, 1))


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
                    time.sleep(_retry_delay(exc.headers, attempt))
                    last_exc = exc
                    continue
                raise ApiError(exc.code, url) from exc
            except (urllib.error.URLError, TimeoutError) as exc:
                last_exc = exc
                if attempt < retries - 1:
                    time.sleep(_retry_delay({}, attempt))
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
            ladder_keep=48, fetch_metadata=True, log=print, now=None) -> ImportResult:
    """Stage all endpoints, validate each house, then atomically publish observations.

    An omitted auctions array is unknown, never a confirmed empty market. Partial
    groups are explicit and cannot prove absence. No comparison of other rulesets
    is implied by selecting one ruleset.
    """
    import hashlib
    now = int(time.time()) if now is None else int(now)
    res = ImportResult(SOURCE)
    manifest = {"version": 1, "started_at": now, "region": client.region,
                "namespace": client.ns("dynamic"), "ruleset": ruleset,
                "ruleset_scope": "selected_only_not_cross_ruleset_verified",
                "limits": {"max_source_age_s": 10800, "max_skew_s": 600, "future_s": 600},
                "only_houses": list(only_houses or []), "expected_houses": [],
                "endpoints": [], "groups": [], "errors": []}
    started_clock = time.monotonic()
    rid = con.execute("INSERT INTO market_runs(started_at,region,ruleset,status,manifest) VALUES(?,?,?,?,?)",
                      (now,client.region,str(ruleset or ""),"running",json.dumps(manifest))).lastrowid
    def finish(status):
        manifest.update(status=status, finished_at=int(time.time()))
        con.execute("UPDATE market_runs SET finished_at=?,ruleset=?,status=?,manifest=? WHERE id=?",
                    (manifest["finished_at"],str(manifest["ruleset"] or ""),status,json.dumps(manifest),rid))
    try:
        houses = discover_houses(client, ruleset)
        if not houses or any(not isinstance(h.get("id"), int) or h["id"] <= 0 for h in houses):
            raise ValueError("Leeres oder ungültiges Häuserverzeichnis")
        if len({h["id"] for h in houses}) != len(houses):
            raise ValueError("Doppelte Haus-ID im Verzeichnis")
        manifest["ruleset"] = houses[0]["ruleset"]
        if only_houses:
            wanted = {int(h) for h in only_houses}
            missing = wanted - {h["id"] for h in houses}
            if missing:
                raise ValueError(f"Angeforderte Häuser fehlen: {sorted(missing)}")
            houses = [h for h in houses if h["id"] in wanted]
        manifest["expected_houses"] = [h["id"] for h in houses]
        known = {int(row[0].split("-")[-1]) for row in con.execute(
            "SELECT ext_id FROM houses WHERE source=? AND region=?", (SOURCE, client.region))
            if row[0].split("-")[-1].isdigit()}
        disappeared = known - set(manifest["expected_houses"]) if not only_houses else set()
        if disappeared:
            manifest["errors"].append(f"Bekannte Häuser fehlen im Verzeichnis: {sorted(disappeared)}")
        staged = []
        for h in houses:
            base = f"/data/wow/ruleset/{h['ruleset']}/auctions/{h['id']}"
            entries, data, commodity_ids = [], [], set()
            for commodity, path in ((False,base),(True,base+"/commodities")):
                entry = {"house": h["id"], "endpoint": "commodities" if commodity else "items",
                         "path": path, "fetched_at": int(time.time()), "status": "error"}
                entries.append(entry); manifest["endpoints"].append(entry)
                try:
                    if time.monotonic()-started_clock > 900:
                        raise ValueError("Abrufbudget von 15 Minuten erreicht")
                    payload, headers = client.get(path)
                    entry["http_status"] = 200 if payload is not None else 304
                    entry["sha256"] = hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(",",":")).encode()).hexdigest()
                    lm = next((v for k,v in headers.items() if k.lower()=="last-modified"),None)
                    entry["last_modified"] = lm
                    entry["etag"] = next((v for k,v in headers.items() if k.lower()=="etag"),None)
                    try:
                        source_ts = int(parsedate_to_datetime(lm).timestamp()) if lm else None
                    except (TypeError,ValueError,OverflowError):
                        source_ts = None
                    entry["source_ts"] = source_ts
                    if not isinstance(payload,dict) or not isinstance(payload.get("auctions"),list):
                        raise ValueError("auctions-Liste fehlt: unbekannt, nicht leer")
                    auctions = payload["auctions"]
                    entry["auction_count"] = len(auctions)
                    seen = set()
                    for a in auctions:
                        if not isinstance(a,dict) or not isinstance(a.get("item"),dict):
                            raise ValueError("Ungültige Auktionsstruktur")
                        iid, qty = a["item"].get("id"), a.get("quantity")
                        if type(iid) is not int or iid <= 0 or type(qty) is not int or qty <= 0:
                            raise ValueError("Ungültige Item-ID oder Menge")
                        aid = a.get("id")
                        if aid is not None:
                            if type(aid) is not int or aid in seen:
                                raise ValueError("Ungültige oder doppelte Auktions-ID")
                            seen.add(aid)
                        price = a.get("unit_price") if commodity or "unit_price" in a else a.get("buyout")
                        if price is not None and (type(price) is not int or price < 0):
                            raise ValueError("Ungültiger Preis")
                        if commodity and price is None:
                            raise ValueError("Commodity ohne Stückpreis")
                    if auctions and source_ts is None:
                        raise ValueError("Quellzeit fehlt")
                    if source_ts and source_ts > now + 600:
                        raise ValueError("Quellzeit liegt in der Zukunft")
                    if auctions and source_ts < now - 10800:
                        raise ValueError("Quellstand älter als 3 Stunden")
                    rows = list(listings_from_payload(payload,commodity))
                    entry.update(status="ok" if auctions else "empty", listing_count=len(rows),
                                 item_count=len({x[0] for x in rows}))
                    data.extend(rows)
                    if commodity: commodity_ids.update(i for i,_,_ in rows)
                except (ApiError,ValueError,TypeError,KeyError) as exc:
                    entry["error"] = str(exc)
                    if isinstance(exc,ApiError):
                        entry["http_status"] = exc.status
            times = [e["source_ts"] for e in entries if e.get("auction_count") and e.get("source_ts")]
            valid = all(e["status"] in ("ok","empty") for e in entries)
            if times and max(times)-min(times)>600:
                valid = False
                for e in entries: e.update(status="invalid",error="Zeitversatz zwischen Endpunkten über 10 Minuten")
            digest = hashlib.sha256("|".join(e.get("sha256", "") for e in entries).encode()).hexdigest()
            old = con.execute("SELECT payload_hash FROM snapshots JOIN houses ON houses.id=snapshots.house_id WHERE houses.source=? AND houses.ext_id=? AND snapshots.ts=?", (SOURCE,f"{client.region}-{h['id']}",(max(times) if times else now)//60*60)).fetchone()
            if valid and old and old[0] and old[0] != digest:
                valid = False
                for e in entries: e.update(status="invalid",error="Geänderter Inhalt bei gleicher Quellzeit; vorherigen Stand behalten")
            h.update(payload_hash=digest,faction=faction_from_name(h["name"]), entries=entries, listings=data,
                     times=times, commodity_ids=commodity_ids, valid=valid, ts=(max(times) if times else now)//60*60)
            staged.append(h)
        touched = []; commodity_items = set()
        with db.transaction(con):
            for h in staged:
                hid = db.upsert_house(con,SOURCE,f"{client.region}-{h['id']}",f"{h['name']} #{h['id']}",
                                      region=client.region,faction=h["faction"],ruleset=h["ruleset"],granularity="hour")
                h["db_id"] = hid
                if not h["valid"]:
                    res.skipped += 1
                    continue
                stats, ladders = aggregate_listings(h["listings"])
                if not db.snapshot_exists(con,hid,h["ts"]) or con.execute("SELECT payload_hash FROM snapshots WHERE house_id=? AND ts=?",(hid,h["ts"])).fetchone()[0] is None:
                    sid = db.write_snapshot(con,hid,h["ts"],SOURCE,stats,ladders)
                    con.execute("UPDATE snapshots SET complete=1,run_id=?,source_min_ts=?,source_max_ts=?,payload_hash=? WHERE id=?",
                                (rid,min(h["times"]) if h["times"] else None,max(h["times"]) if h["times"] else None,h["payload_hash"],sid))
                    touched.append(hid); res.houses += 1; res.snapshots += 1; res.price_points += len(stats)
                else:
                    res.skipped += 1
                    # Revalidation attaches run evidence; no rewrite of existing observations.
                    con.execute("UPDATE snapshots SET complete=1,run_id=? WHERE house_id=? AND ts=?",(rid,hid,h["ts"]))
                db.set_meta(con,f"complete:{hid}","1")
                log(f"Haus {h['id']}: {len(stats)} Items; Endpunkte geprüft")
            if not only_houses:
                for key in db.GROUPS:
                    members = [h for h in staged if db.group_for_faction(h["faction"])==key]
                    valid = [h for h in members if h["valid"]]
                    times = [t for h in valid for t in h["times"]]
                    complete = len(valid)==len(members) and bool(members) and not disappeared
                    synced = not times or max(times)-min(times)<=600
                    group = {"key":key,"expected":len(members),"valid":len(valid),"complete":complete and synced,
                             "source_min_ts":min(times) if times else None,"source_max_ts":max(times) if times else None}
                    manifest["groups"].append(group)
                    if not valid or not synced:
                        group["activation"] = "retained_previous"
                        continue
                    gid = db.upsert_group_house(con,SOURCE,client.region,key)
                    ts = (max(times) if times else now)//60*60
                    digest = hashlib.sha256("|".join(sorted(h["payload_hash"] for h in valid)).encode()).hexdigest()
                    prior = con.execute("SELECT payload_hash FROM snapshots WHERE house_id=? AND ts=?",(gid,ts)).fetchone()
                    if prior and prior[0]:
                        group["activation"] = "unchanged" if prior[0]==digest else "retained_previous_content_conflict"
                        # Do not upgrade an older partial snapshot without rewriting the pool.
                        continue
                    pools = [row for h in valid for row in h["listings"]]
                    stats,ladders = aggregate_listings(pools)
                    mins = []
                    for h in valid:
                        ss,_ = aggregate_listings(h["listings"])
                        mins.append({i:(s["p10"],s["quantity"]) for i,s in ss.items()})
                    db.apply_typical_min(stats,mins)
                    sid = db.write_snapshot(con,gid,ts,SOURCE,stats,ladders)
                    con.execute("UPDATE snapshots SET complete=?,run_id=?,source_min_ts=?,source_max_ts=?,payload_hash=? WHERE id=?",
                                (int(group["complete"]),rid,group["source_min_ts"],group["source_max_ts"],digest,sid))
                    db.set_meta(con,f"complete:{gid}",int(group["complete"]))
                    group.update(activation="complete" if group["complete"] else "partial",house_id=gid,ts=ts)
                    touched.append(gid)
            commodity_items = {i for h in staged if h["valid"] for i in h["commodity_ids"]}
            con.executemany("UPDATE items SET is_commodity=1 WHERE id=?", ((i,) for i in commodity_items))
            if touched:
                db.prune_ladders(con,ladder_keep)
                db.refresh_summary(con,touched)
                from .. import liquidity
                liquidity.update_liquidity(con,touched,log=log)
            status = "complete" if all(h["valid"] for h in staged) and not manifest["errors"] and not only_houses and all(g["complete"] and g.get("activation")!="retained_previous_content_conflict" for g in manifest["groups"] if g["expected"]) else "partial"
            if not any(h["valid"] for h in staged): status="rejected"
            finish(status)
            db.bump_data_version(con)
            db.log_import(con,SOURCE,status,f"Marktlauf {rid}: {sum(h['valid'] for h in staged)}/{len(staged)} Häuser geprüft; {status}")
        if status == "rejected":
            raise RuntimeError(f"Marktlauf {rid}: kein Hauspaar sicher freigegeben")
        res.items = con.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        res.messages.append(f"Marktlauf {rid}: {status}")
        if fetch_metadata:
            n = fetch_item_metadata(con,client,log=log)
            if n: res.messages.append(f"{n} Item-Metadaten geladen")
        return res
    except Exception as exc:
        manifest["errors"].append(str(exc))
        finish("rejected")
        raise


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
    out["details"] = parse_details(payload, locale)
    if isinstance(payload.get("name"), dict):
        out["name_en"] = _loc(payload["name"], "en_US")
    return out


def parse_details(payload: dict, locale="de_DE") -> str:
    """Tooltip-Zeilen + Flags als JSON-String, in der angegebenen Sprache."""
    lines = tooltip_lines(payload.get("preview_item") or {}, locale)
    unique_word = "Einzigartig" if locale.startswith("de") else "Unique"
    if payload.get("max_count") == 1 and not any(l["t"].startswith(unique_word) for l in lines):
        lines.insert(0, {"t": unique_word, "c": "w"})
    return json.dumps({"lines": lines, "stackable": payload.get("is_stackable"),
                       "equippable": payload.get("is_equippable")},
                      ensure_ascii=False, separators=(",", ":"))


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


def fetch_english_details(con, client: BattleNetClient, limit=3000, recheck_days=7,
                          workers=8, log=print) -> int:
    """Englische Tooltip-Zeilen (details_en) für Items mit deutschen Details.

    Dieselbe Battle.net-Abfrage wie bei den deutschen Metadaten (all_locales),
    nur mit en_US ausgewertet – für die englischen statischen Item-Seiten.
    Items, die Battle.net nicht kennt (404), werden 7 Tage nicht erneut gefragt.
    """
    cutoff = int(time.time()) - recheck_days * 86400
    rows = con.execute(
        """SELECT id FROM items
           WHERE details IS NOT NULL AND details_en IS NULL AND COALESCE(details_en_checked, 0) < ?
           ORDER BY id LIMIT ?""", (cutoff, limit)).fetchall()
    if not rows:
        return 0
    log(f"Lade englische Tooltips von Battle.net für {len(rows)} Items …")

    def one(row):
        iid = row[0]
        try:
            item, _ = client.get(f"/data/wow/item/{iid}", "static", all_locales=True)
        except ApiError as exc:
            return iid, None, exc.status
        return iid, item, 200

    ok = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(one, rows))
    now = int(time.time())
    with db.transaction(con):
        for iid, item, _code in results:
            con.execute("UPDATE items SET details_en_checked=? WHERE id=?", (now, iid))
            if not item:
                continue
            con.execute("UPDATE items SET details_en=? WHERE id=?",
                        (parse_details(item, "en_US"), iid))
            ok += 1
    log(f"Englische Tooltips: {ok} von {len(rows)} Items geladen, {len(rows) - ok} dort unbekannt")
    return ok


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
           WHERE ((meta_status != 'ok' OR name_en IS NULL OR name LIKE '[%') AND COALESCE(bn_checked, 0) < ?)
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
                fields = {k: v for k, v in fields.items() if v is not None and (cur.get(k) in (None, "") or (k == "name" and (cur.get(k) or "").startswith("[") and not str(v).startswith("[")))}
                if fields:
                    sets = ", ".join(f"{k}=?" for k in fields)
                    con.execute(f"UPDATE items SET {sets} WHERE id=?", list(fields.values()) + [iid])
            else:
                db.upsert_item(con, iid, "ok", **{k: v for k, v in fields.items() if v is not None})
            ok += 1
        db.bump_data_version(con)
    log(f"Battle.net-Metadaten: {ok} von {len(rows)} Items geladen, {len(rows) - ok} dort unbekannt")
    return ok
