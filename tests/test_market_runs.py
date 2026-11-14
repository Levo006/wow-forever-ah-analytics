"""Endpoint evidence, safe publication, price semantics and time-window regressions."""
import copy
from email.utils import formatdate
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from ahanalytics import api, db, export
from ahanalytics.sources import battlenet

NOW = 1_794_686_400
QUIET = lambda *a: None

class Client:
    region = 'eu'
    def __init__(self):
        self.houses = [{'id':110,'name':'Alliance Auction House','ruleset':1},
                       {'id':111,'name':'Alliance Auction House','ruleset':1}]
        self.data = {}
        for h in self.houses:
            for end in ('','/commodities'):
                p=f"/data/wow/ruleset/1/auctions/{h['id']}{end}"
                a=[] if end else [{'id':h['id'],'item':{'id':1},'buyout':100*h['id'],'quantity':2}]
                self.data[p]=({'auctions':a},{'Last-Modified':formatdate(NOW-60,usegmt=True)})
    def ns(self,kind): return f'{kind}-forever-eu'
    def get(self,path):
        val=self.data[path]
        if isinstance(val,Exception): raise val
        return copy.deepcopy(val)

class MarketRunsTest(unittest.TestCase):
    def setUp(self):
        self.con=db.connect(':memory:');self.client=Client()
        self.base='/data/wow/ruleset/1/auctions/111'
        self.discovery=patch.object(battlenet,'discover_houses',side_effect=lambda *a:self.client.houses)
        self.discovery.start()
    def tearDown(self): self.discovery.stop();self.con.close()
    def collect(self,**kw): return battlenet.collect(self.con,self.client,fetch_metadata=False,log=QUIET,now=NOW,**kw)
    def manifest(self): return api.market_runs(self.con)[0]
    def house(self,ext):return self.con.execute('SELECT id FROM houses WHERE ext_id=?',(ext,)).fetchone()[0]
    def test_complete_manifest_and_lowest(self):
        self.collect();m=self.manifest()
        self.assertEqual(m['status'],'complete');self.assertEqual(len(m['endpoints']),4)
        self.assertEqual(m['expected_houses'],[110,111]);self.assertTrue(all(len(e['sha256'])==64 for e in m['endpoints']))
        r=api.item(self.con,1,{})['houses'];g=next(x for x in r if x['kind']=='allianz')
        self.assertTrue(g['market_complete']);self.assertEqual(g['last_lowest'],5500)
        self.assertEqual(g['price_basis'],'robust_reference')
    def test_unknown_array_is_not_empty_and_no_house_overwrite(self):
        self.collect();hid=self.house('eu-111');old=self.con.execute('SELECT * FROM prices WHERE house_id=?',(hid,)).fetchall()
        self.client.data[self.base]=({'id':111},{'Last-Modified':formatdate(NOW+60,usegmt=True)})
        self.collect();m=self.manifest();self.assertEqual(m['status'],'partial')
        self.assertEqual(self.con.execute('SELECT * FROM prices WHERE house_id=?',(hid,)).fetchall(),old)
        self.assertIn('nicht leer',next(e for e in m['endpoints'] if e['path']==self.base)['error'])
    def test_confirmed_empty_snapshot_proves_absence(self):
        self.collect()
        for p in self.client.data:self.client.data[p]=({'auctions':[]},{})
        with patch('ahanalytics.sources.battlenet.time.time',return_value=NOW+120):
            battlenet.collect(self.con,self.client,fetch_metadata=False,log=QUIET,now=NOW+120)
        self.assertEqual(self.manifest()['status'],'complete')
        row=api.search(self.con,{'q':'1','house':str(self.house('eu-allianz'))})['items'][0]
        self.assertFalse(row['is_current']);self.assertEqual(row['current_quantity'],0)
    def test_endpoint_failures_keep_full_evidence(self):
        self.client.data[self.base]=battlenet.ApiError(404,self.base)
        self.collect();m=self.manifest();self.assertEqual(len(m['endpoints']),4)
        self.assertEqual(next(e for e in m['endpoints'] if e['path']==self.base)['http_status'],404)
        self.assertEqual(m['status'],'partial')
    def test_source_validation(self):
        cases=[({'auctions':[]}, {},'empty'),
               ({'auctions':[{'id':1,'item':{'id':1},'buyout':10,'quantity':1}]},{},'error'),
               ({'auctions':[{'item':{'id':1},'quantity':0,'buyout':3}]},{'Last-Modified':formatdate(NOW,usegmt=True)},'error')]
        for payload,headers,expected in cases:
            with self.subTest(expected=expected,headers=headers):
                self.client.data[self.base]=(payload,headers);self.collect()
                self.assertEqual(next(e for e in self.manifest()['endpoints'] if e['path']==self.base)['status'],expected)
    def test_stale_future_and_skew(self):
        for delta in (-20000,10000):
            self.client.data[self.base]=(copy.deepcopy(Client().data[self.base][0]),{'Last-Modified':formatdate(NOW+delta,usegmt=True)})
            self.collect();self.assertEqual(next(e for e in self.manifest()['endpoints'] if e['path']==self.base)['status'],'error')
        self.client=Client();payload,headers=self.client.data[self.base+'/commodities']
        payload['auctions']=[{'item':{'id':1},'unit_price':1,'quantity':1}]
        headers['Last-Modified']=formatdate(NOW-1200,usegmt=True)
        self.collect();self.assertEqual(next(e for e in self.manifest()['endpoints'] if e['path']==self.base)['status'],'invalid')
    def test_identical_payload_idempotent(self):
        self.collect();count=self.con.execute('SELECT COUNT(*) FROM prices').fetchone()[0]
        self.collect();self.assertEqual(self.con.execute('SELECT COUNT(*) FROM prices').fetchone()[0],count)
        self.assertEqual(len(api.market_runs(self.con)),2)
        self.assertTrue(any(g['activation']=='unchanged' for g in self.manifest()['groups']))
    def test_same_source_time_changed_payload_rejected(self):
        self.collect();self.client.data[self.base][0]['auctions'][0]['buyout']=1
        self.collect();self.assertIn('gleicher Quellzeit',next(e for e in self.manifest()['endpoints'] if e['path']==self.base)['error'])
        self.assertEqual(self.con.execute('SELECT min FROM prices WHERE house_id=?',(self.house('eu-111'),)).fetchone()[0],5550)
    def test_atomic_failure(self):
        real=db.write_snapshot;calls=[]
        def fail(*args,**kw):
            calls.append(1)
            if len(calls)>1:raise RuntimeError('disk fixture')
            return real(*args,**kw)
        with patch.object(db,'write_snapshot',side_effect=fail),self.assertRaises(RuntimeError):self.collect()
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM prices').fetchone()[0],0)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM houses').fetchone()[0],0)
        self.assertEqual(self.manifest()['status'],'rejected')
    def test_discovery_failure_recorded(self):
        self.client.houses=[]
        with self.assertRaises(ValueError):self.collect()
        self.assertEqual(self.manifest()['status'],'rejected')
    def test_missing_known_house_does_not_claim_complete(self):
        self.collect();self.client.houses=self.client.houses[:1];self.collect()
        self.assertEqual(self.manifest()['status'],'partial');self.assertTrue(self.manifest()['errors'])
    def test_subset_does_not_rebuild_groups(self):
        self.collect(only_houses=[110]);self.assertEqual(self.manifest()['status'],'partial')
        self.assertFalse(self.con.execute("SELECT 1 FROM houses WHERE ext_id='eu-allianz'").fetchone())
    def test_retry_after(self):
        self.assertEqual(battlenet._retry_delay({'Retry-After':'10'},0),10)
        with self.assertRaises(battlenet.ApiError):battlenet._retry_delay({'Retry-After':'500'},0)
    def test_static_manifest_and_analytics_parity(self):
        self.collect()
        hid=self.house('eu-allianz')
        with tempfile.TemporaryDirectory() as tmp:
            export.export_site(self.con,tmp,log=QUIET)
            meta=json.loads((Path(tmp)/'data/meta.json').read_text())
            self.assertEqual(meta['market_runs'][0]['id'],self.manifest()['id'])
            target=api.overview(self.con,{'house':str(hid)})
            Path(tmp,'expected.json').write_text(json.dumps(target['kpis']))
            script=Path(__file__).with_name('market_runs.test.js')
            result=subprocess.run(['node',str(script),tmp,str(hid)],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)

class PriceTruthTest(unittest.TestCase):
    def test_market_anchored_window_and_missing_reference(self):
        c=db.connect(':memory:');h=db.upsert_house(c,'battlenet','eu-110','A')
        for ts,st in [(NOW-2*86400,{1:{'min':1,'quantity':1}}),(NOW-86400,{1:{'min':100,'quantity':1}}),
                      (NOW,{2:{'min':50,'quantity':1}})]: db.write_snapshot(c,h,ts,'t',st)
        db.refresh_summary(c)
        row=c.execute('SELECT * FROM item_summary WHERE item_id=1').fetchone()
        self.assertEqual(row['prev24_min'],100) # anchored to market, not last item point
        self.assertEqual(row['prev24_ts'],NOW-86400)
        c.execute('DELETE FROM prices WHERE ts=?',(NOW-86400,));db.refresh_summary(c)
        self.assertIsNone(c.execute('SELECT prev24_min FROM item_summary WHERE item_id=1').fetchone()[0]);c.close()
    def test_legacy_daily_count_unknown(self):
        ts=NOW//86400*86400+db.DAILY_OFFSET
        result=export.compact_history([{'ts':ts,'min':1}],ts)
        self.assertIsNone(result[0]['n']);self.assertTrue(result[0]['aggregated'])
    def test_compaction_keeps_actual_sample_count_and_extrema(self):
        c=db.connect(':memory:');h=db.upsert_group_house(c,'battlenet','eu','allianz')
        ts=NOW//86400*86400-10*86400
        for t,p in [(ts,10),(ts+3600,100),(ts+10*86400,20)]:db.write_snapshot(c,h,t,'t',{1:{'min':p,'quantity':1}})
        db.compact(c,log=QUIET)
        row=c.execute('SELECT sample_n,reference_low,reference_high FROM prices WHERE ts%86400=?',(db.DAILY_OFFSET,)).fetchone()
        self.assertEqual(tuple(row),(2,10,100))
        # A later compaction must not erase provenance of a day whose raw rows are gone.
        db.write_snapshot(c,h,ts+86400,'t',{2:{'min':7,'quantity':1}})
        db.compact(c,log=QUIET)
        row=c.execute('SELECT sample_n,reference_low,reference_high FROM prices WHERE item_id=1 AND ts%86400=?',(db.DAILY_OFFSET,)).fetchone()
        self.assertEqual(tuple(row),(2,10,100));c.close()

if __name__=='__main__':unittest.main()

class RobustOverviewTest(unittest.TestCase):
    def test_median_resists_tiny_base_outlier_with_exact_denominator(self):
        import time
        c=db.connect(':memory:');h=db.upsert_house(c,'battlenet','eu-110','A');now=int(time.time())//60*60
        db.write_snapshot(c,h,now-86400,'t',{1:{'min':100,'quantity':1},2:{'min':100,'quantity':1},3:{'min':1,'quantity':1},4:{'min':5,'quantity':1}})
        sid=db.write_snapshot(c,h,now,'t',{1:{'min':101,'quantity':1},2:{'min':102,'quantity':1},3:{'min':1500,'quantity':1},5:{'min':3,'quantity':1}})
        c.execute('UPDATE snapshots SET complete=1 WHERE id=?',(sid,));db.refresh_summary(c)
        k=api.overview(c,{'house':str(h)})['kpis']
        self.assertEqual(k['comparable24'],3);self.assertAlmostEqual(k['median_chg24'],2)
        self.assertGreater(k['avg_chg24'],1000);self.assertTrue(k['comparisons_reliable'])
        c.close()
