"""Leser für die QuestieDB (WoW-Addon, Forever-Release) – ohne WoW-Client und ohne Abhängigkeiten.

Die Daten stecken in der TOC-Datei als Metadaten-Zeilen ``## X-<Typ>-<ID>-<Feld>: <Base64>``:
  * ``X-<Typ>-IDS``      zlib-komprimierte CBOR-Liste aller IDs
  * ``X-<Typ>-<ID>-S``   CBOR-Zeile mit den Skalarfeldern (Feldnummer → Wert) und Bitmaske ``p``
                         (welche Tabellenfelder vorhanden sind)
  * ``X-<Typ>-<ID>-<n>`` CBOR des Tabellenfelds n
Lange Werte sind gestückelt: Wert ``~N~``, Teile unter ``<Schlüssel>-1`` … ``-N``.

Quelle: https://github.com/Questie/QuestieDB – Code GPLv3, Daten laut LICENSE.md
„im Zweifel all rights reserved“; Nutzung hier auf ausdrücklichen Wunsch, mit Quellenangabe.
"""
from __future__ import annotations

import base64
import re
import struct
import zipfile
import zlib

# Feldnummern (src/meta/*Meta.lua)
ITEM = {"name": 1, "npcDrops": 2, "objectDrops": 3, "itemDrops": 4, "startQuest": 5, "questRewards": 6,
        "class": 12, "vendors": 14, "relatedQuests": 15}
QUEST = {"name": 1, "requiredLevel": 4, "questLevel": 5, "requiredRaces": 6, "objectives": 10,
         "sourceItemId": 11, "zoneOrSort": 17, "requiredSourceItems": 21}
NPC = {"name": 1, "minLevel": 4, "maxLevel": 5, "rank": 6, "zoneID": 9, "friendlyToFaction": 13, "subName": 14,
       "npcFlags": 15}
OBJECT = {"name": 1, "zoneID": 5}


# ------------------------------------------------------------------ CBOR

class _Cbor:
    def __init__(self, data: bytes):
        self.b, self.i = data, 0

    def _arg(self, info):
        if info < 24:
            return info
        n = {24: 1, 25: 2, 26: 4, 27: 8}.get(info)
        if n is None:
            if info == 31:
                return None                     # unbestimmte Länge
            raise ValueError(f"CBOR: ungültige Länge {info}")
        v = int.from_bytes(self.b[self.i:self.i + n], "big")
        self.i += n
        return v

    def read(self):
        ib = self.b[self.i]
        self.i += 1
        major, info = ib >> 5, ib & 31
        if major == 7:
            if info == 20:
                return False
            if info == 21:
                return True
            if info in (22, 23):
                return None
            if info == 25:
                v = struct.unpack(">e", self.b[self.i:self.i + 2])[0]; self.i += 2; return v  # noqa: E702
            if info == 26:
                v = struct.unpack(">f", self.b[self.i:self.i + 4])[0]; self.i += 4; return v  # noqa: E702
            if info == 27:
                v = struct.unpack(">d", self.b[self.i:self.i + 8])[0]; self.i += 8; return v  # noqa: E702
            if info == 31:
                return StopIteration
            return info
        n = self._arg(info)
        if major == 0:
            return n
        if major == 1:
            return -1 - n
        if major in (2, 3):
            if n is None:                       # gestückelter String
                parts = []
                while (x := self.read()) is not StopIteration:
                    parts.append(x)
                return (b"" if major == 2 else "").join(parts)
            raw = self.b[self.i:self.i + n]
            self.i += n
            return bytes(raw) if major == 2 else raw.decode("utf-8", "replace")
        if major == 4:
            out = []
            while n is None or len(out) < n:
                x = self.read()
                if x is StopIteration:
                    break
                out.append(x)
            return out
        if major == 5:
            out = {}
            k = 0
            while n is None or k < n:
                key = self.read()
                if key is StopIteration:
                    break
                out[key] = self.read()
                k += 1
            return out
        if major == 6:
            return self.read()                  # Tags ignorieren
        raise ValueError("CBOR: unbekannter Typ")


def cbor_loads(data: bytes):
    return _Cbor(data).read()


def lua_list(v):
    """Lua-Tabellen kommen als Liste oder als Map {1: …, 2: …}; einheitlich als Liste."""
    if isinstance(v, dict):
        return [v[k] for k in sorted(k for k in v if isinstance(k, int))]
    return v if isinstance(v, list) else []


# ------------------------------------------------------------------ TOC

_LINE = re.compile(r"^## (X-[^:]+): ?(.*)$")
_CHUNK = re.compile(r"^~(\d+)~$")
_DROP_ITEM = re.compile(r"^ {4}\[(\d+)\] = \{")
_DROP_NPC = re.compile(r"^ {8}\[(\d+)\] = ([\d.]+)")


class QuestieDB:
    def __init__(self, toc_text: str):
        self.meta = {}
        for line in toc_text.splitlines():
            m = _LINE.match(line)
            if m:
                self.meta[m.group(1)] = m.group(2).strip()
        self.version = self.meta.get("X-BUILD-COMMIT", "")[:12]
        self.flavor = self.meta.get("X-Flavor", "")

    drops_text = ""

    @classmethod
    def from_file(cls, path: str):
        """Release-Zip (QuestieDB-…-Forever.zip) oder installierter Addon-Ordner."""
        toc, drops = None, ""
        if path.lower().endswith(".zip"):
            with zipfile.ZipFile(path) as z:
                names = z.namelist()
                tocs = [n for n in names if n.endswith("_Forever.toc")]
                if not tocs:
                    raise ValueError("QuestieDB-Zip enthält keine *_Forever.toc")
                toc = z.read(tocs[0]).decode("utf-8")
                dn = [n for n in names if n.replace("\\", "/").endswith("Forever/DropTables/classicItemDrops.lua")]
                if dn:
                    drops = z.read(dn[0]).decode("utf-8", "replace")
        else:
            import os
            with open(os.path.join(path, "QuestieDB_Forever.toc"), encoding="utf-8") as fh:
                toc = fh.read()
            dp = os.path.join(path, "support", "Forever", "DropTables", "classicItemDrops.lua")
            if os.path.exists(dp):
                with open(dp, encoding="utf-8", errors="replace") as fh:
                    drops = fh.read()
        db = cls(toc)
        db.drops_text = drops
        return db

    def drop_chances(self) -> dict[int, dict[int, float]]:
        """Dropchancen (in %) je Item und NPC aus classicItemDrops.lua (Wowhead-Werte, nur Quest-Items)."""
        out, cur = {}, None
        for line in self.drops_text.splitlines():
            m = _DROP_ITEM.match(line)
            if m:
                cur = out.setdefault(int(m.group(1)), {})
                continue
            m = _DROP_NPC.match(line)
            if m and cur is not None:
                cur[int(m.group(1))] = float(m.group(2))
        return {k: v for k, v in out.items() if v}

    def _stored(self, key):
        v = self.meta.get(key)
        if v is None:
            return None
        m = _CHUNK.match(v)
        if m:
            parts = [self.meta.get(f"{key}-{i}") for i in range(1, int(m.group(1)) + 1)]
            if any(p is None for p in parts):
                raise ValueError(f"QuestieDB: Teile von {key} fehlen")
            v = "".join(parts)
        return v

    def ids(self, kind: str) -> list[int]:
        raw = self._stored(f"X-{kind}-IDS")
        if raw is None:
            return []
        return [int(x) for x in lua_list(cbor_loads(zlib.decompress(base64.b64decode(raw))))]

    def row(self, kind: str, id_: int, fields: dict) -> dict | None:
        """Gewünschte Felder eines Eintrags: {Name: Wert}; fehlende Felder fehlen im Ergebnis."""
        s = self._stored(f"X-{kind}-{id_}-S")
        if s is None:
            return None
        scalar = cbor_loads(base64.b64decode(s))
        if isinstance(scalar, list):
            scalar = {i + 1: v for i, v in enumerate(scalar)}
        out = {}
        for name, idx in fields.items():
            if idx in scalar and scalar[idx] is not None:
                out[name] = scalar[idx]
                continue
            t = self._stored(f"X-{kind}-{id_}-{idx}")
            if t is not None:
                out[name] = cbor_loads(base64.b64decode(t))
        return out
