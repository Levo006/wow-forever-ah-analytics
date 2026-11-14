"""Deterministic catalog import, atomicity, price preservation and static parity."""
import csv
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from ahanalytics import api, catalog, db, export

ROOT = Path(__file__).resolve().parents[1]


def fixture():
    specs, files = [], {}
    for table, locale, headers, values in [
        ('Item', 'enUS', ['ID','ClassID','SubclassID'], [[25,2,7],[7753,2,1],[8226,2,7],[900,4,1],[901,4,1]]),
        ('ItemSparse','deDE',['ID','Display_lang','Bonding','OverallQualityID','RequiredLevel','ItemLevel'],
         [[25,'Abgenutztes Kurzschwert',0,1,1,2],[900,'[DNT] Testhelm',1,2,10,15],[901,'Alte Stiefel',2,2,10,15]]),
        ('ItemSparse','enUS',['ID','Display_lang','Bonding','OverallQualityID','RequiredLevel','ItemLevel'],
         [[25,'Worn Shortsword',0,1,1,2],[900,'[DNT] Test Helm',1,2,10,15],[901,'Old Boots',2,2,10,15]])]:
        f=io.StringIO();w=csv.writer(f);w.writerow(headers);w.writerows(values);raw=f.getvalue().encode()
        url=f'https://fixture/{table}/{locale}'
        specs.append(dict(table=table,locale=locale,url=url,sha256=hashlib.sha256(raw).hexdigest(),rows=len(values)))
        files[url]=raw
    m=dict(version=1,product='wowf',build='1.60.1.70938',build_config='fixture',builds_url='https://fixture/builds',
           rights='fixture only',policy_url='https://fixture/policy',sources=specs)
    files[m['builds_url']]=json.dumps({'wowf':[{'version':m['build'],'build_config':'fixture'}]}).encode()
    return m, files


class CatalogTest(unittest.TestCase):
    def setUp(self):
        self.c=db.connect(':memory:');self.m,self.files=fixture()
        self.h=db.upsert_house(self.c,'test','a','Allianz',faction='Allianz')
        db.upsert_item(self.c,7753,name='[Bloodspiller]',name_en='Bloodspiller')
        db.upsert_item(self.c,42,name='Bewahrter Servername',name_en='Kept Server Name')
        db.write_snapshot(self.c,self.h,1794000000,'test',{7753:{'min':1234,'quantity':2}})
        db.refresh_summary(self.c)

    def tearDown(self):
        self.c.close()

    def update(self, **kwargs):
        return catalog.update(self.c,fetch=self.files.__getitem__,manifest=self.m,**kwargs)

    def state(self):
        return {t:[tuple(x) for x in self.c.execute('SELECT * FROM '+t+' ORDER BY 1')] for t in
                ('items','snapshots','prices','ladders','meta')}

    def test_full_readable_import_no_market_or_translation_invention(self):
        prices=[tuple(x) for x in self.c.execute('select * from prices')]
        c=self.update()
        self.assertEqual((c['base_ids'],c['readable_named'],c['imported_named'],c['unreadable']), (5,3,3,2))
        self.assertEqual(c['test_hints'],1)
        self.assertEqual(prices,[tuple(x) for x in self.c.execute('select * from prices')])
        self.assertEqual(api.item(self.c,42,{})['item']['name'],'Bewahrter Servername')
        x=api.item(self.c,7753,{})['item']
        self.assertEqual(x['name'],'Bloodspiller');self.assertTrue(x['translation_missing'])
        self.assertEqual(x['catalog_state'],'unreadable')
        self.assertEqual(api.item(self.c,25,{})['item']['catalog_state'],'readable')
        self.assertEqual(api.item(self.c,25,{})['item']['name'],'Abgenutztes Kurzschwert')
        self.assertFalse(self.c.execute('select 1 from items where id=8226').fetchone())
        self.assertEqual(api.search(self.c,{'q':'Abgenutztes','house':str(self.h)})['items'][0]['last_min'],None)
        self.assertEqual(api.status(self.c,{})['catalog_coverage']['imported_named'],3)

    def test_repeat_is_idempotent_and_offline_after_success(self):
        self.update();before=self.state();self.files.clear();self.update();self.assertEqual(before,self.state())

    def test_network_corruption_build_mismatch_leave_state_unchanged(self):
        self.update();before=self.state()
        for mode in ('hash','network','product','rows','schema','ids'):
            with self.subTest(mode=mode):
                self.m,self.files=fixture()
                spec=self.m['sources'][0]
                if mode=='hash':self.files[spec['url']]+=b'broken'
                elif mode=='network':del self.files[spec['url']]
                elif mode=='product':self.m['product']='wow_classic_era'
                elif mode=='rows':spec['rows']=999
                elif mode=='schema':
                    raw=b'ID,bad\n25,1\n';self.files[spec['url']]=raw;spec['sha256']=hashlib.sha256(raw).hexdigest()
                elif mode=='ids':
                    raw=b'ID,ClassID,SubclassID\n25,2,7\n25,2,7\n';self.files[spec['url']]=raw;spec['sha256']=hashlib.sha256(raw).hexdigest()
                with self.assertRaises((KeyError,ValueError)):self.update(refresh=True)
                self.assertEqual(before,self.state())

    def test_locale_fills_fallback_without_clobbering_server_names(self):
        db.upsert_item(self.c,25,name='[Worn Shortsword]',name_en='Worn Shortsword')
        self.update()
        self.assertEqual(api.item(self.c,25,{})['item']['name'],'Abgenutztes Kurzschwert')
        self.assertEqual(self.c.execute('select meta_status from items where id=25').fetchone()[0],'ok')
        db.upsert_item(self.c,25,name='Bestätigter Servername')
        self.update(refresh=True)
        self.assertEqual(api.item(self.c,25,{})['item']['name'],'Bestätigter Servername')
        self.assertEqual(api.search(self.c,{'q':'Abgenutztes Kurzschwert','house':str(self.h)})['total'],1)

    def test_atomic_activation_rolls_back_on_storage_error(self):
        self.update();before=self.state()
        self.c.execute("CREATE TRIGGER fail_catalog BEFORE UPDATE ON items BEGIN SELECT RAISE(ABORT,'fixture failure'); END")
        with self.assertRaises(Exception):self.update(refresh=True)
        self.assertEqual(before,self.state())
        self.assertEqual(self.c.execute('select count(*) from client_catalog').fetchone()[0],5)

    @unittest.skipUnless(shutil.which('node'),'Node.js not installed')
    def test_export_item_page_and_python_static_metadata_parity(self):
        self.update()
        with tempfile.TemporaryDirectory() as tmp:
            export.export_site(self.c,tmp,log=lambda *a:None)
            m=json.loads((Path(tmp)/'data/meta.json').read_text())
            self.assertEqual(m['catalog_coverage']['imported_named'],3)
            result=subprocess.run(['node',str(ROOT/'tests/catalog.test.js'),tmp,str(self.h)],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
