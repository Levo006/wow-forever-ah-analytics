/* Tests für die Kategorie-Filter in der statischen Suche (Spiegel: tests/test_category_filters.py).
 * Baut einen minimalen Export in ein Temp-Verzeichnis und fragt StaticAPI dagegen. */
"use strict";
const fs = require("fs");
const os = require("os");
const path = require("path");
const vm = require("vm");
const assert = require("assert/strict");

const TS = 1790000000;

function buildRoot(withRecipes) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "ah-catfilter-"));
  const w = (rel, obj) => {
    const p = path.join(root, rel);
    fs.mkdirSync(path.dirname(p), { recursive: true });
    fs.writeFileSync(p, JSON.stringify(obj));
  };
  w("data/meta.json", {
    generated_at: TS, default: 1, search_items: 0,
    counts: { houses: 1, items: 7, catalog: 0 },
    houses: [{ id: 1, name: "Allianz · alle Verbünde", faction: "Allianz", region: "eu", source: "battlenet",
               ext_id: "eu-allianz", granularity: "hour", kind: "allianz", last_ts: TS, snapshots: 2,
               quality: { complete: true, ts: TS } }],
  });
  const cols = ["item_id", "name", "name_en", "quality", "class_id", "subclass_id", "icon", "required_level",
    "is_commodity", "last_ts", "last_min", "last_qty", "market_complete", "market_value", "aliases"];
  const row = (iid, name, q, cid, sub, lvl, current, mv) => [
    iid, name, null, q, cid, sub, null, lvl, 0, current ? TS : TS - 7200, 100, 5, 1, mv, null];
  w("data/house/1/summary.json", { cols, rows: [
    row(2770, "Kupfererz", 1, 7, 7, null, true, 5000),
    row(2840, "Kupferbarren", 1, 7, 7, null, true, 6000),
    row(2589, "Leinenstoff", 1, 7, 5, null, true, 4000),
    row(7000, "Rezeptlose Ware", 2, 7, 11, null, false, 0),
    row(8001, "Pläne: Testdolch", 3, 9, 4, null, true, 50000),
    row(8002, "Rezept: Minusmacher", 3, 9, 6, null, true, 25),
    row(9001, "Testdolch", 3, 2, 15, 20, true, 30000),
    row(9002, "Minusmacher", 1, 0, 0, null, true, 25),
  ] });
  if (withRecipes) w("data/recipes.json", {
    professions: [{ id: 164, name: "Schmiedekunst" }, { id: 171, name: "Alchimie" }, { id: 186, name: "Bergbau" }],
    categories: [""],
    recipes: { cols: ["id", "prof", "cat", "name", "item", "qmin", "qmax", "reagents"], rows: [
      [1, 0, 0, "Testdolch", 9001, 1, 1, [[2840, 1]]],
      [2, 1, 0, "Minusmacher", 9002, 1, 1, [[2770, 1]]],
      [3, 2, 0, "Kupfer verhütten", 2840, 1, 1, [[2770, 1]]],
    ] },
    items: {},
    prices: { allianz: { house_id: 1, items: {
      "2770": [100, 100, 50], "2840": [300, 300, 20], "9001": [10000, 10000, 3], "9002": [50, 50, 5] } } },
    teaches: { "8001": [1], "8002": [2] },
  });
  return root;
}

async function makeApi(root) {
  const context = vm.createContext({ console, Date, fetch: async (url) => {
    const p = path.join(root, url);
    const ok = fs.existsSync(p);
    return { ok, status: ok ? 200 : 404,
      headers: { get: () => "application/json" }, json: async () => JSON.parse(fs.readFileSync(p, "utf8")) };
  } });
  const src = fs.readFileSync(path.join(__dirname, "..", "static", "static-api.js"), "utf8");
  vm.runInContext(src, context);
  return vm.runInContext("StaticAPI", context);
}

(async () => {
  const root = buildRoot(true);
  const api = await makeApi(root);
  const ids = async (params) => JSON.parse(JSON.stringify(
    (await api.request("search", params)).items.map((x) => x.item_id).sort((a, b) => a - b)));

  assert.deepEqual(await ids({ house: 1, class: "7" }), [2589, 2770, 2840, 7000]);
  // Beruf: Zutat oder Ergebnis eines Rezepts des Berufs (ODER über mehrere)
  assert.deepEqual(await ids({ house: 1, class: "7", prof: "171" }), [2770]);
  assert.deepEqual(await ids({ house: 1, class: "7", prof: "164" }), [2840]);
  assert.deepEqual(await ids({ house: 1, class: "7", prof: "186" }), [2770, 2840]);
  assert.deepEqual(await ids({ house: 1, class: "7", prof: "164,171" }), [2770, 2840]);
  assert.deepEqual(await ids({ house: 1, prof: "171" }), [2770, 9002]);
  // Nur aktuell gelistet
  assert.deepEqual(await ids({ house: 1, class: "7", listed: "1" }), [2589, 2770, 2840]);
  // Stufe (benötigte Stufe); ohne bekannte Stufen im Bestand wirkt der Filter nicht
  assert.deepEqual(await ids({ house: 1, class: "2", min_lvl: "10" }), [9001]);
  assert.deepEqual(await ids({ house: 1, class: "2", min_lvl: "30" }), []);
  assert.deepEqual(await ids({ house: 1, class: "2", min_lvl: "19", max_lvl: "20" }), [9001]);
  assert.deepEqual(await ids({ house: 1, class: "7", min_lvl: "10" }), [2589, 2770, 2840, 7000]);
  // Spanne: 8001 lehrt "Testdolch" (10000 * 0,95 - 300 > 0), 8002 nur Verlust
  assert.deepEqual(await ids({ house: 1, margin: "1" }), [8001]);
  assert.deepEqual(await ids({ house: 1, class: "9" }), [8001, 8002]);
  // Qualität (bestehender Parameter) weiterhin kombinierbar
  assert.deepEqual(await ids({ house: 1, class: "7", prof: "186", quality: "1" }), [2770, 2840]);
  fs.rmSync(root, { recursive: true, force: true });

  // Ohne recipes.json (z. B. Demo-Export): Filter wirken nicht, statt die Tabelle zu leeren
  const bare = buildRoot(false);
  const api2 = await makeApi(bare);
  const ids2 = async (params) => JSON.parse(JSON.stringify(
    (await api2.request("search", params)).items.map((x) => x.item_id).sort((a, b) => a - b)));
  assert.deepEqual(await ids2({ house: 1, class: "7", prof: "171", margin: "1" }), [2589, 2770, 2840, 7000]);
  fs.rmSync(bare, { recursive: true, force: true });

  console.log("Kategorie-Filter (statisch): OK");
})().catch((e) => { console.error(e); process.exitCode = 1; });
