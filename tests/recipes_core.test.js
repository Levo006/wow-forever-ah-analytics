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

// ------------------------------------------------------------ Quests & Sets
const qdata = {
  quests: { cols: ["id", "title", "zone", "type", "faction", "classes", "repeatable", "xp", "money", "rewards"], rows: [
    [33, "Wölfe an der Grenze", "Nordhaintal", null, null, null, 0, 170, null, [[80, 1], [6070, 1]]],
    [40, "Hordequest", "Brachland", "Dungeon", "Horde", null, 2, 0, 500, [[80, 0]]],
    [41, "Allianzquest", "Westfall", null, "Allianz", "Krieger", 0, 0, 0, [[80, 0], [99, 1]]],
    [42, "kaputt", "x", null, null, null, 0, 0, 0, []],
  ] },
  sets: { cols: ["id", "name", "items", "effects"], rows: [[144, "Eisenfederrüstung", [15066, 15067], [[2, "Set: +20 Schaden"]]], [1, "leer", [], []]] },
  items: { 80: ["Weiche Schuhe", 1, null, 1], 6070: ["Wolfhaut-Armschienen", 1, null, 1], 15066: ["Brust", 3, null, 1] },
  prices: { allianz: { items: { 80: [100, 100, 1], 6070: [250, 260, 2], 15066: [50000, 50000, 1] } } },
};
const qi = RC.buildQuestIndex(qdata);

test("Quests: Index und Reihenfolge nach Fraktion", () => {
  assert.equal(qi.quests.length, 3);                       // ohne Belohnung ignoriert
  assert.deepEqual(RC.rewardedBy(qi, 80, "allianz").map((q) => q.id), [33, 41, 40]);   // Horde-Quest zuletzt
  assert.deepEqual(RC.rewardedBy(qi, 80, "horde").map((q) => q.id), [33, 40, 41]);
  assert.equal(RC.rewardedBy(qi, 80).find((q) => q.id === 40).repeatLabel, "täglich");
  assert.deepEqual(RC.rewardedBy(qi, 424242), []);
});

test("Quests: wertvollste Wahlbelohnung", () => {
  const r = RC.questRewards(qi, "allianz", qi.quests[0]);
  assert.equal(r.best, 6070);
  assert.equal(r.list.length, 2);
  const h = RC.questRewards(qi, "horde", qi.quests[0]);   // keine Preise → keine Empfehlung
  assert.equal(h.best, null);
  const one = RC.questRewards(qi, "allianz", qi.quests.find((q) => q.id === 41)); // nur eine Wahl mit Preis
  assert.equal(one.best, null);
});

test("Sets: Zugehörigkeit und Kosten", () => {
  assert.equal(RC.setsOf(qi, 15067)[0].name, "Eisenfederrüstung");
  assert.equal(qi.sets.length, 1);
  const c = RC.setCost(qi, "allianz", qi.sets[0]);
  assert.deepEqual([c.total, c.have, c.count, c.complete], [50000, 1, 2, false]);
  assert.deepEqual(RC.buildQuestIndex({}).quests, []);
});

// ------------------------------------------------------------ Herkunft (QuestieDB)
const src = {
  items: {
    750: { d: [[299, 80], [69, null]], dn: 5, q: [[33, 1], [179, 1]] },
    2770: { o: [[1731, null], [2055, null], [3763, null]], on: 3, c: [9363] },
    9363: { k: [774, 2770] },
    4306: { v: [5, 6], vn: 3, q: [[7, 3]] },
  },
  npcs: { 299: ["Young Wolf", 1, 1, 0, "Wald von Elwynn", null, null], 69: ["Timber Wolf", 2, 3, 3, null, null, null],
    5: ["Händler", 10, 10, 0, "Sturmwind", "Allianz", "Stoffhändler"] },
  objects: { 1731: ["Copper Vein", "Durotar"], 2055: ["Copper Vein", "Rotkammgebirge"], 3763: ["Battered Chest", null] },
  quests: { 33: ["Wölfe an der Grenze", 2, 1, "Nordhaintal", "Allianz"], 7: ["Startquest", 10, 8, null, null] },
  names: { 9363: ["Kasten", 1, null, 0] },
};

test("Herkunft: Beute, Fundorte, Händler, Quests", () => {
  const v = RC.sourceView(src, 750);
  assert.equal(v.drops[0].name, "Young Wolf");
  assert.equal(v.drops[0].chance, 80);
  assert.equal(v.drops[1].level, "2–3");
  assert.equal(v.drops[1].rank, "Boss");
  assert.equal(v.dropsTotal, 5);
  assert.ok(v.hasChance);
  assert.deepEqual(v.quests.map((q) => [q.title, q.kindLabel]), [["Wölfe an der Grenze", "Questziel"], ["Quest #179", "Questziel"]]);
  const o = RC.sourceView(src, 2770);
  assert.deepEqual(o.objects.map((x) => [x.name, x.zones.length, x.ids.length]), [["Copper Vein", 2, 2], ["Battered Chest", 0, 1]]);
  assert.deepEqual(o.containers, [9363]);
  assert.deepEqual(RC.sourceView(src, 9363).contents, [774, 2770]);
  const h = RC.sourceView(src, 4306);
  assert.equal(h.vendors[0].sub, "Stoffhändler");
  assert.equal(h.vendors[1].name, "NPC #6");
  assert.equal(h.vendorsTotal, 3);
  assert.deepEqual([h.quests.length, h.starts[0].title], [0, "Startquest"]);
  assert.equal(RC.sourceView(src, 1), null);
  assert.equal(RC.sourceView(null, 750), null);
  assert.equal(RC.sourceView({ items: {} }, 750), null);
});

console.log(`recipes-core: ${n} Tests OK`);
