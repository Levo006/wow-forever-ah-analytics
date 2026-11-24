/* Statikmodus: beantwortet API-Aufrufe aus vorberechneten JSON-Dateien
 * (erzeugt mit `python run.py export`). Wird nur aktiv, wenn data/meta.json
 * als JSON existiert – also z. B. auf GitHub Pages, nicht beim lokalen Server. */
const T = (s) => (globalThis.AHI18N ? AHI18N.t(s) : s);
"use strict";

const StaticAPI = (() => {
  let detected = null;
  const files = new Map();
  class HttpError extends Error {}

  function load(url) {
    if (!files.has(url)) {
      files.set(url, fetch(url).then((r) => {
        if (!r.ok) throw new HttpError(r.status === 404 ? T("Nicht im statischen Export enthalten.") : T("Laden fehlgeschlagen") + ` (${r.status})`);
        return r.json();
      }).catch((e) => { files.delete(url); throw e; }));
    }
    return files.get(url);
  }
  function detect() {
    if (!detected) {
      detected = fetch("data/meta.json", { cache: "no-cache" })
        .then((r) => r.ok && (r.headers.get("content-type") || "").includes("json"))
        .catch(() => false);
    }
    return detected;
  }
  const CLASS_NAMES = { 0: "Verbrauchbar", 1: "Behälter", 2: "Waffen", 3: "Edelsteine", 4: "Rüstung", 5: "Reagenzien", 6: "Projektile",
    7: "Handwerkswaren", 8: "Gegenstandsverbesserung", 9: "Rezepte", 11: "Köcher", 12: "Quest", 13: "Schlüssel", 15: "Verschiedenes",
    16: "Glyphen", 17: "Kampfhaustiere", 19: "Berufe" };
  const FOLD = { "ä": "a", "ö": "o", "ü": "u", "ß": "ss", "é": "e", "è": "e", "á": "a", "à": "a", "â": "a", "ê": "e",
    "î": "i", "ô": "o", "û": "u", "ñ": "n", "'": "", "’": "" };
  const fold = (t) => (t == null ? "" : String(t).toLowerCase().replace(/[äöüßéèáàâêîôûñ'’]/g, (c) => FOLD[c]));
  const toRows = (c) => c.rows.map((r) => { const o = {}; c.cols.forEach((k, i) => (o[k] = r[i])); return o; });
  const meta = () => load("data/meta.json");

  // --------------------------------------------------------------- Helfer
  const num = (v) => (v === undefined || v === null || v === "" ? null : Number(v));
  // Handelsrelevante Items: Handelswaren oder Qualitaet ab ungewoehnlich (kein Grau-/Weissschrott).
  const isRelevant = (r) => (r.quality ?? 1) >= 2 || ((r.is_commodity || 0) === 1 && (r.quality ?? 1) >= 1);
  const ids = (v) => (v ? String(v).split(",").filter(Boolean).map(Number) : []);
  const DAY = 86400;
  const RANGES = { "24h": DAY, "7d": 7 * DAY, "30d": 30 * DAY, "90d": 90 * DAY, all: null };

  async function house(id) {
    const m = await meta();
    let hid = id ? Number(id) : m.default;
    if (!m.houses.some(x=>x.id===hid) && m.legacy_aliases?.[String(hid)]) hid = m.legacy_aliases[String(hid)];
    const h = m.houses.find((x) => x.id === hid);
    if (!h) throw new HttpError(m.houses.length ? T("Auktionshaus unbekannt") + ` ${id}` : T("Keine Daten vorhanden."));
    return h;
  }
  async function summary(hid) {
    const h = await house(hid);
    const s = await load(`data/house/${h.id}/summary.json`);
    if (!s._rows) {
      s._rows = toRows(s).map((r) => {
        const current = r.last_ts === h.last_ts;
        return { ...r, name: r.name ?? r.name_en ?? `Item #${r.item_id}`, house_id: h.id, house_name: h.name,
          region: h.region, faction: h.faction, granularity: h.granularity,
          is_current: current, current_quantity: current ? r.last_qty : (r.market_complete ? 0 : null),
          availability: current ? "observed" : r.market_complete ? "not_observed" : "unknown",
          market_value: current ? r.market_value : 0 };
      });
    }
    return s._rows;
  }
  const snapshots = (hid) => load(`data/house/${hid}/snapshots.json`);
  const itemFile = (id) => load(`data/item/${id}.json`).catch(() => { throw new HttpError(`Item ${id} unbekannt`); });

  function sortRows(rows, key, dir, tie = "item_id") {
    const d = dir === "asc" ? 1 : -1;
    return rows.sort((a, b) => {
      const x = a[key], y = b[key];
      const xn = x === null || x === undefined, yn = y === null || y === undefined;
      if (xn !== yn) return xn ? 1 : -1;
      if (!xn && x !== y) return (typeof x === "string" ? x.localeCompare(y, "de") : x - y) * d;
      return a[tie] - b[tie];
    });
  }
  const page = (rows, params, def = 50) => {
    const p = Math.max(1, num(params.page) || 1);
    const size = Math.min(500, Math.max(1, num(params.page_size) || def));
    return { total: rows.length, page: p, page_size: size, slice: rows.slice((p - 1) * size, p * size) };
  };

  // ------------------------------------------------------------ Kennzahlen
  const clean = (pts) => pts.filter(([, v]) => v !== null && v !== undefined && v > 0);
  const mean = (a) => a.reduce((s, x) => s + x, 0) / a.length;
  const pstdev = (a) => { const m = mean(a); return Math.sqrt(mean(a.map((x) => (x - m) ** 2))); };
  function volatility(points) {
    const p = clean(points);
    if (p.length < 3) return { log_return_std: null, daily: null, cv: null };
    const rets = p.slice(1).map((x, i) => Math.log(x[1] / p[i][1]));
    const sd = pstdev(rets);
    const perDay = (p.length - 1) / Math.max((p[p.length - 1][0] - p[0][0]) / DAY, 1e-9);
    const vals = p.map((x) => x[1]);
    return { log_return_std: sd, daily: sd * Math.sqrt(perDay), cv: pstdev(vals) / mean(vals) };
  }
  function trend(points) {
    const p = clean(points);
    if (p.length < 3) return { pct_per_day: null, r2: null, direction: "unbekannt" };
    const xs = p.map(([t]) => (t - p[0][0]) / DAY), ys = p.map(([, v]) => Math.log(v));
    const mx = mean(xs), my = mean(ys);
    const sxx = xs.reduce((s, x) => s + (x - mx) ** 2, 0);
    if (!sxx) return { pct_per_day: null, r2: null, direction: "unbekannt" };
    const sxy = xs.reduce((s, x, i) => s + (x - mx) * (ys[i] - my), 0);
    const syy = ys.reduce((s, y) => s + (y - my) ** 2, 0);
    const pct = (Math.exp(sxy / sxx) - 1) * 100;
    const r2 = syy > 0 ? (sxy * sxy) / (sxx * syy) : 0;
    const direction = r2 < 0.1 || Math.abs(pct) < 0.25 ? "seitwärts" : pct > 0 ? "steigend" : "fallend";
    return { pct_per_day: pct, r2, direction };
  }
  function describe(points) {
    const v = clean(points).map((x) => x[1]);
    if (!v.length) return {};
    return { count: v.length, min: Math.min(...v), max: Math.max(...v), mean: mean(v), median: median(v), first: v[0], last: v[v.length - 1],
      change_pct: v[0] ? (v[v.length - 1] / v[0] - 1) * 100 : null };
  }
  const median = (a) => { const b = a.slice().sort((x, y) => x - y); const m = b.length >> 1; return b.length % 2 ? b[m] : (b[m - 1] + b[m]) / 2; };
  const lowerBound = (a, x) => { let lo = 0, hi = a.length; while (lo < hi) { const m = (lo + hi) >> 1; if (a[m] < x) lo = m + 1; else hi = m; } return lo; };
  const upperBound = (a, x) => { let lo = 0, hi = a.length; while (lo < hi) { const m = (lo + hi) >> 1; if (a[m] <= x) lo = m + 1; else hi = m; } return lo; };
  function rollingMedian(p, windowS) {
    const ts = p.map((x) => x[0]), half = windowS / 2, out = new Map();
    for (const [t] of p) out.set(t, median(p.slice(lowerBound(ts, t - half), upperBound(ts, t + half)).map((x) => x[1])));
    return out;
  }
  // gleiche Logik wie metrics.outliers (Python): gleitender Median ± k·MAD und mind. 15 %
  function outliers(points, windowS = 2 * DAY, k = 4, minRel = 0.15) {
    const p = clean(points);
    if (p.length < 5) return [];
    const ts = p.map((x) => x[0]), half = windowS / 2, res = [];
    p.forEach(([t, v], i) => {
      const lo = lowerBound(ts, t - half), hi = upperBound(ts, t + half);
      const neigh = []; for (let j = lo; j < hi; j++) if (j !== i) neigh.push(p[j][1]);
      if (neigh.length < 3) return;
      const med = median(neigh), mad = median(neigh.map((x) => Math.abs(x - med))) * 1.4826, dev = v - med;
      if (med > 0 && Math.abs(dev) / med >= minRel && Math.abs(dev) > k * Math.max(mad, med * 0.01))
        res.push({ ts: t, value: v, expected: med, deviation_pct: (dev / med) * 100, kind: dev > 0 ? "Spitze" : "Einbruch" });
    });
    return res;
  }
  const WD = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"];
  // gleiche Logik wie metrics.seasonality (Python), Ortszeit des Browsers
  function seasonality(points) {
    const p = clean(points), out = { hourly: null, weekday: null };
    if (p.length < 24) return out;
    const span = p[p.length - 1][0] - p[0][0], step = span / Math.max(p.length - 1, 1);
    const collect = (windowS, n, keyOf) => {
      const base = rollingMedian(p, windowS), b = Array.from({ length: n }, () => []);
      for (const [t, v] of p) { const m = base.get(t); if (m) b[keyOf(new Date(t * 1000))].push(v / m - 1); }
      return b;
    };
    if (step <= 3 * 3600 && span >= 2 * DAY)
      out.hourly = collect(DAY, 24, (d) => d.getHours()).map((b, h) => ({ hour: h, pct: b.length ? mean(b) * 100 : null, n: b.length }));
    if (span >= 14 * DAY)
      out.weekday = collect(7 * DAY, 7, (d) => (d.getDay() + 6) % 7).map((b, i) => ({ day: WD[i], pct: b.length ? mean(b) * 100 : null, n: b.length }));
    return out;
  }
  function bucket(rows, maxPoints) {
    if (rows.length <= maxPoints || !rows.length) return rows;
    const t0 = rows[0].ts, width = Math.max((rows[rows.length - 1].ts - t0) / maxPoints, 1);
    const out = []; let cur = [], key = null;
    const merge = (rs) => {
      const avg = (k) => { const v = rs.map((r) => r[k]).filter((x) => x !== null && x !== undefined); return v.length ? Math.round(mean(v)) : null; };
      const mins = rs.map((r) => r.min).filter((x) => x !== null), maxs = rs.map((r) => r.max).filter((x) => x !== null);
      return { ts: rs[Math.floor(rs.length / 2)].ts, min: avg("min"), low: mins.length ? Math.min(...mins) : null, p10: avg("p10"),
        median: avg("median"), mean: avg("mean"), max: maxs.length ? Math.max(...maxs) : null, quantity: avg("quantity"), auctions: avg("auctions"), n: rs.every(r=>r.n!==null) ? rs.reduce((s,r)=>s+(r.n??1),0) : null, lowest: rs.some(r=>r.lowest!=null) ? Math.min(...rs.filter(r=>r.lowest!=null).map(r=>r.lowest)) : null, aggregated: true };
    };
    for (const r of rows) {
      const k = Math.floor((r.ts - t0) / width);
      if (key !== null && k !== key) { out.push(merge(cur)); cur = []; }
      key = k; cur.push(r);
    }
    if (cur.length) out.push(merge(cur));
    return out;
  }
  function correlation(pa, pb) {
    const da = new Map(pa.filter((p) => p.v).map((p) => [p.ts, p.v]));
    const db = new Map(pb.filter((p) => p.v).map((p) => [p.ts, p.v]));
    const common = [...da.keys()].filter((t) => db.has(t)).sort((a, b) => a - b);
    if (common.length < 5) return null;
    const ra = [], rb = [];
    for (let i = 1; i < common.length; i++) {
      ra.push(Math.log(da.get(common[i]) / da.get(common[i - 1])));
      rb.push(Math.log(db.get(common[i]) / db.get(common[i - 1])));
    }
    const ma = mean(ra), mb = mean(rb);
    const sa = Math.sqrt(ra.reduce((s, x) => s + (x - ma) ** 2, 0)), sb = Math.sqrt(rb.reduce((s, x) => s + (x - mb) ** 2, 0));
    if (!sa || !sb) return null;
    return ra.reduce((s, x, i) => s + (x - ma) * (rb[i] - mb), 0) / (sa * sb);
  }

  async function historyRows(file, h, range) {
    const c = file.history[String(h.id)];
    if (!c) return [];
    if (!(range in RANGES)) throw new HttpError(`Zeitraum ${range} ungültig`);
    const span = RANGES[range];
    const lo = span ? h.last_ts - span : 0;
    if (!c._rows) c._rows = toRows(c);
    return c._rows.filter((r) => r.ts >= lo);
  }

  // ------------------------------------------------------------- Endpunkte
  async function classes(params) {
    const h=await house(params.house),out=new Map();
    const reliable=!!h.quality?.complete && h.quality.ts>=Date.now()/1000-10800;
    for(const r of await summary(h.id)) {
      const cid=r.class_id??-1;
      if(!out.has(cid))out.set(cid,{class_id:cid,class_name:r.class_name||CLASS_NAMES[cid]||"Ohne Metadaten",items:0,market_value:0,quantity:0,subs:new Map(),_24:[],_7:[]});
      const c=out.get(cid),sid=r.subclass_id??null;
      if(!c.subs.has(sid))c.subs.set(sid,{subclass_id:sid,subclass_name:r.subclass_name||(sid===null?"Unbekannt":`Unterklasse ${sid}`),items:0,market_value:0,quantity:0,_24:[],_7:[]});
      for(const target of [c,c.subs.get(sid)]) {
        target.items++;target.market_value+=r.market_value||0;target.quantity+=r.current_quantity||0;
        if(reliable&&r.is_current&&r.chg24_min!=null)target._24.push(r.chg24_min);
        if(reliable&&r.is_current&&r.chg7d_min!=null)target._7.push(r.chg7d_min);
      }
    }
    const finish=t=>{for(const [key,name] of [["_24","chg24"],["_7","chg7d"]]){t[name]=t[key].length?median(t[key]):null;t[name+"_n"]=t[key].length;delete t[key];}return t;};
    const result=[...out.values()].map(c=>{c.subclasses=[...c.subs.values()].sort((a,b)=>(a.subclass_id??-1)-(b.subclass_id??-1)).map(finish);delete c.subs;return finish(c);});
    result.sort((a,b)=>b.market_value-a.market_value);
    return {house_id:h.id,classes:result};
  }

  const SORTS = { name: "name", price: "last_min", median: "last_median", qty: "last_qty", avg_qty: "avg7_qty", chg24: "chg24_min",
    chg7d: "chg7d_min", vol: "cv7_min", value: "market_value", quality: "quality", id: "item_id", updated: "last_ts", avg30: "avg30_min" };

  // --- Kategorie-Filter: Rezeptdaten einmal laden (fehlt die Datei, z. B. Demo-Export,
  // wirken Beruf-/Spannenfilter nicht, statt die Tabelle zu leeren). Spiegel von api.search. ---
  let recipeFilterP = null;
  function recipeFilters() {
    if (!recipeFilterP) recipeFilterP = load("data/recipes.json").then((d) => {
      const profs = d.professions || [];
      const cols = d.recipes?.cols || [];
      const ci = Object.fromEntries(cols.map((c, i) => [c, i]));
      const byItem = new Map(), byId = new Map();
      const add = (iid, pid) => { if (iid == null || pid == null) return; const s = byItem.get(iid) || new Set(); s.add(pid); byItem.set(iid, s); };
      for (const row of d.recipes?.rows || []) {
        byId.set(row[ci.id], row);
        const pid = (profs[row[ci.prof]] || {}).id;
        for (const x of row[ci.reagents] || []) if (Array.isArray(x)) add(x[0], pid);
        add(row[ci.item], pid);
      }
      return { data: d, ci, byId, byItem };
    }).catch(() => null);
    return recipeFilterP;
  }
  // Spanne im AH-Modus: Verkauf nach 5 % Gebühr minus Materialkosten je Stück.
  // Spiegel von recipes.profitable_recipe_items (Server) bzw. RecipeCore.margin (Item-Seite).
  function recipeProfitable(rf, facs, row, ruleset="normal") {
    const ci = rf.ci;
    const price = (fac, iid) => { const p = rf.data.prices?.[`${ruleset}:${fac}`]?.items?.[String(iid)]; return p && p[0] > 0 ? p[0] : null; };
    for (const fac of facs) {
      const item = row[ci.item];
      const sell = item ? price(fac, item) : null;
      if (!sell) continue;
      const qmin = row[ci.qmin] || 1, qavg = (qmin + (row[ci.qmax] || qmin)) / 2;
      let cost = 0, complete = true;
      for (const x of row[ci.reagents] || []) {
        if (!Array.isArray(x)) continue;
        const u = price(fac, x[0]);
        if (u == null) { complete = false; break; }
        cost += u * x[1];
      }
      if (complete && sell * 0.95 - cost / qavg > 0) return true;
    }
    return false;
  }

  async function search(params) {
    const m = await meta();
    let hs = ids(params.house).map(id=>m.legacy_aliases?.[String(id)] || id);
    if (!hs.length) hs = [m.default];
    if (!hs.length) hs = m.houses.filter((h) => (!params.region || h.region === params.region) && (!params.faction || h.faction === params.faction)
      && (!params.source || h.source === params.source)).map((h) => h.id);
    else hs = hs.filter((id) => m.houses.some((h) => h.id === id && (!params.region || h.region === params.region) && (!params.faction || h.faction === params.faction)));
    let rows = (await Promise.all(hs.map(summary))).flat();
    const q = fold((params.q || "").trim());
    // Vollständiger Itemindex; der Markt ist nur ein Preis-Overlay.
    const seen = new Set(rows.map((r) => r.item_id));
    rows = rows.concat((await catalog()).filter((r) => !seen.has(r.item_id)));
    const wantedItems = ids(params.item_ids);
    if (wantedItems.length) rows = rows.filter(r => wantedItems.includes(r.item_id));
    const keyOf = (r) => (r._k ??= fold(r.name) + "|" + fold(r.name_en) + "|" + (r.aliases || []).map(fold).join("|"));
    if (q) {
      // zweisprachig (deutscher + englischer Name) und umlaut-tolerant
      if (/^\d+$/.test(q)) rows = rows.filter((r) => r.item_id === Number(q) || keyOf(r).includes(q));
      else { const words = q.split(/\s+/); rows = rows.filter((r) => words.every((w) => keyOf(r).includes(w))); }
    }
    for (const [k, col] of [["class", "class_id"], ["subclass", "subclass_id"]]) {
      if (params[k] !== undefined && params[k] !== "") rows = rows.filter((r) => (params[k] === "-1" ? r[col] === null : r[col] === Number(params[k])));
    }
    const quals = ids(params.quality);
    if (quals.length) rows = rows.filter((r) => quals.includes(r.quality));
    if (params.listed === "1") rows = rows.filter((r) => r.is_current === true);
    const minL = num(params.min_lvl), maxL = num(params.max_lvl);
    // Ohne bekannte Stufen im Datenbestand (älterer Export) wirkt der Filter nicht.
    if ((minL !== null && !isNaN(minL) || maxL !== null && !isNaN(maxL)) && rows.some((r) => r.required_level !== null && r.required_level !== undefined))
      rows = rows.filter((r) => r.required_level !== null && r.required_level !== undefined
        && (minL === null || isNaN(minL) || r.required_level >= minL)
        && (maxL === null || isNaN(maxL) || r.required_level <= maxL));
    const profsWanted = ids(params.prof);
    if (profsWanted.length || params.margin === "1") {
      const rf = await recipeFilters();
      if (rf) {
        if (profsWanted.length) rows = rows.filter((r) => { const s = rf.byItem.get(r.item_id); return !!s && profsWanted.some((p) => s.has(p)); });
        if (params.margin === "1") {
          let facs = [...new Set(rows.map((r) => String(r.faction || "").toLowerCase()).filter(Boolean))];
          if (!facs.length) facs = ["allianz", "horde", "neutral"];
          const teaches = rf.data.teaches || {};
          rows = rows.filter((r) => (teaches[String(r.item_id)] || []).some((rid) => {
            const row = rf.byId.get(rid);
            return row && recipeProfitable(rf, facs, row, m.houses.find(h=>h.id===hs[0])?.ruleset_key || "normal");
          }));
        }
      }
    }
    const flt = [["min_price", "last_min", 1], ["max_price", "last_min", -1], ["min_qty", "last_qty", 1], ["max_qty", "last_qty", -1],
      ["min_avg_qty", "avg7_qty", 1], ["min_chg", "chg24_min", 1], ["max_chg", "chg24_min", -1]];
    for (const [k, col, sgn] of flt) {
      const v = num(params[k]);
      if (v !== null && !isNaN(v)) rows = rows.filter((r) => r.is_current && r[col] !== null && r[col] !== undefined && (sgn > 0 ? r[col] >= v : r[col] <= v));
    }
    if (params.commodity === "0" || params.commodity === "1") rows = rows.filter((r) => (r.is_commodity || 0) === Number(params.commodity));
    if (params.sort === "relevance" && q) {
      const rank = (r) => { const de = fold(r.name), en = fold(r.name_en), als = (r.aliases || []).map(fold);
        return de.startsWith(q) || en.startsWith(q) || als.some((x) => x.startsWith(q)) ? 0 : de.includes(" " + q) || en.includes(" " + q) ? 1 : 2; };
      rows.sort((a, b) => rank(a) - rank(b) || (b.market_value || 0) - (a.market_value || 0) || a.item_id - b.item_id);
    } else sortRows(rows, SORTS[params.sort] || "market_value", params.dir);
    const pg = page(rows, params);
    return { total: pg.total, page: pg.page, page_size: pg.page_size, items: pg.slice };
  }

  async function movers(params) {
    const h = await house(params.house);
    const window = params.window || "24h", metric = params.metric || "min";
    const col = `chg${window === "24h" ? "24" : "7d"}_${metric}`;
    let rows = (await summary(h.id)).filter((r) => r.is_current && r[col] !== null && r[col] !== undefined);
    const mq = num(params.min_qty), mp = num(params.min_price);
    if (mq) rows = rows.filter((r) => (r.avg7_qty ?? r.last_qty ?? 0) >= mq);
    if (mp) rows = rows.filter((r) => (r.last_min ?? 0) >= mp);
    if (params.class !== undefined && params.class !== "") rows = rows.filter((r) => r.class_id === Number(params.class));
    if (params.relevant === "1") rows = rows.filter(isRelevant);
    const limit = Math.min(200, num(params.limit) || 25);
    return { house_id: h.id, window, metric, column: col,
      gainers: sortRows(rows.filter((r) => r[col] > 0), col, "desc").slice(0, limit),
      losers: sortRows(rows.filter((r) => r[col] < 0), col, "asc").slice(0, limit) };
  }

  async function liquidity(params) {
    const h = await house(params.house);
    let rows = (await summary(h.id)).filter((r) => r.liq_score !== null && r.liq_score !== undefined);
    if (params.class !== undefined && params.class !== "") rows = rows.filter((r) => r.class_id === Number(params.class));
    const limit = Math.min(200, num(params.limit) || 30);
    const cur = rows.filter((r) => r.is_current);
    return { house_id: h.id, window_days: 7,
      computed_at: cur.reduce((m, r) => Math.max(m, r.liq_ts || 0), 0) || null,
      hot: sortRows([...cur], "liq_score", "desc").slice(0, limit),
      cold: sortRows(cur.filter((r) => (r.liq_events || 0) <= 1), "market_value", "desc").slice(0, limit) };
  }

  async function deals(params) {
    const h = await house(params.house);
    const basis = params.basis || "avg7";
    const refKey = `${basis}_${params.ref === "median" ? "median" : "min"}`;
    const th = (num(params.threshold) ?? 20) / 100;
    let rows = (await summary(h.id)).filter((r) => r.is_current && r.last_ts >= Date.now() / 1000 - 3 * 3600 && r[refKey] > 0 && r.last_min !== null && r.last_min <= r[refKey] * (1 - th) && r.points >= 3);
    const mq = num(params.min_qty);
    if (mq) rows = rows.filter((r) => (r.avg7_qty || 0) >= mq);
    if (params.class !== undefined && params.class !== "") rows = rows.filter((r) => r.class_id === Number(params.class));
    if (params.relevant === "1") rows = rows.filter(isRelevant);
    const lm = num(params.liq_min);
    if (lm) rows = rows.filter((r) => (r.liq_score || 0) >= lm);
    rows = rows.map((r) => ({ ...r, ref_price: r[refKey], discount: (1 - r.last_min / r[refKey]) * 100, saving: r[refKey] - r.last_min }));
    sortRows(rows, "discount", "desc");
    return { house_id: h.id, basis, threshold: th * 100, deals: rows.slice(0, Math.min(500, num(params.limit) || 50)) };
  }

  async function overview(params) {
    const h = await house(params.house);
    const rows = (await summary(h.id)).filter((r) => r.is_current);
    const snaps = await snapshots(h.id);
    const avgOf = (k, a, b) => { const v = rows.filter((r) => r[b] > 0 && r[a] !== null).map((r) => r[a] / r[b] - 1); return v.length ? mean(v) * 100 : null; };
    const sum = (k) => rows.reduce((s, r) => s + (r[k] || 0), 0);
    const p = { house: h.id, limit: 6 };
    return {
      house: h, quality: h.quality || {},
      kpis: { items: rows.length, market_value: sum("market_value"), quantity: sum("last_qty"),
        auctions: rows.some((r) => r.last_auctions !== null) ? sum("last_auctions") : null,
        avg_chg24: avgOf(null, "last_min", "prev24_min"), avg_chg7d: avgOf(null, "last_min", "prev7d_min"),
        ...Object.fromEntries([["24","prev24_min"],["7d","prev7d_min"]].flatMap(([label,col]) => {
          const vals = rows.filter(r=>r[col]>0 && r.last_min>0).map(r=>(r.last_min/r[col]-1)*100);
          return [[`median_chg${label}`,vals.length ? median(vals) : null],[`comparable${label}`,vals.length],
            [`up${label}`,vals.filter(x=>x>0).length],[`down${label}`,vals.filter(x=>x<0).length]];
        })), comparisons_reliable: !!h.quality?.complete && h.quality.ts >= Date.now()/1000-10800 },
      last_ts: snaps.length ? snaps[snaps.length - 1].ts : null,
      snapshot_count: snaps.length,
      market_series: snaps.slice(-400),
      categories: (await classes({ house: h.id })).classes,
      movers: await movers({ ...p, window: "24h", relevant: "1" }),
      deals: await deals({ ...p, threshold: 15, relevant: "1", liq_min: 30 }),
    };
  }

  async function itemHouses(f) {
    if (!f._houses) {
      const m = await meta();
      const raw = Array.isArray(f.houses) ? f.houses : toRows(f.houses);
      f._houses = raw.map((r) => {
        const h = m.houses.find((x) => x.id === r.house_id) || {};
        return { ...r, item_id: f.item.id, name: f.item.name, name_en: f.item.name_en, quality: f.item.quality, icon: f.item.icon,
          house_name: h.name, region: h.region, faction: h.faction, granularity: h.granularity,
          kind: h.kind, label: h.label, short: h.short, verbund: h.verbund, group: h.group, ruleset_key: h.ruleset_key, ruleset_label: h.ruleset_label, ruleset_label_en: h.ruleset_label_en, faction: h.faction };
      });
    }
    return f._houses;
  }
  /** Items ohne AH-Daten (Rezepte, Quests, Sets, QuestieDB) – für Suche und Item-Seite. */
  async function catalog() {
    if (!(await meta()).counts?.catalog && !(await meta()).search_items) return [];
    const c = await load("data/catalog.json");
    if (!c._rows) c._rows = toRows(c).map((r) => ({ ...r, house_id: null, no_ah: true, last_min: null, last_median: null, last_qty: null, current_quantity: null, is_current: false, availability: "no_local_data" }));
    return c._rows;
  }
  const recipes = () => load("data/recipes.json");
  const quests = () => load("data/quests.json");
  /** Herkunft (QuestieDB) eines Items: eine von meta.counts.source_shards Dateien. */
  async function sources(id) {
    const n = (await meta()).counts?.source_shards || 0;
    if (!n) return { items: {}, npcs: {}, objects: {}, quests: {}, names: {} };
    return load(`data/sources/${id % n}.json`);
  }
  async function item(id) {
    // Item ohne AH-Daten, aber aus Rezepten/Quests/Sets bekannt → Seite nur mit Herkunft/Verwendung
    const [r, q] = await Promise.all([recipes().catch(() => null), quests().catch(() => null)]);
    let m = r?.items?.[String(id)] || q?.items?.[String(id)];
    const onlyRecipe = () => ({ item: { id, name: m[0], quality: m[1], icon: m[2], ...(m[4] || {}) }, houses: [] });
    const c = (await catalog().catch(() => [])).find((x) => x.item_id === id);
    if (c) m = [c.name, c.quality, c.icon, c.has_history ? 1 : 0, { ...(m?.[4] || {}), ...c, id }];
    if (m && !m[3]) return onlyRecipe();
    let f;
    try { f = await itemFile(id); } catch (e) {
      if (!m) throw e;
      return onlyRecipe();
    }
    return { item: f.item, houses: await itemHouses(f), liquidity: f.liquidity || {} };
  }
  async function history(id, params) {
    const h = await house(params.house);
    const f = await itemFile(id);
    const rows = await historyRows(f, h, params.range || "7d");
    return { house_id: h.id, item_id: id, range: params.range || "7d", raw_points: rows.length, points: bucket(rows, num(params.max_points) || 720) };
  }
  async function analysis(id, params) {
    const h = await house(params.house);
    const f = await itemFile(id);
    const rows = await historyRows(f, h, RANGES[params.range] !== undefined ? params.range : "all");
    let metric = params.metric || "min";
    if (metric !== "min" && !rows.some((r) => r[metric])) metric = "min";
    const series = rows.map((r) => [r.ts, r[metric]]);
    return { describe: describe(series), volatility: volatility(series), trend: trend(series), outliers: outliers(series),
      seasonality: seasonality(series), quantity: describe(rows.map((r) => [r.ts, r.quantity])), metric, points: series.length };
  }
  async function itemSnapshots(id, params) {
    const h = await house(params.house);
    const f = await itemFile(id);
    const rows = (await historyRows(f, h, "all")).slice();
    const snaps = await snapshots(h.id);
    const lad = new Map(snaps.map((s) => [s.ts, s.has_ladder && s.file]));
    const out = rows.map((r, i) => ({ ...r, prev_min: i ? rows[i - 1].min : null, has_ladder: lad.get(r.ts) ? 1 : 0, daily: !!r.aggregated, merged: !!r.aggregated || (r.n || 1) > 1 }))
      .map((r) => ({ ...r, chg_min: r.prev_min ? (r.min / r.prev_min - 1) * 100 : null })).reverse();
    const pg = page(out, params);
    return { total: pg.total, page: pg.page, page_size: pg.page_size, rows: pg.slice };
  }
  async function snapshot(hid, ts, params) {
    const h = await house(hid);
    const snaps = await snapshots(h.id);
    const idx = snaps.findIndex((s) => s.ts === ts);
    if (idx < 0) throw new HttpError(T("Snapshot nicht gefunden"));
    const s = snaps[idx];
    if (!s.file) throw new HttpError(T("Für diesen älteren Snapshot enthält der statische Export keine Detaildaten (nur die jüngsten Snapshots je Haus)."));
    const data = await load(`data/house/${h.id}/snap/${ts}.json`);
    const meta = new Map((await summary(h.id)).map((r) => [r.item_id, r]));
    let rows = toRows(data).map((r) => {
      const m = meta.get(r.item_id) || {};
      return { ...r, house_id: h.id, ts, name: m.name ?? `Item #${r.item_id}`, name_en: m.name_en, quality: m.quality, icon: m.icon, class_id: m.class_id,
        class_name: m.class_name, subclass_name: m.subclass_name, value: (r.min || 0) * (r.quantity || 0),
        chg: r.prev_min > 0 ? (r.min / r.prev_min - 1) * 100 : null };
    });
    const q = fold((params.q || "").trim());
    if (q) rows = rows.filter((r) => fold(r.name).includes(q) || fold(r.name_en).includes(q) || String(r.item_id) === q);
    if (params.class !== undefined && params.class !== "") rows = rows.filter((r) => r.class_id === Number(params.class));
    const sorts = { value: "value", price: "min", qty: "quantity", chg: "chg", name: "name", auctions: "auctions" };
    sortRows(rows, sorts[params.sort] || "value", params.dir);
    const pg = page(rows, params);
    const withFile = snaps.filter((x) => x.file);
    const wi = withFile.findIndex((x) => x.ts === ts);
    const res = { snapshot: { ...s, house_id: h.id }, house: h, prev_ts: wi > 0 ? withFile[wi - 1].ts : null,
      next_ts: wi < withFile.length - 1 ? withFile[wi + 1].ts : null, total: pg.total, page: pg.page, page_size: pg.page_size, items: pg.slice };
    if (params.item) {
      const fid = Number(params.item);
      const st = toRows(data).find((r) => r.item_id === fid) || null;
      const m = meta.get(fid);
      res.focus = { item: m ? { ...m } : { item_id: fid, name: `Item #${fid}` }, stats: st, ladder: data.ladders[String(fid)] || null };
    }
    return res;
  }
  async function compare(params) {
    const list = ids(params.items).slice(0, 8);
    if (!list.length) throw new HttpError(T("Parameter items fehlt"));
    const h = await house(params.house);
    const metric = params.metric || "min";
    const series = [];
    for (const id of list) {
      let f;
      try { f = await itemFile(id); } catch { continue; }
      const rows = await historyRows(f, h, params.range || "7d");
      const pts = bucket(rows, num(params.max_points) || 400);
      const vals = rows.map((r) => [r.ts, r[metric]]);
      series.push({ item_id: id, name: f.item.name, quality: f.item.quality, icon: f.item.icon,
        points: pts.map((p) => ({ ts: p.ts, v: p[metric] })), summary: (await itemHouses(f)).find((x) => x.house_id === h.id) || null,
        trend: trend(vals), volatility: volatility(vals), describe: describe(vals) });
    }
    return { house_id: h.id, metric, series, correlation: series.map((a) => series.map((b) => correlation(a.points, b.points))) };
  }
  async function status() {
    const m = await meta();
    return { static: true, market_runs: m.market_runs || [], catalog_coverage: m.catalog_coverage, generated_at: m.generated_at, counts: m.counts, repo_url: m.repo_url, history_days: m.history_days,
      battlenet_configured: false, auctionator_paths: [], collector: null };
  }

  const routes = [
    [/^status$/, () => status()],
    [/^houses$/, async () => { const m = await meta(); return { houses: m.houses, default: m.default, legacy_aliases: m.legacy_aliases }; }],
    [/^overview$/, (m, p) => overview(p)],
    [/^classes$/, (m, p) => classes(p)],
    [/^search$/, (m, p) => search(p)],
    [/^item\/(\d+)$/, (m) => item(+m[1])],
    [/^item\/(\d+)\/history$/, (m, p) => history(+m[1], p)],
    [/^item\/(\d+)\/analysis$/, (m, p) => analysis(+m[1], p)],
    [/^item\/(\d+)\/snapshots$/, (m, p) => itemSnapshots(+m[1], p)],
    [/^snapshot\/(\d+)\/(\d+)$/, (m, p) => snapshot(+m[1], +m[2], p)],
    [/^compare$/, (m, p) => compare(p)],
    [/^movers$/, (m, p) => movers(p)],
    [/^deals$/, (m, p) => deals(p)],
    [/^liquidity$/, (m, p) => liquidity(p)],
    [/^recipes$/, () => recipes()],
    [/^quests$/, () => quests()],
    [/^sources\/(\d+)$/, (m) => sources(+m[1])],
    [/^import\/log$/, async () => ({ log: (await meta()).import_log || [] })],
  ];

  async function request(path, params, opts = {}) {
    if (opts.method && opts.method !== "GET") throw new Error(T("Im statischen Modus (GitHub Pages) nicht verfügbar – dafür die Website lokal starten."));
    for (const [rx, fn] of routes) {
      const m = path.match(rx);
      if (m) return fn(m, params);
    }
    throw new HttpError(T("Unbekannter Endpunkt"));
  }

  return { detect, request, meta };
})();
