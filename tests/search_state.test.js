"use strict";
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert/strict');
const root = process.argv[2], house = Number(process.argv[3]);
const context = vm.createContext({ console, Date, fetch: async (url) => {
  const p = path.join(root, url);
  return { ok: fs.existsSync(p), status: fs.existsSync(p) ? 200 : 404,
    headers: { get: () => 'application/json' }, json: async () => JSON.parse(fs.readFileSync(p, 'utf8')) };
}});
vm.runInContext(fs.readFileSync(path.join(root, 'static-api.js'), 'utf8'), context);
const api = vm.runInContext('StaticAPI', context);
(async () => {
  for (const [q, id] of [['Blutvergießer',7753], ['Bloodspiller',7753], ['Der Schlächter',8226], ['85',85]]) {
    const r = await api.request('search', {q, house});
    assert.equal(r.total, 1, q);
    assert.equal(r.items[0].item_id, id, q);
  }
  assert.equal((await api.request('search', {house, class:'2'})).total, 5);
  const foreign = (await api.request('search', {house, q:'85'})).items[0];
  assert.equal(foreign.has_history, true);
  assert.equal(foreign.house_id, null);
  assert.equal((await api.request('search', {house,q:'85',min_qty:'1'})).total, 0);
  const historic = (await api.request('search', {house,q:'1'})).items[0];
  assert.equal(historic.is_current, false);
  assert.equal(historic.current_quantity, 0);
  assert.equal(historic.last_min, 100);
  assert.equal(historic.market_value, 0);
  assert.equal((await api.request('overview', {house})).kpis.quantity, 9);
  assert(!(await api.request('deals', {house})).deals.some(x=>x.item_id===1));
  assert((await api.request('item/85', {})).houses.length > 0);
  assert.equal((await api.request('item/8226', {})).houses.length, 0);
  console.log('Static search and offer-state regressions: OK');
})().catch(e=>{console.error(e);process.exitCode=1;});
