"""Tests für die QuestieDB-Herkunft: CBOR/TOC-Leser, Import, Plausibilität, Update, Export."""
import base64
import io
import json
import os
import shutil
import struct
import sys
import tempfile
import unittest
import zipfile
import zlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ahanalytics import api, db, export, origins  # noqa: E402
from ahanalytics.sources import questie  # noqa: E402

QUIET = lambda *a: None  # noqa: E731


def _json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


# ------------------------------------------------------------ kleiner CBOR-Encoder für Testdaten
def _head(major, n):
    if n < 24:
        return bytes([major << 5 | n])
    for info, size in ((24, 1), (25, 2), (26, 4), (27, 8)):
        if n < 1 << (8 * size):
            return bytes([major << 5 | info]) + n.to_bytes(size, "big")
    raise ValueError(n)


def cbor(v):
    if v is None:
        return b"\xf6"
    if isinstance(v, bool):
        return b"\xf5" if v else b"\xf4"
    if isinstance(v, int):
        return _head(0, v) if v >= 0 else _head(1, -1 - v)
    if isinstance(v, float):
        return b"\xfb" + struct.pack(">d", v)
    if isinstance(v, bytes):
        return _head(2, len(v)) + v
    if isinstance(v, str):
        b = v.encode()
        return _head(3, len(b)) + b
    if isinstance(v, list):
        return _head(4, len(v)) + b"".join(cbor(x) for x in v)
    if isinstance(v, dict):
        return _head(5, len(v)) + b"".join(cbor(k) + cbor(x) for k, x in v.items())
    raise TypeError(v)


def b64(v):
    return base64.b64encode(cbor(v)).decode()


def entity(kind, ids, rows, chunk=None):
    """TOC-Zeilen: rows = {id: {feld: wert}}; Tabellen (list/dict) als eigene Felder, Rest im Skalar-Datensatz."""
    lines = [f"## X-{kind}-IDS: " + base64.b64encode(zlib.compress(cbor(ids))).decode()]
    for i, r in rows.items():
        scalar, mask = {}, 0
        for f, v in r.items():
            if isinstance(v, (list, dict)):
                mask |= 1 << (f - 1)
                val = b64(v)
                if chunk and (kind, i, f) == chunk:          # gestückelter Wert
                    parts = [val[:5], val[5:]]
                    lines.append(f"## X-{kind}-{i}-{f}: ~2~")
                    lines += [f"## X-{kind}-{i}-{f}-{n}: {p}" for n, p in enumerate(parts, 1)]
                else:
                    lines.append(f"## X-{kind}-{i}-{f}: {val}")
            else:
                scalar[f] = v
        scalar["p"] = mask
        lines.append(f"## X-{kind}-{i}-S: {b64(scalar)}")
    return lines


NPCS = {i: {1: f"Mob {i}".encode(), 4: i % 60 + 1, 5: i % 60 + 2, 9: 12} for i in range(1, 31)}
NPCS[1].update({1: b"Hogger", 6: 1})
NPCS[2].update({1: "Krämer".encode(), 13: b"A", 14: b"Stoffhandel"})
ITEMS = {
    750: {1: b"Tough Wolf Meat", 2: [1, 3, 4]},
    2770: {1: b"Copper Ore", 2: list(range(1, 31)), 3: [1731, 1732], 4: [9363]},
    2589: {1: b"Linen Cloth", 14: [2]},
    9363: {1: b"Kiste", 5: 40},
    774: {1: b"Malachite", 4: [9363]},
}
QUESTS = {33: {1: b"Wolves", 5: 2, 4: 1, 6: 77, 17: 12, 10: {3: [[750, None, 0]]}},
          40: {1: b"Hordisch", 5: 5, 6: 178, 21: [2589]}}
OBJECTS = {1731: {1: b"Copper Vein", 5: 12}, 1732: {1: b"Copper Vein", 5: 40}}
DROPS = """QuestieClassicItemDrops.wowheadData = [[return {
    [750] = { -- Tough Wolf Meat
        [1] = 80.0,
        [3] = 12.5,
    },
}]]"""


def toc():
    lines = ["## Interface: 16001", "## X-Flavor: Forever", "## X-BUILD-COMMIT: abcdef1234567890"]
    lines += entity("Item", list(ITEMS), ITEMS, chunk=("Item", 2770, 2))
    lines += entity("Quest", list(QUESTS), QUESTS)
    lines += entity("Npc", list(NPCS), NPCS)
    lines += entity("Object", list(OBJECTS), OBJECTS)
    return "\n".join(lines) + "\n"


def make_zip(path):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("QuestieDB/QuestieDB_Forever.toc", toc())
        z.writestr("QuestieDB/support/Forever/DropTables/classicItemDrops.lua", DROPS)


class Small:
    """Plausibilitätsgrenzen für die kleinen Testdaten senken."""

    def __enter__(self):
        self.old = origins.MIN_NPCS, origins.MIN_SOURCES
        origins.MIN_NPCS, origins.MIN_SOURCES = 1, 1

    def __exit__(self, *a):
        origins.MIN_NPCS, origins.MIN_SOURCES = self.old


class ReaderTest(unittest.TestCase):
    def test_cbor_roundtrip(self):
        for v in (0, 23, 24, 70000, -5, 1.5, "Wölfe", b"x", [1, [2, None]], {1: "a", "p": 3}, True):
            self.assertEqual(questie.cbor_loads(cbor(v)), v)
        self.assertEqual(questie.cbor_loads(b"\xf9\x3c\x00"), 1.0)            # half float
        self.assertEqual(questie.cbor_loads(b"\x9f\x01\x02\xff"), [1, 2])      # unbestimmte Länge

    def test_toc(self):
        q = questie.QuestieDB(toc())
        self.assertEqual(q.flavor, "Forever")
        self.assertEqual(sorted(q.ids("Item")), sorted(ITEMS))
        r = q.row("Item", 2770, questie.ITEM)
        self.assertEqual(r["npcDrops"], list(range(1, 31)))                  # gestückelt
        self.assertEqual(r["itemDrops"], [9363])
        self.assertIsNone(q.row("Item", 1, questie.ITEM))
        self.assertEqual(q.ids("Gibtsnicht"), [])
        q.drops_text = DROPS
        self.assertEqual(q.drop_chances(), {750: {1: 80.0, 3: 12.5}})


class ImportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.zip = os.path.join(self.tmp, "QuestieDB-1.0-Forever.zip")
        make_zip(self.zip)
        self.con = db.connect(":memory:")
        for i, n in ((750, "Zähes Wolfsfleisch"), (2770, "Kupfererz"), (9363, "Kiste"), (2589, "Leinenstoff")):
            self.con.execute("INSERT INTO items(id, name, meta_status) VALUES (?, ?, 'ok')", (i, n))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_parse_store_export(self):
        data = origins.parse(questie.QuestieDB.from_file(self.zip))
        self.assertIn((750, origins.NPC_DROP, 1, 80.0), data["sources"])
        self.assertIn((33, 750, origins.Q_OBJECTIVE), data["quest_items"])
        self.assertIn((40, 2589, origins.Q_SOURCE), data["quest_items"])
        self.assertIn((40, 9363, origins.Q_STARTS), data["quest_items"])
        q = {r[0]: r for r in data["quests"]}
        self.assertEqual((q[33][5], q[40][5]), ("Allianz", "Horde"))
        self.assertFalse(origins.store(self.con, data, "v1", log=QUIET))   # zu wenig für echte Grenzen
        with Small():
            self.assertTrue(origins.store(self.con, data, "v1", log=QUIET))
        self.con.execute("INSERT INTO zones VALUES (12, 'Wald von Elwynn')")
        d = origins.export_items(self.con, [750, 2770, 9363, 2589, 1])
        e = d["items"]
        self.assertEqual(e["750"]["d"][:2], [[1, 80.0], [3, 12.5]])         # nach Chance sortiert
        self.assertEqual(e["750"]["q"], [[33, 1]])
        self.assertEqual(e["2770"]["dn"], 30)
        self.assertEqual(len(e["2770"]["d"]), origins.MAX_NPCS if origins.MAX_NPCS < 30 else 30)
        self.assertEqual(e["2770"]["c"], [9363])
        self.assertEqual(e["9363"]["k"], [774, 2770])
        self.assertEqual(e["9363"]["q"], [[40, 3]])
        self.assertEqual(e["2589"]["v"], [2])
        self.assertNotIn("1", e)
        self.assertEqual(d["npcs"]["1"], ["Hogger", 2, 3, 1, "Wald von Elwynn", None, None])
        self.assertEqual(d["npcs"]["2"][5:], ["Allianz", "Stoffhandel"])
        self.assertEqual(d["objects"]["1731"], ["Copper Vein", "Wald von Elwynn"])
        self.assertEqual(d["quests"]["33"][0], "Wolves")
        self.assertEqual(d["names"]["774"][0], "Malachite")                  # englischer Ersatzname
        self.assertEqual(d["names"]["774"][4], 0)                            # keine eigene Seite
        self.assertEqual(d["names"]["9363"][4], 1)
        # Battle.net-Titel hat Vorrang
        self.con.execute("CREATE TABLE IF NOT EXISTS quests (id INTEGER PRIMARY KEY, title TEXT, zone TEXT, faction TEXT)")
        self.con.execute("INSERT INTO quests(id, title, zone, faction) VALUES (33, 'Wölfe an der Grenze', 'Nordhaintal', 'Allianz')")
        self.assertEqual(origins.export_items(self.con, [750])["quests"]["33"][0], "Wölfe an der Grenze")
        self.assertEqual(api.sources(self.con, 750)["items"]["750"]["q"], [[33, 1]])
        # kleinerer Abruf ersetzt den Bestand nicht
        small = dict(data, sources=data["sources"][:3])
        with Small():
            self.assertFalse(origins.store(self.con, small, "v2", log=QUIET))
        self.assertEqual(db.get_meta(self.con, "questie_tag"), "v1")

    def test_update_download_and_weekly_check(self):
        with open(self.zip, "rb") as fh:
            zbytes = fh.read()
        calls = []

        def opener(req, timeout=None):
            calls.append(req.full_url)
            if req.full_url == origins.RELEASE_API:
                body = json.dumps({"tag_name": "v1.1.0", "assets": [
                    {"name": "QuestieDB-1.1.0-Vanilla.zip", "browser_download_url": "https://x/v.zip"},
                    {"name": "QuestieDB-1.1.0-Forever.zip", "browser_download_url": "https://x/f.zip", "size": len(zbytes)}]}).encode()
            else:
                self.assertEqual(req.full_url, "https://x/f.zip")
                body = zbytes
            r = io.BytesIO(body)
            r.__enter__ = lambda *a: r
            r.__exit__ = lambda *a: None
            return r

        with Small():
            self.assertTrue(origins.update(self.con, opener=opener, log=QUIET))
            self.assertEqual(len(calls), 2)
            self.assertFalse(origins.update(self.con, opener=opener, log=QUIET))   # innerhalb einer Woche: kein Abruf
            self.assertEqual(len(calls), 2)
            db.set_meta(self.con, "questie_checked", 0)
            self.assertFalse(origins.update(self.con, opener=opener, log=QUIET))   # gleiches Release: nur API
            self.assertEqual(len(calls), 3)
            self.assertTrue(origins.update(self.con, path=self.zip, log=QUIET))    # lokale Datei
        self.assertEqual(db.get_meta(self.con, "questie_tag"), "lokal:QuestieDB-1.0-Forever.zip")

    def test_export_site_shards(self):
        with Small():
            origins.update(self.con, path=self.zip, log=QUIET)
        out = tempfile.mkdtemp()
        try:
            export.export_site(self.con, out, log=QUIET)
            meta = _json(os.path.join(out, "data", "meta.json"))
            self.assertEqual(meta["counts"]["source_shards"], origins.SHARDS)
            self.assertEqual(meta["counts"]["sources"], 4)
            sh = _json(os.path.join(out, "data", "sources", f"{750 % origins.SHARDS}.json"))
            self.assertIn("750", sh["items"])
            self.assertEqual(sh["source"]["name"], "QuestieDB")
        finally:
            shutil.rmtree(out, ignore_errors=True)

    def test_no_data(self):
        self.assertEqual(origins.export_items(self.con, [750])["items"], {})
        self.assertEqual(origins.export_shards(self.con, self.tmp), 0)


if __name__ == "__main__":
    unittest.main()
