"""Ruleset isolation and compatibility regressions."""
import json
import unittest
from unittest.mock import patch
from ahanalytics import api, db, recipes, rulesets, itempages
from ahanalytics.sources import battlenet
from test_market_runs import Client, NOW, QUIET

class RulesetsTest(unittest.TestCase):
    def setUp(self):
        self.c=db.connect(':memory:')
        self.ids={}
        for key,(_,_,nums) in rulesets.RULESETS.items():
            for fac,num in zip(rulesets.FACTIONS,nums):
                h=db.upsert_house(self.c,'battlenet',f'eu-{num}',str(num),region='eu',faction=fac)
                self.ids[(key,fac)]=h
                db.write_snapshot(self.c,h,NOW,'test',{2770:{'min':num,'quantity':1}})
        self.old=db.upsert_group_house(self.c,'battlenet','eu','allianz')
        db.write_snapshot(self.c,self.old,NOW,'test',{2770:{'min':99999,'quantity':1}})
        db.refresh_summary(self.c)
    def tearDown(self):self.c.close()
    def test_normal_default_and_old_alias(self):
        normal=self.ids['normal','Allianz']
        self.assertEqual(api.houses(self.c,{})['default'],normal)
        self.assertEqual(api._resolve_house(self.c,{'house':self.old}),normal)
        result=api.search(self.c,{'q':'2770','house':self.old})['items']
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]['last_min'],111)
        self.assertEqual(api.search(self.c,{'q':'2770'})['items'][0]['last_min'],111)
    def test_mapping_and_no_mixed_item_rows(self):
        hs=api.houses(self.c,{})['houses']
        self.assertEqual(len(hs),12)
        self.assertEqual({h['ruleset_key'] for h in hs},set(rulesets.RULESETS))
        rows=api.item(self.c,2770,{})['houses']
        self.assertEqual(len(rows),12)
        self.assertNotIn(99999,{r['last_min'] for r in rows})
        mapping=recipes.group_houses(self.c)
        self.assertEqual(mapping['pvp:Allianz'.lower()],self.ids['pvp','Allianz'])
        self.assertEqual(mapping['allianz'],self.ids['normal','Allianz'])
    def test_dormant_does_not_mask_active_errors(self):
        self.c.close();self.c=db.connect(':memory:')
        client=Client()
        client.houses.append({'id':112,'name':'Alliance Auction House','ruleset':1})
        for ending in ('','/commodities'):
            client.data['/data/wow/ruleset/1/auctions/112'+ending]=({'id':112},{})
        with patch.object(battlenet,'discover_houses',return_value=client.houses):
            battlenet.collect(self.c,client,now=NOW,fetch_metadata=False,log=QUIET)
            manifest=api.market_runs(self.c)[0]
            self.assertEqual(manifest['status'],'complete')
            self.assertTrue(all(e['status']=='expected_inactive' for e in manifest['endpoints'] if e['house']==112))
            client.data['/data/wow/ruleset/1/auctions/111']=({'id':111},{})
            battlenet.collect(self.c,client,now=NOW,fetch_metadata=False,log=QUIET)
            self.assertEqual(api.market_runs(self.c)[0]['status'],'partial')
    def test_rows_separate_ruleset_prices(self):
        rows,_=itempages._price_rows({'normal:Allianz':{'last_min':111},'pvp:Allianz':{'last_min':222}},'en')
        self.assertIn('Normal · Alliance',rows)
        self.assertIn('PvP · Alliance',rows)
        self.assertIn('1s 11c',rows)
        self.assertIn('2s 22c',rows)
