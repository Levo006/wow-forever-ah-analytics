import datetime as dt
import gzip
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('archive_rulesets', Path(__file__).parents[1] / 'scripts' / 'archive_rulesets.py')
archive = importlib.util.module_from_spec(spec)
spec.loader.exec_module(archive)


class RulesetArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = self.root / 'input.sqlite'
        self.out = self.root / 'out'
        with sqlite3.connect(self.db) as c:
            c.executescript('''
            CREATE TABLE houses(id INTEGER PRIMARY KEY, ext_id TEXT, source TEXT, granularity TEXT);
            CREATE TABLE prices(house_id INTEGER,item_id INTEGER,ts INTEGER,min INTEGER,quantity INTEGER,PRIMARY KEY(house_id,item_id,ts));
            CREATE TABLE snapshots(id INTEGER PRIMARY KEY,house_id INTEGER,ts INTEGER,complete INTEGER,run_id INTEGER);
            CREATE TABLE market_runs(id INTEGER PRIMARY KEY,manifest TEXT);
            CREATE TABLE items(id INTEGER PRIMARY KEY,name TEXT);
            INSERT INTO items VALUES(42,'Test');
            INSERT INTO market_runs VALUES(7,'partial source evidence');
            ''')
            for hid, ext in enumerate(archive.EXT_IDS, 1):
                c.execute('INSERT INTO houses VALUES(?,?,?,?)', (hid, ext, 'battlenet', 'hour'))
                for n, date in enumerate(('2026-11-11', '2026-11-12', '2026-11-13', '2026-12-13', '2026-12-14')):
                    ts = int(dt.datetime.fromisoformat(date).replace(tzinfo=dt.timezone.utc).timestamp()) + 1800
                    c.execute('INSERT INTO prices VALUES(?,?,?,101,8)', (hid, 42, ts))
                    c.execute('INSERT INTO snapshots VALUES(?,?,?,?,7)', (hid*10+n, hid, ts, 0 if hid==1 else 1))
                daily = int(dt.datetime(2026,11,12,tzinfo=dt.timezone.utc).timestamp()) + archive.DAILY_OFFSET
                c.execute('INSERT INTO prices VALUES(?,?,?,999,1)', (hid,42,daily))

    def tearDown(self):
        self.tmp.cleanup()

    def test_roundtrip_bounds_partial_and_source_untouched(self):
        before = archive.sha256(self.db)
        rows = archive.export(self.db, self.out, 'fixture', dt.datetime(2026,12,15,tzinfo=dt.timezone.utc))
        self.assertEqual([r['day_utc'] for r in rows], ['2026-11-12','2026-11-13','2026-12-13'])
        self.assertEqual(before, archive.sha256(self.db))
        for manifest in rows:
            self.assertEqual(sum(h['price_rows'] for h in manifest['houses']),12)
            packed = self.out / ('ah-rulesets-v2-'+manifest['day_utc']+'.sqlite.gz')
            raw = self.root / 'check.sqlite'
            raw.write_bytes(gzip.decompress(packed.read_bytes()))
            self.assertEqual(archive.sha256(raw),manifest['sqlite_sha256'])
            self.assertEqual(archive.sha256(packed),manifest['gzip_sha256'])
            with sqlite3.connect(raw) as c:
                self.assertEqual(c.execute('SELECT min FROM prices').fetchall(),[(101,)]*12)
                self.assertEqual(c.execute('SELECT complete FROM snapshots ORDER BY house_id').fetchall(),[(0,)]+[(1,)]*11)
                self.assertEqual(c.execute('SELECT manifest FROM market_runs').fetchone()[0], 'partial source evidence')
        self.assertEqual(archive.export(self.db,self.out,'fixture',dt.datetime(2026,12,15,tzinfo=dt.timezone.utc)), [])

    def test_open_day_not_exported(self):
        rows=archive.export(self.db,self.out,'fixture',dt.datetime(2026,11,13,tzinfo=dt.timezone.utc))
        self.assertEqual([r['day_utc'] for r in rows],['2026-11-12'])

    def test_missing_market_fails_closed(self):
        with sqlite3.connect(self.db) as c:
            c.execute('DELETE FROM houses WHERE id=3')
        with self.assertRaises(ValueError):
            archive.export(self.db,self.out,'fixture')

    def test_orphan_price_rejected(self):
        with sqlite3.connect(self.db) as c:
            c.execute('DELETE FROM snapshots WHERE id=11')
        with self.assertRaises(ValueError):
            archive.export(self.db,self.out,'fixture',dt.datetime(2026,11,13,tzinfo=dt.timezone.utc))


if __name__ == '__main__':
    unittest.main()

class RulesetCutoffTest(RulesetArchiveTests):
    def test_slow_house_does_not_block_other_markets(self):
        with sqlite3.connect(self.db) as c:
            limit=int(dt.datetime(2026,11,13,tzinfo=dt.timezone.utc).timestamp())
            c.execute('DELETE FROM prices WHERE house_id=1 AND ts>=?',(limit,))
            c.execute('DELETE FROM snapshots WHERE house_id=1 AND ts>=?',(limit,))
        rows=archive.export(self.db,self.out,'fixture',dt.datetime(2026,12,15,tzinfo=dt.timezone.utc))
        self.assertIn('2026-12-13',[r['day_utc'] for r in rows])
        last=rows[-1]
        self.assertEqual(next(h for h in last['houses'] if h['house_id']==1)['price_rows'],0)
    def test_existing_asset_never_rebuilt(self):
        rows=archive.export(self.db,self.out,'fixture',dt.datetime(2026,11,13,tzinfo=dt.timezone.utc),existing=['ah-rulesets-v2-2026-11-12.tar'])
        self.assertEqual(rows,[])
