"""Import der Auctionator-Preisdatenbank (SavedVariables/Auctionator.lua).

Format (Auctionator ≥ 10.x, ``__dbversion`` 8):

    AUCTIONATOR_PRICE_DATABASE = {
      ["__dbversion"] = 8,
      ["<Realm>"] = "<CBOR-Blob>",   -- oder bereits eine Lua-Tabelle
    }

Der CBOR-Blob ist eine Map ``dbKey -> {m, h, l, a}``:

* ``m``  zuletzt gesehener Mindestpreis
* ``h``  {Tag: höchster Mindestpreis des Tages}
* ``l``  {Tag: niedrigster Mindestpreis des Tages} (nur wenn ≠ h)
* ``a``  {Tag: maximal gesehene Stückzahl}

„Tag“ = Tage seit 01.01.2020 00:00 Ortszeit (Auctionator ``SCAN_DAY_0``).
dbKeys: ``"12345"`` (Item-ID), ``"g:12345:ilvl"`` / ``"gr:12345:..."``
(Ausrüstung mit Itemlevel/Varianten) und ``"p:..."`` (Kampfhaustiere,
werden übersprungen). Varianten eines Items werden zusammengeführt
(Minimum der Preise, Summe der Mengen).
"""
from __future__ import annotations

import glob
import os
import time

from .. import cbor, db, lua
from . import ImportResult

SOURCE = "auctionator"
SCAN_DAY_0 = int(time.mktime((2020, 1, 1, 0, 0, 0, 0, 0, -1)))


def find_savedvariables(wow_root: str = r"G:\World of Warcraft\_forever_") -> list[str]:
    pattern = os.path.join(wow_root, "WTF", "Account", "*", "SavedVariables", "Auctionator.lua")
    return sorted(glob.glob(pattern))


def _day_map(v) -> dict[int, int]:
    if not v or isinstance(v, list):
        if isinstance(v, list):  # Array-Form: Index = Tag (selten)
            return {i: x for i, x in enumerate(v) if isinstance(x, (int, float))}
        return {}
    out = {}
    for k, x in v.items():
        try:
            out[int(k)] = x
        except (TypeError, ValueError):
            continue
    return out


def parse_key(key: str) -> int | None:
    if key.isdigit():
        return int(key)
    parts = key.split(":")
    if parts[0] in ("g", "gr") and len(parts) > 1 and parts[1].isdigit():
        return int(parts[1])
    return None  # Pets ("p:") und Unbekanntes


def decode_realms(data: bytes) -> dict[str, dict]:
    """Liefert {realm: {dbKey: eintrag}} mit str-Schlüsseln."""
    sv = lua.parse_savedvariables(data)
    pdb = sv.get("AUCTIONATOR_PRICE_DATABASE")
    if not isinstance(pdb, dict):
        raise ValueError("AUCTIONATOR_PRICE_DATABASE nicht gefunden – ist das die Auctionator.lua?")
    realms = {}
    for realm, blob in pdb.items():
        realm = realm.decode("utf-8", "replace") if isinstance(realm, bytes) else str(realm)
        if realm == "__dbversion":
            continue
        if isinstance(blob, bytes):
            try:
                content = cbor.loads(blob)
            except Exception as exc:  # noqa: BLE001
                raise ValueError(f"CBOR-Daten von Realm {realm!r} nicht lesbar: {exc}") from exc
        else:
            content = blob
        content = lua.decode_strings(content)
        if isinstance(content, dict):
            content = {k: v for k, v in content.items() if isinstance(v, dict)}
            realms[realm] = content
    return realms


def realm_series(entries: dict) -> dict[int, dict[int, dict]]:
    """{item_id: {ts: stats}} aus einem Realm-Inhalt."""
    out: dict[int, dict[int, dict]] = {}
    for key, e in entries.items():
        item_id = parse_key(str(key))
        if item_id is None:
            continue
        h, l, a = _day_map(e.get("h")), _day_map(e.get("l")), _day_map(e.get("a"))
        for day, hi in h.items():
            lo = l.get(day, hi)
            ts = SCAN_DAY_0 + day * 86400
            qty = a.get(day)
            cur = out.setdefault(item_id, {}).get(ts)
            if cur is None:
                out[item_id][ts] = {"min": int(lo), "max": int(hi), "quantity": qty,
                                    "p10": None, "median": None, "mean": None, "auctions": None}
            else:  # Variante desselben Items
                cur["min"] = min(cur["min"], int(lo))
                cur["max"] = max(cur["max"], int(hi))
                if qty is not None:
                    cur["quantity"] = (cur["quantity"] or 0) + qty
    return out


def import_bytes(con, data: bytes, region: str = "eu", label: str = "") -> ImportResult:
    res = ImportResult(SOURCE)
    realms = decode_realms(data)
    touched = []
    with db.transaction(con):
        for realm, entries in realms.items():
            series = realm_series(entries)
            if not series:
                res.messages.append(f"Realm {realm}: keine Preisdaten")
                continue
            hid = db.upsert_house(con, SOURCE, realm, f"{realm} (Auctionator)",
                                  region=region, faction="", ruleset=realm, granularity="day")
            touched.append(hid)
            res.houses += 1
            by_ts: dict[int, dict[int, dict]] = {}
            for iid, pts in series.items():
                for ts, st in pts.items():
                    by_ts.setdefault(ts, {})[iid] = st
            for ts, stats in sorted(by_ts.items()):
                db.write_snapshot(con, hid, ts, SOURCE, stats)
                res.snapshots += 1
                res.price_points += len(stats)
            res.items += len(series)
        if touched:
            db.refresh_summary(con, touched)
            db.bump_data_version(con)
        db.log_import(con, SOURCE, "ok", (label + ": " if label else "") + res.summary())
    return res


def import_file(con, path: str, region: str = "eu") -> ImportResult:
    with open(path, "rb") as fh:
        data = fh.read()
    return import_bytes(con, data, region=region, label=os.path.basename(path))
