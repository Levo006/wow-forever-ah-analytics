// Static browser API must preserve provenance and not invent prices.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');
if (!process.argv[2]) { console.log('catalog parity runs from test_catalog.py with its fixture export'); process.exit(0); }
const root = process.argv[2], house = process.argv[3];
const context = { console, URLSearchParams, setTimeout, clearTimeout,
  window: {}, location: { href:'https://fixture.test/' },
  fetch: async (url) => {
    const f=path.join(root, String(url).replace(/^\.\//,'').split('?')[0]);
    if (!fs.existsSync(f)) return { ok:false, status:404 };
    return { ok:true, headers:{get:()=> 'application/json'}, json:async()=>JSON.parse(fs.readFileSync(f,'utf8')) };
  }
};
vm.createContext(context);
vm.runInContext(fs.readFileSync(path.join(__dirname,'../static/static-api.js'),'utf8'),context);
(async()=>{
  const call=vm.runInContext('StaticAPI',context).request;
  const st=await call('status',{});assert.equal(st.catalog_coverage.imported_named,3);
  const s=await call('search',{q:'Abgenutztes',house});assert.equal(s.total,1);
  assert.equal(s.items[0].catalog_state,'readable');assert.equal(s.items[0].last_min,null);
  const it=await call('item/25',{});assert.equal(it.item.catalog_build,'1.60.1.70938');
  assert.equal(it.item.class_name,'Waffen');assert.equal(it.item.name_en,'Worn Shortsword');assert.equal(it.houses.length,0);
  const old=await call('item/7753',{});assert.equal(old.item.catalog_state,'unreadable');assert.equal(old.item.aliases[0],'Blutvergießer');
  console.log('catalog Python/static metadata parity OK');
})().catch(e=>{console.error(e);process.exitCode=1;});
