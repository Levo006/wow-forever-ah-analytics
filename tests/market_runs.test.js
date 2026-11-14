'use strict';
const fs=require('fs'),path=require('path'),vm=require('vm'),assert=require('assert/strict');
const root=process.argv[2],house=Number(process.argv[3]);
const context=vm.createContext({console,Date,fetch:async url=>({ok:fs.existsSync(path.join(root,url)),headers:{get:()=> 'application/json'},json:async()=>JSON.parse(fs.readFileSync(path.join(root,url),'utf8'))})});
vm.runInContext(fs.readFileSync(path.join(root,'static-api.js'),'utf8'),context);
(async()=>{
 const api=vm.runInContext('StaticAPI',context);
 const actual=(await api.request('overview',{house})).kpis,expected=JSON.parse(fs.readFileSync(path.join(root,'expected.json'),'utf8'));
 for(const key of ['median_chg24','median_chg7d','comparable24','comparable7d','up24','down24','comparisons_reliable','quantity'])assert.equal(actual[key],expected[key],key);
 const row=(await api.request('search',{house,q:'1'})).items[0];
 assert.equal(row.last_lowest,5500);assert.equal(row.price_basis,'robust_reference');
 assert.equal((await api.request('status')).market_runs[0].status,'complete');
 console.log('Manifest / price / overview parity OK');
})().catch(e=>{console.error(e);process.exitCode=1;});
