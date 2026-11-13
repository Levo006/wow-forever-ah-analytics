/* Rezept-Logik ohne DOM: Index, Bäume „daraus wird …“ / „hergestellt aus“, Materialkosten.
 * Läuft im Browser (window.RecipeCore) und in Node (Tests: tests/recipes_core.test.js). */
"use strict";

(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.RecipeCore = api;
})(typeof self !== "undefined" ? self : this, () => {
  const AH_FEE = 0.05; // Auktionshaus behält 5 % des Verkaufspreises

  /** Baut aus recipes.json (bzw. /api/recipes) einen Index. */
  function buildIndex(data) {
    const profs = data.professions || [];
    const cats = data.categories || [];
    const cols = data.recipes?.cols || [];
    const ci = Object.fromEntries(cols.map((c, i) => [c, i]));
    const recipes = [];
    const byReagent = new Map();
    const byProduct = new Map();
    for (const row of data.recipes?.rows || []) {
      const reagents = (row[ci.reagents] || []).filter((x) => Array.isArray(x) && x[0] && x[1] > 0).map(([id, q]) => ({ id, q }));
      if (!reagents.length) continue;
      const p = profs[row[ci.prof]] || { name: "Sonstiges" };
      const r = {
        id: row[ci.id], prof: p.name || "Sonstiges", profId: p.id, cat: cats[row[ci.cat]] || "", name: row[ci.name],
        item: row[ci.item] || null, qmin: Number(row[ci.qmin]) || 1, qmax: Number(row[ci.qmax]) || Number(row[ci.qmin]) || 1, reagents,
      };
      r.qavg = (r.qmin + r.qmax) / 2;
      recipes.push(r);
      for (const x of reagents) push(byReagent, x.id, r);
      if (r.item) push(byProduct, r.item, r);
    }
    return { recipes, byReagent, byProduct, items: data.items || {}, prices: data.prices || {}, professions: profs.map((p) => p.name || "Sonstiges") };
  }
  function push(map, k, v) {
    const a = map.get(k);
    if (a) { if (!a.includes(v)) a.push(v); } else map.set(k, [v]);
  }

  function itemInfo(idx, id) {
    const m = idx.items[String(id)];
    return m ? { id, name: m[0] || `Item #${id}`, quality: m[1], icon: m[2], inAH: !!m[3] } : { id, name: `Item #${id}`, quality: null, icon: null, inAH: false };
  }
  /** Aktueller Preis (typischer Tiefstpreis) einer Fraktion oder null. */
  function price(idx, faction, id) {
    const p = idx.prices?.[faction]?.items?.[String(id)];
    return p && p[0] > 0 ? { min: p[0], median: p[1], qty: p[2] } : null;
  }

  const usedIn = (idx, id, prof) => (idx.byReagent.get(id) || []).filter((r) => !prof || r.prof === prof);
  const madeBy = (idx, id) => idx.byProduct.get(id) || [];

  /** Berufe mit Anzahl Rezepten, in denen das Item vorkommt (Zutat oder Ergebnis). */
  function professionsFor(idx, id) {
    const n = new Map();
    for (const r of [...usedIn(idx, id), ...madeBy(idx, id)]) n.set(r.prof, (n.get(r.prof) || 0) + 1);
    return [...n.entries()].sort((a, b) => b[1] - a[1]).map(([prof, count]) => ({ prof, count }));
  }

  /**
   * „Daraus wird …“: Rezepte, die das Item verwenden, rekursiv für deren Ergebnisse.
   * Zyklen (ein Item taucht im eigenen Pfad wieder auf) werden markiert und nicht weiter verfolgt.
   * Knoten: { recipe, item, qty (benötigte Menge des Eltern-Items), cycle, children, more }
   */
  function usesTree(idx, id, { maxDepth = 4, maxNodes = 500, prof = null } = {}) {
    let budget = maxNodes;
    const walk = (itemId, depth, path) => {
      const list = usedIn(idx, itemId, depth === 0 ? prof : null);
      const out = [];
      for (const r of list) {
        if (budget <= 0) return { nodes: out, more: list.length - out.length };
        budget--;
        const reag = r.reagents.find((x) => x.id === itemId);
        const node = { recipe: r, item: r.item, qty: reag ? reag.q : 0, cycle: false, children: [], more: 0 };
        if (r.item && path.has(r.item)) node.cycle = true;
        else if (r.item && depth + 1 < maxDepth) {
          const sub = walk(r.item, depth + 1, new Set([...path, r.item]));
          node.children = sub.nodes; node.more = sub.more;
        } else if (r.item) node.more = usedIn(idx, r.item).length;
        out.push(node);
      }
      return { nodes: out, more: 0 };
    };
    return walk(id, 0, new Set([id])).nodes;
  }

  /**
   * „Hergestellt aus“: Rezepte für das Item, je Zutat rekursiv deren Rezepte.
   * Knoten: { recipe, reagents: [{ id, q, cycle, recipes: [Knoten …] }] }
   */
  function madeFromTree(idx, id, { maxDepth = 4 } = {}) {
    const walk = (itemId, depth, path) => madeBy(idx, itemId).map((r) => ({
      recipe: r,
      reagents: r.reagents.map((x) => {
        const cycle = path.has(x.id);
        return { id: x.id, q: x.q, cycle, recipes: !cycle && depth + 1 < maxDepth ? walk(x.id, depth + 1, new Set([...path, x.id])) : [] };
      }),
    }));
    return walk(id, 0, new Set([id]));
  }

  /**
   * Stückkosten eines Items: AH-Preis oder (mode "best") günstiger selbst hergestellt.
   * Ergebnis { cost, source: "ah"|"craft"|null, recipe } – cost null, wenn unbekannt.
   */
  function unitCost(idx, faction, id, { mode = "ah", depth = 3, path = new Set() } = {}) {
    const p = price(idx, faction, id);
    let best = p ? { cost: p.min, source: "ah", recipe: null } : { cost: null, source: null, recipe: null };
    if (mode !== "best" || depth <= 0 || path.has(id)) return best;
    const next = new Set([...path, id]);
    for (const r of madeBy(idx, id)) {
      const m = materialCost(idx, faction, r, { mode, depth: depth - 1, path: next });
      if (m.complete && (best.cost == null || m.perUnit < best.cost)) best = { cost: m.perUnit, source: "craft", recipe: r };
    }
    return best;
  }

  /** Materialkosten eines Rezepts. perUnit = Kosten je hergestelltem Stück (Mittel bei Mengenspanne). */
  function materialCost(idx, faction, recipe, { mode = "ah", depth = 3, path = new Set() } = {}) {
    let total = 0;
    const missing = [];
    const parts = recipe.reagents.map((x) => {
      const u = unitCost(idx, faction, x.id, { mode, depth, path });
      if (u.cost == null) missing.push(x.id); else total += u.cost * x.q;
      return { id: x.id, q: x.q, unit: u.cost, source: u.source, sum: u.cost == null ? null : u.cost * x.q };
    });
    const complete = !missing.length;
    return { parts, total: complete ? total : null, partial: total, perUnit: complete ? total / recipe.qavg : null, complete, missing };
  }

  /** Spanne: Verkauf des Ergebnisses im AH (nach Gebühr) minus Materialkosten, je Stück. */
  function margin(idx, faction, recipe, opts = {}) {
    const m = materialCost(idx, faction, recipe, opts);
    const sell = recipe.item ? price(idx, faction, recipe.item) : null;
    if (!sell || !m.complete) return { cost: m.perUnit, sell: sell ? sell.min : null, profit: null, pct: null, complete: m.complete, missing: m.missing };
    const net = sell.min * (1 - AH_FEE);
    const profit = net - m.perUnit;
    return { cost: m.perUnit, sell: sell.min, profit, pct: m.perUnit > 0 ? (profit / m.perUnit) * 100 : null, complete: true, missing: [] };
  }

  /** Alle Items im Baum „daraus wird …“ (ohne Wurzel), z. B. für Zählung/Zusammenfassung. */
  function countTree(nodes) {
    let n = 0;
    const seen = new Set();
    const walk = (ns) => ns.forEach((x) => { n++; if (x.item) seen.add(x.item); walk(x.children); });
    walk(nodes);
    return { nodes: n, items: seen.size };
  }

  // ------------------------------------------------------------ Quests & Sets
  const REPEAT = ["", "wiederholbar", "täglich", "wöchentlich"];

  /** Index aus quests.json (bzw. /api/quests): Belohnungen und Set-Zugehörigkeit je Item. */
  function buildQuestIndex(data) {
    const rowsOf = (t) => {
      const cols = t?.cols || [];
      return (t?.rows || []).map((r) => Object.fromEntries(cols.map((c, i) => [c, r[i]])));
    };
    const quests = rowsOf(data?.quests).map((q) => ({
      ...q, repeatLabel: REPEAT[q.repeatable || 0] || "",
      rewards: (q.rewards || []).filter((x) => Array.isArray(x) && x[0]).map(([id, choice]) => ({ id, choice: !!choice })),
    })).filter((q) => q.rewards.length);
    const sets = rowsOf(data?.sets).map((s) => ({ ...s, items: (s.items || []).filter(Boolean), effects: s.effects || [] })).filter((s) => s.items.length);
    const byReward = new Map(), bySet = new Map();
    for (const q of quests) for (const r of q.rewards) push(byReward, r.id, q);
    for (const s of sets) for (const id of s.items) push(bySet, id, s);
    return { quests, sets, byReward, bySet, items: data?.items || {}, prices: data?.prices || {} };
  }

  /** Quests, die das Item als Belohnung geben (Fraktionsquests der anderen Seite zuletzt). */
  function rewardedBy(qidx, id, faction = null) {
    const fac = { allianz: "Allianz", horde: "Horde" }[faction] || null;
    return (qidx.byReward.get(id) || []).slice().sort((a, b) =>
      ((a.faction && fac && a.faction !== fac) ? 1 : 0) - ((b.faction && fac && b.faction !== fac) ? 1 : 0) || a.id - b.id);
  }

  /** Belohnungen einer Quest mit Preisen; best = wertvollste Wahlbelohnung (nur mit Preis). */
  function questRewards(qidx, faction, quest) {
    const list = quest.rewards.map((r) => ({ ...r, price: price(qidx, faction, r.id) }));
    const choices = list.filter((r) => r.choice && r.price);
    const best = choices.length > 1 ? choices.reduce((m, r) => (r.price.min > m.price.min ? r : m)) : null;
    return { list, best: best ? best.id : null };
  }

  const setsOf = (qidx, id) => qidx.bySet.get(id) || [];

  /** Set-Kosten im AH: Summe der verfügbaren Teile, Anzahl fehlender Teile. */
  function setCost(qidx, faction, set) {
    let total = 0, have = 0;
    const parts = set.items.map((id) => { const p = price(qidx, faction, id); if (p) { total += p.min; have++; } return { id, price: p }; });
    return { parts, total, have, count: set.items.length, complete: have === set.items.length };
  }

  return { AH_FEE, buildIndex, itemInfo, price, usedIn, madeBy, professionsFor, usesTree, madeFromTree, unitCost, materialCost, margin, countTree,
    buildQuestIndex, rewardedBy, questRewards, setsOf, setCost };
});
