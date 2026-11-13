/* Tests für static/recipes-core.js (läuft mit `node tests/recipes_core.test.js`,
 * wird von tests/test_recipes.py automatisch mit ausgeführt). */
"use strict";
const assert = require("node:assert/strict");
const path = require("node:path");
const RC = require(path.join(__dirname, "..", "static", "recipes-core.js"));

// Kupfererz(2770) → Kupferbarren(2840) → Bronzebarren(2841, ×2 aus Kupfer+Zinn 3576) → Bronzeaxt(9001)
// Kreislauf: A(10) → B(11) → A(10); Verzauberung ohne Ergebnis-Item; Rezept mit Mengenspanne
const data = {
  professions: [{ id: 186, name: "Bergbau" }, { id: 164, name: "Schmiedekunst" }, { id: 333, name: "Verzauberkunst" }, { id: 171, name: "Alchimie" }],
  categories: ["Barren", "Waffen", "Armschienen", "Tränke"],
  recipes: {
    cols: ["id", "prof", "cat", "name", "item", "qmin", "qmax", "reagents"],
    rows: [
      [1, 0, 0, "Kupfer verhütten", 2840, 1, 1, [[2770, 1]]],
      [2, 0, 0, "Bronze verhütten", 2841, 2, 2, [[2840, 1], [3576, 1]]],
      [3, 1, 1, "Bronzeaxt", 9001, 1, 1, [[2841, 4], [2880, 1]]],
      [4, 1, 1, "Kupferdolch", 9002, 1, 1, [[2840, 6]]],
      [5, 2, 2, "Armschiene - Ausdauer", null, 1, 1, [[2840, 2]]],
      [6, 3, 3, "A aus B", 10, 1, 1, [[11, 1]]],
      [7, 3, 3, "B aus A", 11, 1, 1, [[10, 1]]],
      [8, 3, 3, "Glückstrank", 12, 1, 3, [[2770, 2]]],
      [9, 3, 3, "kaputt", 13, 1, 1, []],               // ohne Zutaten → ignoriert
    ],
  },
  items: { 2770: ["Kupfererz", 1, "inv_ore_copper_01", 1], 2840: ["Kupferbarren", 1, null, 1], 2841: ["Bronzebarren", 1, null, 1],
    3576: ["Zinnbarren", 1, null, 1], 9001: ["Bronzeaxt", 2, null, 0] },
  prices: { allianz: { house_id: 1, items: { 2770: [100, 110, 50], 2840: [300, 300, 20], 3576: [200, 210, 5], 2841: [900, 950, 3], 9001: [10000, 10000, 1], 12: [500, 500, 1] } },
    horde: { house_id: 2, items: {} } },
};
const idx = RC.buildIndex(data);
let n = 0;
const test = (name, fn) => { fn(); n++; };

test("Index", () => {
  assert.equal(idx.recipes.length, 8);
  assert.deepEqual(RC.usedIn(idx, 2840).map((r) => r.id).sort(), [2, 4, 5]);
  assert.deepEqual(RC.madeBy(idx, 2841).map((r) => r.id), [2]);
  assert.equal(RC.usedIn(idx, 2840, "Bergbau").length, 1);
  const pf = RC.professionsFor(idx, 2840);
  assert.deepEqual(pf[0], { prof: "Bergbau", count: 2 });     // verwendet (Bronze) + hergestellt (Kupfer)
  assert.deepEqual(pf.map((p) => p.prof).sort(), ["Bergbau", "Schmiedekunst", "Verzauberkunst"]);
});

test("fehlende Items", () => {
  const i = RC.itemInfo(idx, 424242);
  assert.equal(i.name, "Item #424242");
  assert.equal(i.inAH, false);
  assert.equal(RC.itemInfo(idx, 9001).inAH, false);
  assert.equal(RC.price(idx, "allianz", 424242), null);
  assert.equal(RC.price(idx, "gibtsnicht", 2770), null);
  assert.deepEqual(RC.usedIn(idx, 424242), []);
});

test("Baum: Kupfererz → Barren → Bronze → Axt", () => {
  const t = RC.usesTree(idx, 2770);
  const smelt = t.find((x) => x.recipe.id === 1);
  assert.equal(smelt.item, 2840);
  const bronze = smelt.children.find((x) => x.recipe.id === 2);
  assert.equal(bronze.qty, 1);
  assert.equal(bronze.children[0].item, 9001);
  assert.equal(bronze.children[0].qty, 4);
  const ench = smelt.children.find((x) => x.recipe.id === 5);
  assert.equal(ench.item, null);
  assert.deepEqual(ench.children, []);
  // Berufsfilter gilt nur für die erste Ebene
  assert.deepEqual(RC.usesTree(idx, 2770, { prof: "Alchimie" }).map((x) => x.recipe.id), [8]);
});

test("Baum: Tiefenbegrenzung", () => {
  const t = RC.usesTree(idx, 2770, { maxDepth: 1 });
  const smelt = t.find((x) => x.recipe.id === 1);
  assert.deepEqual(smelt.children, []);
  assert.equal(smelt.more, 3);   // weitere Verwendungen von Kupferbarren angedeutet
});

test("Baum: Knotenbudget", () => {
  const t = RC.usesTree(idx, 2770, { maxNodes: 2 });
  assert.ok(RC.countTree(t).nodes <= 2);
});

test("Zyklen werden erkannt und nicht verfolgt", () => {
  const t = RC.usesTree(idx, 10, { maxDepth: 20 });
  assert.equal(t.length, 1);
  assert.equal(t[0].item, 11);
  assert.equal(t[0].children[0].item, 10);
  assert.equal(t[0].children[0].cycle, true);
  assert.deepEqual(t[0].children[0].children, []);
  const m = RC.madeFromTree(idx, 10, { maxDepth: 20 });
  assert.equal(m[0].reagents[0].id, 11);
  assert.equal(m[0].reagents[0].recipes[0].reagents[0].cycle, true);
  // Kosten im Kreislauf ohne Preise: endet, Ergebnis unbekannt
  assert.equal(RC.unitCost(idx, "allianz", 10, { mode: "best", depth: 10 }).cost, null);
});

test("Hergestellt aus (rekursiv)", () => {
  const m = RC.madeFromTree(idx, 9001);
  assert.equal(m.length, 1);
  const bronze = m[0].reagents.find((x) => x.id === 2841);
  assert.equal(bronze.q, 4);
  const copper = bronze.recipes[0].reagents.find((x) => x.id === 2840);
  assert.equal(copper.recipes[0].reagents[0].id, 2770);
});

test("Materialkosten und Spanne", () => {
  const bronze = RC.madeBy(idx, 2841)[0];
  const c = RC.materialCost(idx, "allianz", bronze);
  assert.equal(c.total, 500);
  assert.equal(c.perUnit, 250);            // ergibt 2 Stück
  const g = RC.margin(idx, "allianz", bronze);
  assert.equal(g.profit, 900 * 0.95 - 250);
  // günstigster Weg: Kupferbarren aus Erz (100) statt AH (300)
  const best = RC.materialCost(idx, "allianz", bronze, { mode: "best" });
  assert.equal(best.perUnit, (100 + 200) / 2);
  assert.equal(best.parts.find((p) => p.id === 2840).source, "craft");
  // Mengenspanne 1–3 → Mittel 2
  assert.equal(RC.materialCost(idx, "allianz", RC.madeBy(idx, 12)[0]).perUnit, 100);
});

test("fehlende Preise", () => {
  const axe = RC.madeBy(idx, 9001)[0];        // Zutat 2880 ohne Preis
  const c = RC.materialCost(idx, "allianz", axe);
  assert.equal(c.complete, false);
  assert.deepEqual(c.missing, [2880]);
  assert.equal(c.total, null);
  assert.equal(c.partial, 3600);
  assert.equal(RC.margin(idx, "allianz", axe).profit, null);
  const h = RC.margin(idx, "horde", RC.madeBy(idx, 2841)[0]);  // Fraktion ohne Preise
  assert.equal(h.profit, null);
  assert.equal(h.sell, null);
  const ench = RC.usedIn(idx, 2840).find((r) => r.id === 5);
  assert.equal(RC.margin(idx, "allianz", ench).profit, null);
});

test("leere bzw. kaputte Daten", () => {
  const e = RC.buildIndex({});
  assert.equal(e.recipes.length, 0);
  assert.deepEqual(RC.usesTree(e, 1), []);
  assert.deepEqual(RC.madeFromTree(e, 1), []);
});

console.log(`recipes-core: ${n} Tests OK`);
