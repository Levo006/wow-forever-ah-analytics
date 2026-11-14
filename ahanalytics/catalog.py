"""Pinned Forever client identity layer. No market data or Classic fallback.

All source bytes and row counts are pinned. New builds require a reviewed manifest.
An incomplete download cannot alter the existing catalog or any price history.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import time
import urllib.request
from pathlib import Path

from . import db
from .itemmeta import CLASS_NAMES

MANIFEST = Path(__file__).resolve().parent.parent / "catalog-source.json"
TEST_NAME = re.compile(r"(?:^(?-i:OLD)(?=[A-Z\s(])|\bUNUSED\b|\bDEPRECATED\b|\bQATest\b|\bPlaceholder\b|\[PH\]|\[DNT\])", re.I)
SCHEMA = """CREATE TABLE IF NOT EXISTS client_catalog (
    item_id INTEGER PRIMARY KEY, build TEXT NOT NULL, name_de TEXT, name_en TEXT,
    readable INTEGER NOT NULL, test_hint INTEGER NOT NULL, binding INTEGER
);"""


def parse_csv(raw, spec):
    if hashlib.sha256(raw).hexdigest() != spec["sha256"]:
        raise ValueError(f"{spec['table']}/{spec['locale']}: Prüfsumme weicht ab; kein Import")
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
    required = {"ID", "ClassID", "SubclassID"} if spec["table"] == "Item" else {
        "ID", "Display_lang", "Bonding", "OverallQualityID", "RequiredLevel", "ItemLevel"}
    if not required <= set(reader.fieldnames or []):
        raise ValueError("Client-Tabelle hat ein unbekanntes Schema")
    rows = {}
    for r in reader:
        iid = int(r["ID"])
        if iid <= 0 or iid in rows:
            raise ValueError("Ungültige/doppelte Item-ID")
        rows[iid] = r
    if len(rows) != spec["rows"]:
        raise ValueError("Client-Tabelle unvollständig")
    return rows


def _get(url):
    last = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "wow-ah-analytics/catalog-1"})
            with urllib.request.urlopen(req, timeout=60) as response:
                return response.read(40 * 1024 * 1024 + 1)
        except Exception as exc:
            last = exc
            if attempt < 2:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"Katalogquelle nicht erreichbar: {url}") from last


def update(con, refresh=False, fetch=_get, manifest=None):
    manifest = manifest or json.loads(MANIFEST.read_text(encoding="utf-8"))
    digest = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    if not refresh and db.get_meta(con, "catalog_manifest_hash") == digest:
        return coverage(con)
    # Verify exact live product/build association, not 'latest beta' or highest version.
    builds = json.loads(fetch(manifest["builds_url"]))
    if manifest["product"] != "wowf" or not any(
        b.get("version") == manifest["build"] and b.get("build_config") == manifest["build_config"]
        for b in builds.get("wowf", [])
    ):
        raise ValueError("Build gehört nicht zum freigegebenen Forever-Liveprodukt")
    tables = {}
    for spec in manifest["sources"]:
        tables[(spec["table"], spec["locale"])] = parse_csv(fetch(spec["url"]), spec)
    base = tables[("Item", "enUS")]
    de, en = tables[("ItemSparse", "deDE")], tables[("ItemSparse", "enUS")]
    if not (set(de) | set(en)) <= set(base):
        raise ValueError("ItemSparse enthält IDs außerhalb der Build-Basistabelle")
    records, metadata = [], []
    build = manifest["build"]
    for iid, b in base.items():
        d, e = de.get(iid, {}), en.get(iid, {})
        nd, ne = (d.get("Display_lang") or "").strip(), (e.get("Display_lang") or "").strip()
        readable = bool(nd or ne)
        test = bool(TEST_NAME.search(nd) or TEST_NAME.search(ne))
        fields = d or e
        binding = int(fields["Bonding"]) if fields else None
        records.append((iid, build, nd or None, ne or None, int(readable), int(test), binding))
        if readable:
            metadata.append((iid, nd or None, ne or None, int(fields["OverallQualityID"]),
                int(b["ClassID"]), CLASS_NAMES.get(int(b["ClassID"]), f"Klasse {b['ClassID']}"),
                int(b["SubclassID"]), int(fields["ItemLevel"]), int(fields["RequiredLevel"])))
    con.execute(SCHEMA)
    before = con.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    # Whole build activation is atomic. Missing/unreadable rows never delete old names or prices.
    with db.transaction(con):
        con.execute("DELETE FROM client_catalog")
        con.executemany("INSERT INTO client_catalog VALUES (?,?,?,?,?,?,?)", records)
        con.executemany("""INSERT INTO items(id,name,name_en,quality,class_id,class_name,subclass_id,item_level,required_level)
            VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
            name=CASE WHEN items.name IS NULL OR items.name='' OR items.name LIKE '[%]'
                 THEN COALESCE(excluded.name,items.name) ELSE items.name END,
            name_en=COALESCE(NULLIF(items.name_en,''),excluded.name_en),
            quality=COALESCE(items.quality,excluded.quality),
            class_id=COALESCE(items.class_id,excluded.class_id),
            class_name=COALESCE(items.class_name,excluded.class_name),
            subclass_id=COALESCE(items.subclass_id,excluded.subclass_id),
            item_level=COALESCE(items.item_level,excluded.item_level),
            required_level=COALESCE(items.required_level,excluded.required_level)""", metadata)
        con.execute("""UPDATE items SET catalog_build=?,catalog_state='outside_build',catalog_test=NULL,binding=NULL,catalog_aliases=NULL""", (build,))
        con.execute("""UPDATE items SET catalog_state=CASE WHEN c.readable=1 THEN 'readable' ELSE 'unreadable' END,
            catalog_test=c.test_hint,binding=c.binding FROM client_catalog c WHERE items.id=c.item_id""")
        con.executemany("UPDATE items SET catalog_aliases=? WHERE id=?",
            ((json.dumps([n for n in (nd, ne) if n], ensure_ascii=False), iid)
             for iid, _, nd, ne, _, _, _ in records))
        info = {"build": build, "product": manifest["product"], "imported_at": int(time.time()),
                "manifest_hash": digest, "rights": manifest["rights"], "policy_url": manifest["policy_url"],
                "sources": manifest["sources"], "added": con.execute("SELECT COUNT(*) FROM items").fetchone()[0] - before}
        db.set_meta(con, "catalog_info", json.dumps(info, ensure_ascii=False))
        db.set_meta(con, "catalog_manifest_hash", digest)
        db.bump_data_version(con)
        db.log_import(con, "client-catalog", "ok", f"Forever {build}: {len(metadata)} lesbar benannt, {info['added']} neue Items; keine Preise importiert")
    return coverage(con)


def coverage(con):
    raw = db.get_meta(con, "catalog_info")
    if not raw:
        return None
    info = json.loads(raw)
    info.update(dict(con.execute("""SELECT COUNT(*) base_ids, SUM(readable) readable_named,
        SUM(name_de IS NOT NULL) de_names, SUM(name_en IS NOT NULL) en_names,
        SUM(readable=0) unreadable, SUM(test_hint) test_hints FROM client_catalog""").fetchone()))
    info["imported_named"] = con.execute("SELECT COUNT(*) FROM client_catalog c JOIN items i ON i.id=c.item_id WHERE c.readable=1").fetchone()[0]
    info["search_items"] = con.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    info["observed_without_readable_client"] = con.execute("""SELECT COUNT(DISTINCT s.item_id) FROM item_summary s
        LEFT JOIN client_catalog c ON c.item_id=s.item_id WHERE COALESCE(c.readable,0)=0""").fetchone()[0]
    info["missing_de_total"] = con.execute("SELECT COUNT(*) FROM items WHERE name IS NULL OR name='' OR name LIKE '[%]'").fetchone()[0]
    return info
