/* Forever AH-Analytics – Single-Page-Frontend (ohne Build-Schritt). */
"use strict";

// ================================================================ Basics
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const main = $("#main");
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const store = {
  get(k, d) { try { const v = localStorage.getItem("ah." + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("ah." + k, JSON.stringify(v)); } catch { /* egal */ } },
};
const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const SERIES = ["--s1", "--s2", "--s3", "--s4", "--s5", "--s6", "--s7", "--s8"];
const TZ = -new Date().getTimezoneOffset();

const state = {
  house: store.get("house", null),
  houses: [],
  compare: store.get("compare", []),
  charts: [],
  route: null,
};

let inflight = new Set();
let STATIC = false;
async function api(path, params = {}, opts = {}) {
  if (STATIC) {
    const p = {};
    for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== "") p[k] = String(v);
    return StaticAPI.request(path, p, opts);
  }
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== "") qs.set(k, v);
  const url = "/api/" + path + (qs.toString() ? "?" + qs : "");
  const ctrl = new AbortController();
  inflight.add(ctrl);
  try {
    const r = await fetch(url, { signal: ctrl.signal, ...opts });
    const data = await r.json().catch(() => ({ error: "Ungültige Antwort" }));
    if (!r.ok) throw new Error(data.error || r.statusText);
    return data;
  } finally { inflight.delete(ctrl); }
}

function toast(msg, ms = 3500) {
  const t = $("#toast"); t.textContent = msg; t.hidden = false;
  clearTimeout(toast._t); toast._t = setTimeout(() => (t.hidden = true), ms);
}

// ============================================================ Formatierung
function money(c, opts = {}) {
  if (c == null || isNaN(c)) return '<span class="muted">–</span>';
  c = Math.round(c);
  const neg = c < 0; c = Math.abs(c);
  const g = Math.floor(c / 10000), s = Math.floor((c % 10000) / 100), k = c % 100;
  let out = "";
  if (g) out += `<span class="g">${g.toLocaleString("de-DE")}</span><i class="g">g</i>`;
  if (g && (opts.short || g >= 1000)) return `<span class="money">${neg ? "−" : ""}${out}</span>`;
  if (s || (g && k)) out +=`<span class="s">${s}</span><i class="s">s</i>`;
  if (!g && (k || !s)) out += `<span class="c">${k}</span><i class="c">c</i>`;
  else if (g && k && !opts.short && g < 100) out += `<span class="c">${k}</span><i class="c">c</i>`;
  return `<span class="money">${neg ? "−" : ""}${out}</span>`;
}
function moneyText(c) {
  if (c == null || isNaN(c)) return "–";
  c = Math.round(c);
  const g = Math.floor(c / 10000), s = Math.floor((c % 10000) / 100), k = c % 100;
  if (g >= 100) return `${g.toLocaleString("de-DE")}g`;
  if (g) return `${g}g ${s}s`;
  if (s) return `${s}s ${k}c`;
  return `${k}c`;
}
const num = (v, d = 0) => (v == null || isNaN(v) ? "–" : Number(v).toLocaleString("de-DE", { maximumFractionDigits: d, minimumFractionDigits: d }));
const compact = (v) => (v == null ? "–" : Intl.NumberFormat("de-DE", { notation: "compact", maximumFractionDigits: 1 }).format(v));
function pct(v, d = 1) {
  if (v == null || isNaN(v)) return '<span class="muted">–</span>';
  const cls = v > 0.05 ? "up" : v < -0.05 ? "down" : "muted";
  const arrow = v > 0.05 ? "▲" : v < -0.05 ? "▼" : "■";
  return `<span class="chg ${cls}">${arrow} ${v > 0 ? "+" : ""}${num(v, d)} %</span>`;
}
const dt = (ts, withTime = true) => ts ? new Date(ts * 1000).toLocaleString("de-DE", withTime ? { day: "2-digit", month: "2-digit", year: "2-digit", hour: "2-digit", minute: "2-digit" } : { day: "2-digit", month: "2-digit", year: "numeric" }) : "–";
function ago(ts) {
  if (!ts) return "–";
  const s = Date.now() / 1000 - ts;
  if (s < 3600) return `vor ${Math.max(1, Math.round(s / 60))} Min.`;
  if (s < 86400) return `vor ${Math.round(s / 3600)} Std.`;
  return `vor ${Math.round(s / 86400)} Tagen`;
}
const QUAL = ["Schlecht", "Gewöhnlich", "Ungewöhnlich", "Selten", "Episch", "Legendär", "Artefakt", "Erbstück"];

function iconUrl(icon) {
  if (!icon) return null;
  if (/^https?:/.test(icon)) return icon;
  return `https://wow.zamimg.com/images/wow/icons/medium/${encodeURIComponent(icon)}.jpg`;
}
function icon(it, size = "") {
  const q = it.quality ?? 1;
  const url = iconUrl(it.icon);
  const letter = esc((it.name || "?").replace(/^Item #/, "#").slice(0, 1));
  const cls = `ico ${size === true ? "big" : size || ""} bq${q}`;
  if (!url) return `<span class="${cls}">${letter}</span>`;
  return `<img class="${cls}" src="${url}" alt="" loading="lazy" onerror="this.outerHTML='<span class=&quot;${cls}&quot;>${letter}</span>'">`;
}
function itemLabel(it, link = true) {
  const id = it.item_id ?? it.id;
  const name = `<span class="q${it.quality ?? 1}">${esc(it.name || "Item #" + id)}</span>`;
  const inner = `${icon(it)}${name}`;
  return link ? `<a class="item" href="#/item/${id}${it.house_id ? "?house=" + it.house_id : ""}">${inner}</a>` : `<span class="item">${inner}</span>`;
}
function sparkline(vals, w = 90, h = 24) {
  const v = (vals || []).filter((x) => x != null);
  if (v.length < 2) return '<span class="muted small">–</span>';
  const mn = Math.min(...v), mx = Math.max(...v), rg = mx - mn || 1;
  const pts = v.map((x, i) => `${(i / (v.length - 1)) * (w - 2) + 1},${h - 2 - ((x - mn) / rg) * (h - 4)}`).join(" ");
  const col = v[v.length - 1] >= v[0] ? css("--up") : css("--down");
  return `<svg class="spark" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" aria-hidden="true"><polyline fill="none" stroke="${col}" stroke-width="1.6" stroke-linejoin="round" points="${pts}"/></svg>`;
}

// ================================================================ Routing
function parseHash() {
  const h = location.hash.replace(/^#/, "") || "/";
  const [path, q] = h.split("?");
  return { path, parts: path.split("/").filter(Boolean), params: Object.fromEntries(new URLSearchParams(q || "")) };
}
function go(path, params = {}, replace = false) {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== "") qs.set(k, v);
  const h = "#" + path + (qs.toString() ? "?" + qs : "");
  if (replace) history.replaceState(null, "", h), route();
  else location.hash = h;
}
function setParams(patch, replace = true) {
  const r = parseHash();
  go(r.path, { ...r.params, ...patch }, replace);
}

const routes = {
  "": viewDashboard, search: viewSearch, categories: viewCategories, item: viewItem,
  snapshot: viewSnapshot, compare: viewCompare, movers: viewMovers, deals: viewDeals, data: viewData,
};

async function route() {
  const r = parseHash();
  state.route = r;
  if (r.params.house && houseById(r.params.house) && String(r.params.house) !== String(state.house)) {
    state.house = String(r.params.house); store.set("house", state.house);
    $("#houseSel").value = state.house; classCache = {};
    updateFreshness();
  }
  inflight.forEach((c) => c.abort()); inflight.clear();
  state.charts.forEach((c) => c.destroy()); state.charts = [];
  const key = r.parts[0] || "";
  $$("#nav a").forEach((a) => a.classList.toggle("active", (a.dataset.route === (key || "dashboard"))));
  const view = routes[key] || viewNotFound;
  main.innerHTML = '<div class="loading">Lade …</div>';
  try {
    await view(r);
  } catch (e) {
    if (e.name === "AbortError") return;
    main.innerHTML = `<div class="error"><b>Fehler:</b> ${esc(e.message)}</div>` +
      (/Keine Daten/.test(e.message) ? `<p class="mt"><a class="btn primary" href="#/data">Zu den Datenquellen</a></p>` : "");
  }
}
function viewNotFound() { main.innerHTML = '<div class="empty">Seite nicht gefunden.</div>'; }
const curHouse = (p) => p?.house || state.house || "";
const houseById = (id) => state.houses.find((h) => String(h.id) === String(id));

// ================================================================ Charts
Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
Chart.defaults.font.size = 12;
Chart.defaults.animation = false;
function chartTheme() {
  Chart.defaults.color = css("--text-secondary");
  Chart.defaults.borderColor = css("--grid");
}
function timeTick(span) {
  return (v) => {
    const d = new Date(v * 1000);
    if (span <= 2 * 86400) return d.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
    if (span <= 60 * 86400) return d.toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit" }) + (span <= 4 * 86400 && d.getHours() ? " " + d.getHours() + "h" : "");
    return d.toLocaleDateString("de-DE", { month: "short", year: "2-digit" });
  };
}
function makeChart(canvas, cfg) {
  chartTheme();
  const c = new Chart(canvas, cfg);
  state.charts.push(c);
  return c;
}
function timeLineChart(canvas, datasets, { yFmt = moneyText, span = 7 * 86400, onClick, yTitle, extraTooltip, minY } = {}) {
  return makeChart(canvas, {
    type: "line",
    data: { datasets },
    options: {
      responsive: true, maintainAspectRatio: false, parsing: false, normalized: true,
      interaction: { mode: "nearest", axis: "x", intersect: false },
      scales: {
        x: { type: "linear", ticks: { callback: timeTick(span), maxRotation: 0, autoSkipPadding: 18 }, grid: { display: false } },
        y: { ticks: { callback: yFmt }, title: { display: !!yTitle, text: yTitle }, min: minY, grace: "5%" },
      },
      plugins: {
        legend: { display: datasets.filter((d) => !d._noLegend).length > 1, position: "top", align: "start", labels: { usePointStyle: true, pointStyle: "line", boxWidth: 18, filter: (l, d) => !d.datasets[l.datasetIndex]._noLegend } },
        tooltip: {
          callbacks: {
            title: (items) => dt(items[0].parsed.x),
            label: (ctx) => ` ${ctx.dataset.label}: ${yFmt(ctx.parsed.y)}`,
            afterBody: extraTooltip,
          },
        },
      },
      onClick: onClick ? (evt, els, chart) => {
        const pts = chart.getElementsAtEventForMode(evt, "nearest", { axis: "x", intersect: false }, false);
        if (pts.length) onClick(chart.data.datasets[pts[0].datasetIndex].data[pts[0].index]);
      } : undefined,
      onHover: onClick ? (evt, els) => { evt.native.target.style.cursor = els.length ? "pointer" : "default"; } : undefined,
    },
  });
}
function line(label, data, colorVar, extra = {}) {
  const c = css(colorVar);
  return { label, data, borderColor: c, backgroundColor: c, borderWidth: 2, pointRadius: 0, pointHoverRadius: 4, tension: 0.15, spanGaps: true, ...extra };
}
function divergingBars(canvas, labels, values, title) {
  const pos = css("--div-pos"), neg = css("--div-neg");
  return makeChart(canvas, {
    type: "bar",
    data: { labels, datasets: [{ label: title, data: values, backgroundColor: values.map((v) => (v >= 0 ? pos : neg)), borderRadius: 4, borderSkipped: false, maxBarThickness: 26 }] },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false }, tooltip: { callbacks: { label: (c) => ` ${c.parsed.y > 0 ? "+" : ""}${num(c.parsed.y, 2)} % ggü. normal` } } },
      scales: { y: { ticks: { callback: (v) => v + " %" } }, x: { grid: { display: false } } },
    },
  });
}

// ================================================================ Tabellen
/** Generische sortierbare Tabelle. cols: {key,label,num,sort,fmt(row)} */
function table(cols, rows, { sort, dir, onSort, rowHref, empty = "Keine Einträge." } = {}) {
  if (!rows.length) return `<div class="empty">${empty}</div>`;
  const th = cols.map((c) => {
    const s = c.sort ? `sortable ${sort === c.sort ? "sorted" + (dir === "asc" ? " asc" : "") : ""}` : "";
    return `<th class="${c.num ? "n" : ""} ${s}" ${c.sort ? `data-sort="${c.sort}"` : ""} ${c.title ? `title="${esc(c.title)}"` : ""}>${c.label}</th>`;
  }).join("");
  const tb = rows.map((r, i) => { const href = rowHref && rowHref(r); return `<tr data-i="${i}" ${href ? `data-href="${esc(href)}"` : 'class="nohover"'}>`; }).map((open, i) => open +
    cols.map((c) => `<td class="${c.num ? "n" : ""} ${c.cls || ""}">${c.fmt ? c.fmt(rows[i]) : esc(rows[i][c.key])}</td>`).join("") + "</tr>").join("");
  const id = "t" + Math.random().toString(36).slice(2, 8);
  setTimeout(() => {
    const el = document.getElementById(id);
    if (!el) return;
    el.addEventListener("click", (e) => {
      const th = e.target.closest("th[data-sort]");
      if (th && onSort) {
        const k = th.dataset.sort;
        onSort(k, sort === k && dir !== "asc" ? "asc" : "desc");
        return;
      }
      if (e.target.closest("a, input, button")) return;
      const tr = e.target.closest("tr[data-href]");
      if (tr) location.hash = tr.dataset.href;
    });
  });
  return `<div class="tablewrap"><table class="t" id="${id}"><thead><tr>${th}</tr></thead><tbody>${tb}</tbody></table></div>`;
}
function pager(total, page, size, onPage) {
  const pages = Math.max(1, Math.ceil(total / size));
  const id = "p" + Math.random().toString(36).slice(2, 8);
  setTimeout(() => {
    const el = document.getElementById(id);
    if (!el) return;
    el.addEventListener("click", (e) => { const b = e.target.closest("button[data-p]"); if (b) onPage(+b.dataset.p); });
  });
  return `<div class="pager" id="${id}">${num(total)} Einträge · Seite ${page} / ${pages}
    <button class="btn small" data-p="${page - 1}" ${page <= 1 ? "disabled" : ""}>‹ Zurück</button>
    <button class="btn small" data-p="${page + 1}" ${page >= pages ? "disabled" : ""}>Weiter ›</button></div>`;
}

const COL = {
  item: { label: "Item", sort: "name", cls: "itemcell", fmt: (r) => itemLabel(r) },
  house: { label: "Haus", fmt: (r) => `<span class="pill">${esc(r.house_name)}</span>` },
  price: { label: "Preis (min)", num: true, sort: "price", fmt: (r) => money(r.last_min) },
  median: { label: "Median", num: true, sort: "median", fmt: (r) => money(r.last_median) },
  avg7: { label: "Ø 7 T (min)", num: true, fmt: (r) => money(r.avg7_min) },
  avg30: { label: "Ø 30 T (min)", num: true, sort: "avg30", fmt: (r) => money(r.avg30_min) },
  qty: { label: "Menge", num: true, sort: "qty", fmt: (r) => num(r.last_qty) },
  avgqty: { label: "Ø Menge 7 T", num: true, sort: "avg_qty", fmt: (r) => num(r.avg7_qty) },
  chg24: { label: "Δ 24 h", num: true, sort: "chg24", fmt: (r) => pct(r.chg24_min) },
  chg7d: { label: "Δ 7 T", num: true, sort: "chg7d", fmt: (r) => pct(r.chg7d_min) },
  vol: { label: "Volatilität", num: true, sort: "vol", title: "Variationskoeffizient des Mindestpreises (7 Tage)", fmt: (r) => r.cv7_min == null ? "–" : num(r.cv7_min * 100, 1) + " %" },
  value: { label: "Marktwert", num: true, sort: "value", title: "Mindestpreis × angebotene Menge", fmt: (r) => money(r.market_value, { short: true }) },
  spark: { label: "14 Tage", fmt: (r) => sparkline(r.spark) },
  updated: { label: "Stand", sort: "updated", fmt: (r) => `<span class="muted small">${dt(r.last_ts)}</span>` },
  cmp: { label: "", fmt: (r) => `<button class="btn small" data-cmp="${r.item_id}" title="Zum Vergleich hinzufügen">${state.compare.includes(r.item_id) ? "✓" : "+"} Vgl.</button>` },
};
document.addEventListener("click", (e) => {
  const b = e.target.closest("button[data-cmp]");
  if (!b) return;
  e.stopPropagation();
  toggleCompare(+b.dataset.cmp);
  b.textContent = (state.compare.includes(+b.dataset.cmp) ? "✓" : "+") + " Vgl.";
}, true);
function toggleCompare(id) {
  const i = state.compare.indexOf(id);
  if (i >= 0) state.compare.splice(i, 1);
  else {
    if (state.compare.length >= 8) { toast("Maximal 8 Items im Vergleich."); return; }
    state.compare.push(id);
    toast("Zum Vergleich hinzugefügt.", 1500);
  }
  store.set("compare", state.compare);
  updateCompareBadge();
}
function updateCompareBadge() {
  const b = $("#cmpCount");
  b.hidden = !state.compare.length;
  b.textContent = state.compare.length;
}

// ============================================================= Übersicht
async function viewDashboard(r) {
  const d = await api("overview", { house: curHouse(r.params) });
  const k = d.kpis, h = d.house;
  const gran = h.granularity === "day" ? "Tageswerte" : "stündliche Snapshots";
  main.innerHTML = `
    <div class="pagehead"><h1>${esc(h.name)}</h1>
      <span class="pill">${esc((h.region || "").toUpperCase())}</span>${h.faction ? `<span class="pill">${esc(h.faction)}</span>` : ""}
      <span class="pill">Quelle: ${esc(h.source)}</span>
      <span class="sub">${num(d.snapshot_count)} ${gran} · letzter ${dt(d.last_ts)} (${ago(d.last_ts)})</span></div>
    <div class="kpis">
      <div class="kpi"><div class="l">Gehandelte Items</div><div class="v">${num(k.items)}</div></div>
      <div class="kpi"><div class="l">Marktwert (min × Menge)</div><div class="v">${money(k.market_value, { short: true })}</div></div>
      <div class="kpi"><div class="l">Angebotene Stück</div><div class="v">${compact(k.quantity)}</div></div>
      <div class="kpi"><div class="l">Auktionen</div><div class="v">${k.auctions == null ? "–" : compact(k.auctions)}</div></div>
      <div class="kpi"><div class="l">Ø Preisänderung 24 h</div><div class="v">${pct(k.avg_chg24)}</div></div>
      <div class="kpi"><div class="l">Ø Preisänderung 7 T</div><div class="v">${pct(k.avg_chg7d)}</div></div>
    </div>
    <div class="grid g32">
      <div class="card"><div class="cardhead"><h2>Marktwert im Zeitverlauf</h2><span class="right muted small">Summe Mindestpreis × Menge je Snapshot</span></div>
        <div class="chartbox md"><canvas id="mkt"></canvas></div></div>
      <div class="card flush"><h2>Kategorien</h2>${table([
        { label: "Kategorie", fmt: (c) => esc(c.class_name) },
        { label: "Items", num: true, fmt: (c) => num(c.items) },
        { label: "Marktwert", num: true, fmt: (c) => money(c.market_value, { short: true }) },
        { label: "Δ 24 h", num: true, fmt: (c) => pct(c.chg24) },
      ], d.categories.slice(0, 9), { rowHref: (c) => `/categories?class=${c.class_id}&house=${h.id}` })}</div>
    </div>
    <div class="grid g3 mt">
      <div class="card flush"><div class="cardhead" style="padding:14px 16px 0"><h2>Gewinner</h2><a class="right small" href="#/movers?house=${h.id}">alle ›</a></div>${moversMini(d.movers.gainers)}</div>
      <div class="card flush"><div class="cardhead" style="padding:14px 16px 0"><h2>Verlierer</h2><a class="right small" href="#/movers?house=${h.id}">alle ›</a></div>${moversMini(d.movers.losers)}</div>
      <div class="card flush"><div class="cardhead" style="padding:14px 16px 0"><h2>Deals (≥ 15 % unter Ø 7 T)</h2><a class="right small" href="#/deals?house=${h.id}">alle ›</a></div>
        ${table([{ ...COL.item, sort: null }, { label: "Preis", num: true, fmt: (x) => money(x.last_min) }, { label: "Rabatt", num: true, fmt: (x) => `<span class="chg up">−${num(x.discount, 0)} %</span>` }],
          d.deals.deals, { rowHref: (x) => `/item/${x.item_id}?house=${x.house_id}`, empty: "Aktuell keine Deals." })}</div>
    </div>
    <div class="card flush mt"><h2>Volatilste Items (7 Tage)</h2>${table([COL.item, COL.price, COL.chg24, COL.chg7d, COL.vol, COL.qty], d.most_volatile, { rowHref: (x) => `/item/${x.item_id}?house=${x.house_id}` })}</div>`;
  const s = d.market_series;
  if (s.length > 1) {
    timeLineChart($("#mkt"), [line("Marktwert", s.map((p) => ({ x: p.ts, y: p.total_value })), "--s1", { fill: false })],
      { span: s[s.length - 1].ts - s[0].ts, yFmt: moneyText, minY: 0 });
  } else $("#mkt").parentElement.innerHTML = '<div class="empty">Zu wenige Snapshots für einen Verlauf.</div>';
}
function moversMini(rows) {
  return table([{ ...COL.item, sort: null }, { label: "Preis", num: true, fmt: (x) => money(x.last_min) }, { label: "Δ", num: true, fmt: (x) => pct(x.chg24_min ?? x.chg7d_min) }],
    rows, { rowHref: (x) => `/item/${x.item_id}?house=${x.house_id}`, empty: "Keine Änderungen." });
}

// ================================================================= Suche
let classCache = {};
async function getClasses(house) {
  if (!classCache[house]) classCache[house] = (await api("classes", { house })).classes;
  return classCache[house];
}
async function viewSearch(r) {
  const p = { sort: "value", dir: "desc", page: 1, ...r.params };
  if (!("house" in r.params) && !p.region && !p.faction) p.house = state.house;
  const classes = await getClasses(p.house || state.house);
  const subs = classes.find((c) => String(c.class_id) === String(p.class))?.subclasses || [];
  const quals = (p.quality || "").split(",").filter(Boolean);
  const regions = [...new Set(state.houses.map((h) => h.region).filter(Boolean))];
  const factions = [...new Set(state.houses.map((h) => h.faction).filter(Boolean))];
  const gold = (v) => (v ? v / 10000 : "");
  main.innerHTML = `
    <div class="pagehead"><h1>Item-Suche</h1><span class="sub" id="sumline"></span></div>
    <form class="card filters" id="ff">
      <label class="f"><span>Name / ID</span><input type="search" name="q" value="${esc(p.q || "")}" placeholder="Deutsch oder Englisch, z. B. Silberblatt, Silverleaf, 765"></label>
      <label class="f"><span>Klasse</span><select name="class"><option value="">Alle</option>${classes.map((c) => `<option value="${c.class_id}" ${String(p.class) === String(c.class_id) ? "selected" : ""}>${esc(c.class_name)} (${c.items})</option>`).join("")}</select></label>
      <label class="f"><span>Unterklasse</span><select name="subclass" ${subs.length ? "" : "disabled"}><option value="">Alle</option>${subs.map((s) => `<option value="${s.subclass_id}" ${String(p.subclass) === String(s.subclass_id) ? "selected" : ""}>${esc(s.subclass_name)} (${s.items})</option>`).join("")}</select></label>
      <div class="f"><span>Qualität</span><div class="qual">${[0, 1, 2, 3, 4, 5].map((q) => `<label class="q${q}" title="${QUAL[q]}"><input type="checkbox" name="quality" value="${q}" ${quals.includes(String(q)) ? "checked" : ""}><span>${QUAL[q].slice(0, 4)}</span></label>`).join("")}</div></div>
      <div class="f"><span>Preis in Gold</span><div class="range"><input type="number" step="any" min="0" name="min_price" value="${gold(p.min_price)}" placeholder="von">–<input type="number" step="any" min="0" name="max_price" value="${gold(p.max_price)}" placeholder="bis"></div></div>
      <div class="f"><span>Menge (aktuell)</span><div class="range"><input type="number" min="0" name="min_qty" value="${esc(p.min_qty || "")}" placeholder="von">–<input type="number" min="0" name="max_qty" value="${esc(p.max_qty || "")}" placeholder="bis"></div></div>
      <label class="f"><span>Ø Menge 7 T ≥</span><input type="number" min="0" name="min_avg_qty" value="${esc(p.min_avg_qty || "")}"></label>
      <label class="f"><span>Region</span><select name="region"><option value="">Alle</option>${regions.map((x) => `<option ${p.region === x ? "selected" : ""}>${x}</option>`).join("")}</select></label>
      <label class="f"><span>Fraktion</span><select name="faction"><option value="">Alle</option>${factions.map((x) => `<option ${p.faction === x ? "selected" : ""}>${x}</option>`).join("")}</select></label>
      <label class="f"><span>Auktionshaus</span><select name="house"><option value="">Alle passenden</option>${state.houses.map((h) => `<option value="${h.id}" ${String(p.house) === String(h.id) ? "selected" : ""}>${esc(h.name)}</option>`).join("")}</select></label>
      <label class="f"><span>Typ</span><select name="commodity"><option value="">Alle</option><option value="1" ${p.commodity === "1" ? "selected" : ""}>Handelswaren</option><option value="0" ${p.commodity === "0" ? "selected" : ""}>Einzelitems</option></select></label>
      <div class="f"><span>&nbsp;</span><div style="display:flex;gap:6px"><button class="btn primary">Filtern</button><button class="btn" type="button" id="reset">Zurücksetzen</button></div></div>
    </form>
    <div class="card flush" id="res"><div class="loading">Suche …</div></div>`;
  const form = $("#ff");
  const submit = () => {
    const fd = new FormData(form);
    const np = { sort: p.sort, dir: p.dir, page: 1 };
    for (const [k, v] of fd.entries()) if (v !== "" && k !== "quality") np[k] = v;
    const qs = fd.getAll("quality"); if (qs.length) np.quality = qs.join(",");
    if (np.min_price) np.min_price = Math.round(np.min_price * 10000);
    if (np.max_price) np.max_price = Math.round(np.max_price * 10000);
    if (!np.house) np.house = "";
    go("/search", np, true);
  };
  form.addEventListener("submit", (e) => { e.preventDefault(); submit(); });
  const qWrap = form.q.parentElement; qWrap.style.position = "relative";
  qWrap.insertAdjacentHTML("beforeend", '<div class="ac" id="qRes" hidden></div>');
  attachAutocomplete(form.q, $("#qRes"), p.house || state.house, (it) => (location.hash = `/item/${it.item_id}?house=${it.house_id}`), { showAll: false });
  form.class.addEventListener("change", () => { form.subclass.value = ""; submit(); });
  $$("select, input[type=checkbox]", form).forEach((el) => el.name !== "class" && el.addEventListener("change", submit));
  $("#reset").onclick = () => go("/search", {}, true);
  const res = await api("search", { ...p, spark: 1, page_size: 50 });
  $("#sumline").textContent = `${num(res.total)} Treffer`;
  $("#res").innerHTML = table([COL.item, COL.house, COL.price, COL.median, COL.avg30, COL.qty, COL.chg24, COL.chg7d, COL.vol, COL.value, COL.spark, COL.cmp], res.items, {
    sort: p.sort, dir: p.dir, onSort: (s, d) => setParams({ sort: s, dir: d, page: 1 }),
    rowHref: (x) => `/item/${x.item_id}?house=${x.house_id}`, empty: "Keine Items passen zu den Filtern.",
  }) + pager(res.total, res.page, res.page_size, (pg) => setParams({ page: pg }));
}

// ============================================================ Kategorien
async function viewCategories(r) {
  const house = curHouse(r.params);
  const classes = await getClasses(house);
  const p = r.params;
  const cls = classes.find((c) => String(c.class_id) === String(p.class));
  const sub = cls?.subclasses.find((s) => String(s.subclass_id) === String(p.subclass));
  const crumbs = [`<a href="#/categories?house=${house}">Alle Kategorien</a>`];
  if (cls) crumbs.push(`<a href="#/categories?house=${house}&class=${cls.class_id}">${esc(cls.class_name)}</a>`);
  if (sub) crumbs.push(esc(sub.subclass_name));
  const maxv = (arr) => Math.max(1, ...arr.map((x) => x.market_value || 0));
  const card = (x, href, name) => `<a class="cat" href="${href}"><div class="nm">${esc(name)}</div>
      <div class="row"><span>${num(x.items)} Items</span><span>${money(x.market_value, { short: true })}</span></div>
      <div class="row"><span>Δ 24 h ${pct(x.chg24)}</span><span>Δ 7 T ${pct(x.chg7d)}</span></div>
      <div class="bar" style="width:${(100 * (x.market_value || 0)) / card.max}%"></div></a>`;
  let body = "";
  if (!cls) {
    card.max = maxv(classes);
    body = `<div class="catgrid">${classes.map((c) => card(c, `#/categories?house=${house}&class=${c.class_id}`, c.class_name)).join("")}</div>`;
  } else if (!sub && cls.subclasses.length > 1) {
    card.max = maxv(cls.subclasses);
    body = `<div class="catgrid">${cls.subclasses.map((s) => card(s, `#/categories?house=${house}&class=${cls.class_id}&subclass=${s.subclass_id ?? -1}`, s.subclass_name)).join("")}</div>
      <div class="card flush mt" id="catitems"><div class="loading">Lade Items …</div></div>`;
  } else {
    body = `<div class="card flush" id="catitems"><div class="loading">Lade Items …</div></div>`;
  }
  main.innerHTML = `<div class="crumbs">${crumbs.join(" › ")}</div>
    <div class="pagehead"><h1>${esc(sub?.subclass_name || cls?.class_name || "Kategorien")}</h1>
      <span class="sub">Drilldown: Kategorie → Unterkategorie → Item → Snapshot</span></div>${body}`;
  if (cls) {
    const sp = { house, class: cls.class_id, sort: p.sort || "value", dir: p.dir || "desc", page: p.page || 1, page_size: 50, spark: 1 };
    if (sub) sp.subclass = sub.subclass_id ?? -1;
    if (cls.class_id === -1) sp.class = -1;
    const res = await api("search", sp);
    $("#catitems").innerHTML = `<h2>Items</h2>` + table([COL.item, COL.price, COL.median, COL.avg30, COL.qty, COL.chg24, COL.chg7d, COL.vol, COL.value, COL.spark, COL.cmp], res.items, {
      sort: sp.sort, dir: sp.dir, onSort: (s, d) => setParams({ sort: s, dir: d, page: 1 }),
      rowHref: (x) => `/item/${x.item_id}?house=${x.house_id}`,
    }) + pager(res.total, res.page, res.page_size, (pg) => setParams({ page: pg }));
  }
}

// ================================================================= Item
async function viewItem(r) {
  const id = +r.parts[1];
  const p = { range: "7d", ...r.params };
  const info = await api(`item/${id}`);
  const it = info.item;
  if (!info.houses.length) { main.innerHTML = `<div class="empty">Für dieses Item gibt es keine Preisdaten.</div>`; return; }
  const house = info.houses.find((h) => String(h.house_id) === String(p.house || state.house)) || info.houses[0];
  const hid = house.house_id;
  const daily = house.granularity === "day";
  const ranges = daily ? ["7d", "30d", "90d", "all"] : ["24h", "7d", "30d", "all"];
  if (!ranges.includes(p.range)) p.range = daily ? "30d" : "7d";
  const anaRange = p.range === "24h" ? "7d" : p.range;
  const metric = p.metric || (house.last_median != null ? "median" : "min");
  const shown = new Set((p.show || (house.last_median != null ? "min,median,mean" : "min,max")).split(","));
  const [hist, ana] = await Promise.all([
    api(`item/${id}/history`, { house: hid, range: p.range, max_points: 800 }),
    api(`item/${id}/analysis`, { house: hid, range: anaRange, metric, tz: TZ }),
  ]);
  const t = ana.trend || {}, v = ana.volatility || {};
  const inCmp = state.compare.includes(id);
  main.innerHTML = `
    <div class="crumbs"><a href="#/categories?house=${hid}">Kategorien</a> › <a href="#/categories?house=${hid}&class=${it.class_id ?? -1}">${esc(it.class_name || "Ohne Metadaten")}</a>${it.subclass_name ? ` › <a href="#/categories?house=${hid}&class=${it.class_id}&subclass=${it.subclass_id}">${esc(it.subclass_name)}</a>` : ""} › ${esc(it.name)}</div>
    <div class="pagehead itemhead">${icon(it, true)}
      <div><h1 class="q${it.quality ?? 1}">${esc(it.name)}</h1>
        <div class="meta">${it.name_en && it.name_en !== it.name ? `<span title="Englischer Name">EN: ${esc(it.name_en)}</span>` : ""}<span>ID ${id}</span>${it.quality_name ? `<span>${esc(it.quality_name)}</span>` : ""}${it.item_level ? `<span>Stufe ${it.item_level}</span>` : ""}
        ${it.is_commodity ? '<span class="pill">Handelsware</span>' : ""}${it.vendor_sell ? `<span>Händler: ${money(it.vendor_sell)}</span>` : ""}
        <a href="https://www.wowhead.com/forever/de/item=${id}" target="_blank" rel="noopener">Wowhead ↗</a></div></div>
      <div class="actions">
        <select id="hsel" title="Auktionshaus">${info.houses.map((h) => `<option value="${h.house_id}" ${h.house_id === hid ? "selected" : ""}>${esc(h.house_name)}</option>`).join("")}</select>
        <button class="btn" id="cmpBtn">${inCmp ? "✓ Im Vergleich" : "+ Vergleich"}</button>
        <a class="btn" href="#/compare?items=${[...new Set([...state.compare, id])].join(",")}&house=${hid}">Vergleichen ›</a>
      </div></div>
    <div class="kpis">
      <div class="kpi"><div class="l">Aktueller Mindestpreis</div><div class="v">${money(house.last_min)}</div><div class="d">${dt(house.last_ts)}</div></div>
      <div class="kpi"><div class="l">Median (aktuell)</div><div class="v">${money(house.last_median)}</div><div class="d">Menge ${num(house.last_qty)}${house.last_auctions ? ` · ${num(house.last_auctions)} Auktionen` : ""}</div></div>
      <div class="kpi"><div class="l">Ø 7 T / Ø 30 T (min)</div><div class="v">${money(house.avg7_min)}</div><div class="d">${money(house.avg30_min)}</div></div>
      <div class="kpi"><div class="l">30-T-Tief / -Hoch</div><div class="v">${money(house.low30_min)}</div><div class="d">${money(house.high30_min)}</div></div>
      <div class="kpi"><div class="l">Änderung 24 h / 7 T</div><div class="v">${pct(house.chg24_min)}</div><div class="d">${pct(house.chg7d_min)}</div></div>
      <div class="kpi" title="Lineare Regression auf ln(Preis), ${anaRange}"><div class="l">Trend (${esc(ana.metric)}, ${anaRange})</div><div class="v">${t.pct_per_day == null ? "–" : (t.pct_per_day > 0 ? "+" : "") + num(t.pct_per_day, 2) + " %/Tag"}</div><div class="d">${esc(t.direction || "")}${t.r2 != null ? ` · R² ${num(t.r2, 2)}` : ""}</div></div>
      <div class="kpi" title="Std.-Abw. der Log-Renditen, auf einen Tag skaliert; CV = Std/Mittel"><div class="l">Volatilität (${anaRange})</div><div class="v">${v.daily == null ? "–" : num(v.daily * 100, 1) + " %/Tag"}</div><div class="d">CV ${v.cv == null ? "–" : num(v.cv * 100, 1) + " %"}</div></div>
      <div class="kpi"><div class="l">Ausreißer (${anaRange})</div><div class="v">${num(ana.outliers.length)}</div><div class="d">${ana.points} Datenpunkte</div></div>
    </div>
    <div class="card">
      <div class="cardhead"><h2>Preisverlauf</h2>
        <div class="right">
          <div class="toggles" id="tog">${[["min", "Min", "--s1"], ["p10", "10 %-Perz.", "--s7"], ["median", "Median", "--s2"], ["mean", "Mittel", "--s3"], ["max", "Max", "--s4"]].map(([k, l, c]) =>
            `<label><input type="checkbox" value="${k}" ${shown.has(k) ? "checked" : ""}><span class="sw" style="background:var(${c})"></span>${l}</label>`).join("")}
            <label title="Ausreißer markieren"><input type="checkbox" value="out" ${p.out !== "0" ? "checked" : ""}>Ausreißer</label></div>
          <div class="seg" id="rng">${ranges.map((x) => `<button data-r="${x}" class="${x === p.range ? "on" : ""}">${x === "all" ? "Alles" : x.replace("d", " T").replace("h", " h")}</button>`).join("")}</div>
        </div></div>
      <div class="chartbox"><canvas id="pc"></canvas></div>
      <div class="muted small" style="margin-top:6px">${hist.raw_points} Datenpunkte${hist.points.length < hist.raw_points ? ` (verdichtet auf ${hist.points.length})` : ""} · Klick auf den Chart öffnet den Snapshot.</div>
      <h3 class="mt">Angebotsmenge</h3>
      <div class="chartbox sm"><canvas id="vc"></canvas></div>
    </div>
    <div class="grid g2 mt">
      <div class="card"><div class="cardhead"><h2>Tageszeit-Muster</h2><span class="right muted small">Abweichung vom 24-h-Median, Ortszeit</span></div>
        <div class="chartbox sm" id="hbox"><canvas id="hc"></canvas></div></div>
      <div class="card"><div class="cardhead"><h2>Wochentags-Muster</h2><span class="right muted small">Abweichung vom 7-Tage-Median</span></div>
        <div class="chartbox sm" id="wbox"><canvas id="wc"></canvas></div></div>
    </div>
    <div class="grid g2 mt">
      <div class="card flush"><h2>Häuservergleich</h2>${table([COL.house, COL.price, COL.median, COL.avg7, COL.qty, COL.chg24, COL.chg7d,
        { label: "vs. dieses Haus", num: true, fmt: (x) => x.house_id === hid ? '<span class="muted">—</span>' : pct(house.last_min ? (x.last_min / house.last_min - 1) * 100 : null) }],
        info.houses, { rowHref: (x) => `/item/${id}?house=${x.house_id}&range=${p.range}` })}</div>
      <div class="card flush"><h2>Ausreißer</h2>${table([
        { label: "Zeitpunkt", fmt: (o) => dt(o.ts) },
        { label: "Art", fmt: (o) => `<span class="${o.kind === "Spitze" ? "up" : "down"}">${o.kind === "Spitze" ? "▲" : "▼"} ${o.kind}</span>` },
        { label: "Wert", num: true, fmt: (o) => money(o.value) },
        { label: "Erwartet", num: true, fmt: (o) => money(o.expected) },
        { label: "Abw.", num: true, fmt: (o) => pct(o.deviation_pct, 0) }],
        ana.outliers.slice().reverse().slice(0, 12), { rowHref: (o) => `/snapshot/${hid}/${o.ts}?item=${id}`, empty: "Keine Ausreißer im Zeitraum." })}</div>
    </div>
    <div class="card flush mt" id="snaps"><div class="loading">Lade Snapshots …</div></div>`;

  // Interaktion
  $("#hsel").onchange = (e) => setParams({ house: e.target.value });
  $("#cmpBtn").onclick = (e) => { toggleCompare(id); e.target.textContent = state.compare.includes(id) ? "✓ Im Vergleich" : "+ Vergleich"; };
  $("#rng").onclick = (e) => { const b = e.target.closest("button[data-r]"); if (b) setParams({ range: b.dataset.r }); };
  $("#tog").onchange = () => {
    const sel = $$("#tog input:checked").map((x) => x.value);
    setParams({ show: sel.filter((x) => x !== "out").join(","), out: sel.includes("out") ? "" : "0" });
  };

  // Preis-Chart
  const pts = hist.points;
  const span = pts.length ? pts[pts.length - 1].ts - pts[0].ts : 86400;
  const ds = [];
  const defs = [["min", "Min", "--s1"], ["p10", "10 %-Perzentil", "--s7"], ["median", "Median", "--s2"], ["mean", "Mittel", "--s3"], ["max", "Max", "--s4"]];
  for (const [k, l, c] of defs) {
    if (!shown.has(k) || !pts.some((x) => x[k] != null)) continue;
    ds.push(line(l, pts.map((x) => ({ x: x.ts, y: x[k], ts: x.ts })), c, k === "max" ? { borderDash: [4, 3], borderWidth: 1.5 } : {}));
  }
  if (p.out !== "0" && ana.outliers.length) {
    const lo = pts.length ? pts[0].ts : 0;
    const o = ana.outliers.filter((x) => x.ts >= lo);
    ds.push({ type: "scatter", label: `Ausreißer (${ana.metric})`, data: o.map((x) => ({ x: x.ts, y: x.value, ts: x.ts })), pointRadius: 5, pointHoverRadius: 7,
      backgroundColor: css("--s8"), borderColor: css("--surface-1"), borderWidth: 2, pointStyle: "triangle" });
  }
  if (!ds.length) $("#pc").parentElement.innerHTML = '<div class="empty">Keine Preisdaten im Zeitraum.</div>';
  else timeLineChart($("#pc"), ds, { span, onClick: (pt) => (location.hash = `/snapshot/${hid}/${nearestSnap(pt.ts, pts)}?item=${id}`) });

  // Volumen-Chart (eigene Achse = eigener Chart, keine Doppelachse)
  makeChart($("#vc"), {
    type: "bar",
    data: { datasets: [{ label: "Menge", data: pts.map((x) => ({ x: x.ts, y: x.quantity })), backgroundColor: css("--s1") + "99", borderRadius: 2, barPercentage: 1, categoryPercentage: 1 }] },
    options: {
      responsive: true, maintainAspectRatio: false, parsing: false,
      scales: { x: { type: "linear", ticks: { callback: timeTick(span), maxRotation: 0 }, grid: { display: false } }, y: { ticks: { callback: (v) => compact(v) } } },
      plugins: { legend: { display: false }, tooltip: { callbacks: { title: (i) => dt(i[0].parsed.x), label: (c) => ` Menge: ${num(c.parsed.y)}` } } },
    },
  });

  // Saisonalität
  const se = ana.seasonality || {};
  if (se.hourly) divergingBars($("#hc"), se.hourly.map((x) => x.hour + " h"), se.hourly.map((x) => x.pct ?? 0), "Tageszeit");
  else $("#hbox").innerHTML = `<div class="empty">Benötigt stündliche Daten über mindestens 2 Tage.</div>`;
  if (se.weekday) divergingBars($("#wc"), se.weekday.map((x) => x.day), se.weekday.map((x) => x.pct ?? 0), "Wochentag");
  else $("#wbox").innerHTML = `<div class="empty">Benötigt mindestens 14 Tage Historie (Zeitraum „30 T“ oder „Alles“ wählen).</div>`;

  // Snapshot-Liste
  const loadSnaps = async (page) => {
    const s = await api(`item/${id}/snapshots`, { house: hid, page, page_size: 25 });
    $("#snaps").innerHTML = `<h2>Einzelne Snapshots</h2>` + table([
      { label: "Zeitpunkt", fmt: (x) => x.daily ? `${dt(x.ts, false)} <span class="pill" title="Mittel aus ${x.n} Snapshots">Tagesmittel</span>` : dt(x.ts) },
      { label: "Min", num: true, fmt: (x) => money(x.min) }, { label: "10 %", num: true, fmt: (x) => money(x.p10) },
      { label: "Median", num: true, fmt: (x) => money(x.median) }, { label: "Mittel", num: true, fmt: (x) => money(x.mean) },
      { label: "Max", num: true, fmt: (x) => money(x.max) }, { label: "Menge", num: true, fmt: (x) => num(x.quantity) },
      { label: "Auktionen", num: true, fmt: (x) => num(x.auctions) }, { label: "Δ Min", num: true, fmt: (x) => pct(x.chg_min) },
      { label: "Staffel", fmt: (x) => (x.has_ladder ? '<span class="pill">vorhanden</span>' : "") },
    ], s.rows, { rowHref: (x) => (x.daily ? null : `/snapshot/${hid}/${x.ts}?item=${id}`) }) + pager(s.total, s.page, s.page_size, loadSnaps);
  };
  loadSnaps(1);
}
function nearestSnap(ts, pts) {
  return ts; // Punkte tragen bereits den Snapshot-Zeitstempel (bei Verdichtung: mittlerer Snapshot)
}

// ============================================================== Snapshot
async function viewSnapshot(r) {
  const hid = +r.parts[1], ts = +r.parts[2];
  const p = { sort: "value", dir: "desc", page: 1, ...r.params };
  const s = await api(`snapshot/${hid}/${ts}`, { ...p, page_size: 50 });
  const f = s.focus;
  const q = s.house.granularity === "day";
  main.innerHTML = `
    <div class="crumbs"><a href="#/?house=${hid}">${esc(s.house.name)}</a> › Snapshots › ${dt(ts)}${f ? ` › <a href="#/item/${f.item.item_id}?house=${hid}">${esc(f.item.name)}</a>` : ""}</div>
    <div class="pagehead"><h1>Snapshot ${dt(ts)}</h1><span class="pill">${esc(s.house.name)}</span><span class="pill">${q ? "Tageswert" : "Stunden-Snapshot"}</span>
      <div class="actions">
        ${s.prev_ts ? `<a class="btn" href="#/snapshot/${hid}/${s.prev_ts}${f ? "?item=" + f.item.item_id : ""}">‹ Vorheriger</a>` : ""}
        ${s.next_ts ? `<a class="btn" href="#/snapshot/${hid}/${s.next_ts}${f ? "?item=" + f.item.item_id : ""}">Nächster ›</a>` : ""}
      </div></div>
    <div class="kpis">
      <div class="kpi"><div class="l">Items</div><div class="v">${num(s.snapshot.item_count)}</div></div>
      <div class="kpi"><div class="l">Auktionen</div><div class="v">${num(s.snapshot.auction_count)}</div></div>
      <div class="kpi"><div class="l">Marktwert</div><div class="v">${money(s.snapshot.total_value, { short: true })}</div></div>
      <div class="kpi"><div class="l">Quelle</div><div class="v" style="font-size:16px">${esc(s.snapshot.source)}</div><div class="d">importiert ${dt(s.snapshot.imported_at)}</div></div>
    </div>
    ${f ? `<div class="grid g32">
      <div class="card"><div class="cardhead"><h2>${itemLabel({ ...f.item, house_id: hid })} – Preisstaffel</h2></div>
        ${f.ladder ? `<div class="chartbox md"><canvas id="lc"></canvas></div><p class="muted small">Angebotene Menge je Preisstufe (günstigste ${f.ladder.length} Stufen). Tooltip zeigt kumulierte Menge und Gesamtkosten bis zu dieser Stufe.</p>` :
          `<div class="note">Für diesen Snapshot ist keine Preisstaffel gespeichert (nur für die jüngsten Snapshots der Live-Quelle bzw. nicht bei Auctionator-Tageswerten).</div>`}</div>
      <div class="card"><h2>Kennzahlen</h2>${f.stats ? `<table class="t"><tbody>
        ${[["Min", f.stats.min], ["10 %-Perzentil", f.stats.p10], ["Median", f.stats.median], ["Mittel", f.stats.mean], ["Max", f.stats.max]].map(([l, v]) => `<tr class="nohover"><td>${l}</td><td class="n">${money(v)}</td></tr>`).join("")}
        <tr class="nohover"><td>Menge</td><td class="n">${num(f.stats.quantity)}</td></tr>
        <tr class="nohover"><td>Auktionen</td><td class="n">${num(f.stats.auctions)}</td></tr>
        ${f.ladder ? `<tr class="nohover"><td>Kosten für 20 Stück</td><td class="n">${money(buyCost(f.ladder, 20))}</td></tr><tr class="nohover"><td>Kosten für 100 Stück</td><td class="n">${money(buyCost(f.ladder, 100))}</td></tr>` : ""}
        </tbody></table>` : '<div class="empty">Item in diesem Snapshot nicht gelistet.</div>'}</div>
    </div>` : ""}
    <div class="card flush mt">
      <div class="cardhead" style="padding:14px 16px 0"><h2>Alle Items in diesem Snapshot</h2>
        <form class="right" id="sf"><input type="search" name="q" value="${esc(p.q || "")}" placeholder="Filtern …"></form></div>
      ${table([
        { label: "Item", sort: "name", cls: "itemcell", fmt: (x) => itemLabel({ ...x, house_id: hid }, false) },
        { label: "Min", num: true, sort: "price", fmt: (x) => money(x.min) },
        { label: "Median", num: true, fmt: (x) => money(x.median) },
        { label: "Menge", num: true, sort: "qty", fmt: (x) => num(x.quantity) },
        { label: "Auktionen", num: true, sort: "auctions", fmt: (x) => num(x.auctions) },
        { label: "Δ ggü. vorher", num: true, sort: "chg", fmt: (x) => pct(x.chg) },
        { label: "Marktwert", num: true, sort: "value", fmt: (x) => money(x.value, { short: true }) },
      ], s.items, { sort: p.sort, dir: p.dir, onSort: (so, d) => setParams({ sort: so, dir: d, page: 1 }), rowHref: (x) => `/snapshot/${hid}/${ts}?item=${x.item_id}` })}
      ${pager(s.total, s.page, s.page_size, (pg) => setParams({ page: pg }))}</div>`;
  $("#sf").onsubmit = (e) => { e.preventDefault(); setParams({ q: e.target.q.value, page: 1 }); };
  if (f?.ladder) {
    let cum = 0;
    const cumv = f.ladder.map(([, qn]) => (cum += qn));
    makeChart($("#lc"), {
      data: {
        labels: f.ladder.map(([pr]) => moneyText(pr)),
        datasets: [
          { type: "bar", label: "Menge", data: f.ladder.map(([, qn]) => qn), backgroundColor: css("--s1"), borderRadius: 3, maxBarThickness: 22 },
        ],
      },
      options: { responsive: true, maintainAspectRatio: false, scales: { x: { ticks: { maxRotation: 50, autoSkip: true }, grid: { display: false } }, y: { ticks: { callback: (v) => compact(v) }, title: { display: true, text: "Stück" } } },
        plugins: { legend: { display: false }, tooltip: { callbacks: {
          label: (c) => ` ${num(c.parsed.y)} Stück zu ${moneyText(f.ladder[c.dataIndex][0])}`,
          afterLabel: (c) => ` kumuliert: ${num(cumv[c.dataIndex])} Stück für ${moneyText(buyCost(f.ladder, cumv[c.dataIndex]))}` } } } },
    });
  }
}
function buyCost(ladder, n) {
  let left = n, cost = 0;
  for (const [pr, q] of ladder) { const take = Math.min(left, q); cost += take * pr; left -= take; if (!left) break; }
  return left ? null : cost;
}

// ============================================================== Vergleich
async function viewCompare(r) {
  const p = { range: "7d", metric: "min", mode: "idx", ...r.params };
  let ids = (p.items || state.compare.join(",")).split(",").filter(Boolean).map(Number);
  const house = curHouse(p);
  main.innerHTML = `
    <div class="pagehead"><h1>Vergleich</h1><span class="sub">Bis zu 8 Items in einem Chart</span></div>
    <div class="card">
      <div class="cardhead">
        <div class="chips" id="chips"></div>
        <div class="right" style="position:relative"><input type="search" id="addItem" placeholder="+ Item hinzufügen …" autocomplete="off"><div class="ac" id="addRes" hidden></div></div>
      </div>
      <div class="cardhead">
        <div class="seg" id="rng">${["24h", "7d", "30d", "all"].map((x) => `<button data-r="${x}" class="${x === p.range ? "on" : ""}">${x === "all" ? "Alles" : x.replace("d", " T").replace("h", " h")}</button>`).join("")}</div>
        <div class="seg" id="met">${[["min", "Min"], ["p10", "10 %"], ["median", "Median"], ["mean", "Mittel"], ["quantity", "Menge"]].map(([k, l]) => `<button data-m="${k}" class="${k === p.metric ? "on" : ""}">${l}</button>`).join("")}</div>
        <div class="seg" id="mode"><button data-o="idx" class="${p.mode === "idx" ? "on" : ""}" title="Alle Reihen auf 100 = erster Wert">Index (100)</button><button data-o="abs" class="${p.mode === "abs" ? "on" : ""}">Absolut</button></div>
        <select id="hs" class="right">${state.houses.map((h) => `<option value="${h.id}" ${String(h.id) === String(house) ? "selected" : ""}>${esc(h.name)}</option>`).join("")}</select>
      </div>
      <div class="chartbox" id="cbox"><canvas id="cc"></canvas></div>
    </div>
    <div class="card flush mt" id="ctab"></div>
    <div class="card mt" id="corr"></div>`;
  $("#rng").onclick = (e) => { const b = e.target.closest("button"); if (b) setParams({ range: b.dataset.r }); };
  $("#met").onclick = (e) => { const b = e.target.closest("button"); if (b) setParams({ metric: b.dataset.m }); };
  $("#mode").onclick = (e) => { const b = e.target.closest("button"); if (b) setParams({ mode: b.dataset.o }); };
  $("#hs").onchange = (e) => setParams({ house: e.target.value });
  const setIds = (n) => { state.compare = n; store.set("compare", n); updateCompareBadge(); setParams({ items: n.join(",") }); };
  attachAutocomplete($("#addItem"), $("#addRes"), house, (it) => { if (!ids.includes(it.item_id) && ids.length < 8) setIds([...ids, it.item_id]); }, { pickFirstOnEnter: true, showAll: false });
  if (!ids.length) {
    $("#chips").innerHTML = '<span class="muted">Noch keine Items gewählt – über das Suchfeld rechts oder „+ Vgl.“ in Tabellen hinzufügen.</span>';
    $("#cbox").innerHTML = '<div class="empty">Kein Item ausgewählt.</div>';
    return;
  }
  const d = await api("compare", { items: ids.join(","), house, range: p.range, metric: p.metric, max_points: 500 });
  $("#chips").innerHTML = d.series.map((s, i) => `<span class="chip"><span class="dot" style="background:var(${SERIES[i]})"></span>
     <a class="q${s.quality ?? 1}" href="#/item/${s.item_id}?house=${house}">${esc(s.name)}</a><button data-rm="${s.item_id}" title="Entfernen">×</button></span>`).join("") +
    `<button class="btn small" id="clr">Alle entfernen</button>`;
  $("#chips").onclick = (e) => {
    const b = e.target.closest("button[data-rm]");
    if (b) setIds(ids.filter((x) => x !== +b.dataset.rm));
    if (e.target.id === "clr") setIds([]);
  };
  const isQty = p.metric === "quantity";
  const idx = p.mode === "idx";
  const ds = d.series.map((s, i) => {
    const first = s.points.find((x) => x.v)?.v;
    return line(s.name, s.points.map((x) => ({ x: x.ts, y: x.v == null ? null : idx ? (first ? (x.v / first) * 100 : null) : x.v })), SERIES[i]);
  });
  const allPts = d.series.flatMap((s) => s.points);
  const span = allPts.length ? Math.max(...allPts.map((x) => x.ts)) - Math.min(...allPts.map((x) => x.ts)) : 86400;
  timeLineChart($("#cc"), ds, { span, yFmt: idx ? (v) => num(v, 0) : isQty ? (v) => compact(v) : moneyText, yTitle: idx ? "Index (Start = 100)" : "" });
  const m = (s) => s.summary || {};
  $("#ctab").innerHTML = `<h2>Kennzahlen</h2>` + table([
    { label: "", fmt: (s) => `<span class="sw" style="background:var(${SERIES[d.series.indexOf(s)]});width:18px"></span>` },
    { label: "Item", cls: "itemcell", fmt: (s) => itemLabel({ ...s, house_id: house }) },
    { label: "Preis (min)", num: true, fmt: (s) => money(m(s).last_min) },
    { label: "Median", num: true, fmt: (s) => money(m(s).last_median) },
    { label: "Ø 7 T", num: true, fmt: (s) => money(m(s).avg7_min) },
    { label: "Δ 24 h", num: true, fmt: (s) => pct(m(s).chg24_min) },
    { label: "Δ Zeitraum", num: true, fmt: (s) => pct(s.describe.change_pct) },
    { label: "Trend", num: true, fmt: (s) => s.trend.pct_per_day == null ? "–" : `${s.trend.pct_per_day > 0 ? "+" : ""}${num(s.trend.pct_per_day, 2)} %/T` },
    { label: "Volatilität", num: true, fmt: (s) => s.volatility.daily == null ? "–" : num(s.volatility.daily * 100, 1) + " %/T" },
    { label: "Menge", num: true, fmt: (s) => num(m(s).last_qty) },
  ], d.series, { rowHref: (s) => `/item/${s.item_id}?house=${house}` });
  if (d.series.length > 1) {
    const cell = (v) => {
      if (v == null) return '<td class="muted">–</td>';
      const c = v >= 0 ? "var(--div-pos)" : "var(--div-neg)";
      return `<td style="background:color-mix(in srgb, ${c} ${Math.round(Math.abs(v) * 70)}%, var(--div-mid))">${num(v, 2)}</td>`;
    };
    $("#corr").innerHTML = `<div class="cardhead"><h2>Korrelation der Preisänderungen</h2><span class="right muted small">Pearson auf Log-Renditen · +1 gleichläufig, −1 gegenläufig</span></div>
      <div class="tablewrap"><table class="t matrix"><thead><tr><th></th>${d.series.map((s) => `<th>${esc(s.name)}</th>`).join("")}</tr></thead>
      <tbody>${d.correlation.map((row, i) => `<tr class="nohover"><th>${esc(d.series[i].name)}</th>${row.map(cell).join("")}</tr>`).join("")}</tbody></table></div>`;
  } else $("#corr").hidden = true;
}

// ================================================================ Movers
async function viewMovers(r) {
  const house = curHouse(r.params);
  const h = houseById(house);
  const p = { window: "24h", metric: "min", min_qty: "", ...r.params };
  const classes = await getClasses(house);
  main.innerHTML = `
    <div class="pagehead"><h1>Top-Movers</h1><span class="sub">Größte Preisänderungen in ${esc(h?.name || "")}</span></div>
    <div class="card filters">
      <div class="f"><span>Zeitfenster</span><div class="seg" id="win">${["24h", "7d"].map((x) => `<button data-w="${x}" class="${p.window === x ? "on" : ""}">${x === "24h" ? "24 Stunden" : "7 Tage"}</button>`).join("")}</div></div>
      <div class="f"><span>Kennzahl</span><div class="seg" id="met">${[["min", "Mindestpreis"], ["median", "Median"]].map(([k, l]) => `<button data-m="${k}" class="${p.metric === k ? "on" : ""}">${l}</button>`).join("")}</div></div>
      <label class="f"><span>Ø Menge ≥ (Liquidität)</span><input type="number" min="0" id="mq" value="${esc(p.min_qty)}"></label>
      <label class="f"><span>Klasse</span><select id="cl"><option value="">Alle</option>${classes.map((c) => `<option value="${c.class_id}" ${String(p.class) === String(c.class_id) ? "selected" : ""}>${esc(c.class_name)}</option>`).join("")}</select></label>
    </div>
    <div class="grid g2"><div class="card flush" id="g"><div class="loading">…</div></div><div class="card flush" id="l"><div class="loading">…</div></div></div>`;
  $("#win").onclick = (e) => { const b = e.target.closest("button"); if (b) setParams({ window: b.dataset.w }); };
  $("#met").onclick = (e) => { const b = e.target.closest("button"); if (b) setParams({ metric: b.dataset.m }); };
  $("#mq").onchange = (e) => setParams({ min_qty: e.target.value });
  $("#cl").onchange = (e) => setParams({ class: e.target.value });
  const d = await api("movers", { ...p, house, limit: 30 });
  const col = d.column;
  const mx = Math.max(1, ...[...d.gainers, ...d.losers].map((x) => Math.abs(x[col])));
  const cols = (sign) => [COL.item,
    { label: "Vorher", num: true, fmt: (x) => money(x[(d.window === "24h" ? "prev24_" : "prev7d_") + d.metric]) },
    { label: "Jetzt", num: true, fmt: (x) => money(x["last_" + d.metric]) },
    { label: "Änderung", num: true, fmt: (x) => pct(x[col]) },
    { label: "", cls: "barcell", fmt: (x) => `<div class="bar" style="width:${(100 * Math.abs(x[col])) / mx}%;background:var(${sign > 0 ? "--up" : "--down"})"></div>` },
    COL.qty, COL.cmp];
  $("#g").innerHTML = `<h2>▲ Gewinner</h2>` + table(cols(1), d.gainers, { rowHref: (x) => `/item/${x.item_id}?house=${x.house_id}`, empty: "Keine steigenden Preise." });
  $("#l").innerHTML = `<h2>▼ Verlierer</h2>` + table(cols(-1), d.losers, { rowHref: (x) => `/item/${x.item_id}?house=${x.house_id}`, empty: "Keine fallenden Preise." });
}

// ================================================================= Deals
async function viewDeals(r) {
  const house = curHouse(r.params);
  const p = { threshold: "20", basis: "avg7", ref: "min", min_qty: "", ...r.params };
  const classes = await getClasses(house);
  main.innerHTML = `
    <div class="pagehead"><h1>Deals</h1><span class="sub">Aktueller Mindestpreis deutlich unter dem Durchschnitt</span></div>
    <div class="card filters">
      <label class="f"><span>Mindestens … % unter Ø</span><input type="number" min="1" max="95" id="th" value="${esc(p.threshold)}"></label>
      <div class="f"><span>Vergleichsbasis</span><div class="seg" id="ba">${[["avg7", "Ø 7 Tage"], ["avg30", "Ø 30 Tage"]].map(([k, l]) => `<button data-b="${k}" class="${p.basis === k ? "on" : ""}">${l}</button>`).join("")}</div></div>
      <div class="f"><span>Referenzpreis</span><div class="seg" id="rf">${[["min", "Ø Mindestpreis"], ["median", "Ø Median"]].map(([k, l]) => `<button data-x="${k}" class="${p.ref === k ? "on" : ""}">${l}</button>`).join("")}</div></div>
      <label class="f"><span>Ø Menge ≥</span><input type="number" min="0" id="mq" value="${esc(p.min_qty)}"></label>
      <label class="f"><span>Klasse</span><select id="cl"><option value="">Alle</option>${classes.map((c) => `<option value="${c.class_id}" ${String(p.class) === String(c.class_id) ? "selected" : ""}>${esc(c.class_name)}</option>`).join("")}</select></label>
    </div>
    <div class="card flush" id="dl"><div class="loading">…</div></div>`;
  $("#th").onchange = (e) => setParams({ threshold: e.target.value });
  $("#mq").onchange = (e) => setParams({ min_qty: e.target.value });
  $("#cl").onchange = (e) => setParams({ class: e.target.value });
  $("#ba").onclick = (e) => { const b = e.target.closest("button"); if (b) setParams({ basis: b.dataset.b }); };
  $("#rf").onclick = (e) => { const b = e.target.closest("button"); if (b) setParams({ ref: b.dataset.x }); };
  const d = await api("deals", { ...p, house, limit: 200 });
  $("#dl").innerHTML = `<h2>${num(d.deals.length)} Deals</h2>` + table([COL.item,
    { label: "Jetzt", num: true, fmt: (x) => money(x.last_min) },
    { label: "Referenz", num: true, fmt: (x) => money(x.ref_price) },
    { label: "Rabatt", num: true, fmt: (x) => `<span class="chg up">−${num(x.discount, 1)} %</span>` },
    { label: "Ersparnis/Stück", num: true, fmt: (x) => money(x.saving) },
    { label: "Menge", num: true, fmt: (x) => num(x.last_qty) },
    { label: "Ø Menge 7 T", num: true, fmt: (x) => num(x.avg7_qty) },
    { label: "30-T-Tief", num: true, fmt: (x) => money(x.low30_min) },
    { label: "Stand", fmt: (x) => `<span class="muted small">${dt(x.last_ts)}</span>` }, COL.cmp],
    d.deals, { rowHref: (x) => `/item/${x.item_id}?house=${x.house_id}`, empty: "Keine Deals mit diesen Kriterien." });
}

// ================================================================== Daten

function dataFlowCard() {
  return `<div class="card mt"><h2>Wie kommen die Preise auf diese Seite?</h2>
    <ol class="flow">
      <li><b>Scan im Spiel:</b> Das Addon Auctionator merkt sich bei jedem Auktionshaus-Scan (und bei jeder Suche dort) pro Item den niedrigsten Preis und die angebotene Menge des Tages.</li>
      <li><b>Speichern:</b> WoW schreibt diese Daten erst beim <code>/reload</code>, Ausloggen oder Beenden in die Datei <code>Auctionator.lua</code>.</li>
      <li><b>Import:</b> <code>start.bat</code> bzw. <code>publish.bat</code> liest die Datei ein, berechnet Kennzahlen und lädt Item-Namen und Icons (Wowhead-Datenbank für Forever).</li>
      <li><b>Veröffentlichen:</b> <code>publish.bat</code> exportiert die Seite und lädt sie zu GitHub hoch; GitHub Pages stellt sie nach 1–2 Minuten bereit, Browser können bis zu 10 Minuten zwischenspeichern.</li>
    </ol>
    <div class="note"><b>Beispiel – eigene Auktion einstellen:</b> Sie erscheint erst, wenn danach ein Scan läuft, der sie erfasst (Auctionator speichert nur den <i>günstigsten</i> Preis und die Gesamtmenge pro Tag), dann <code>/reload</code> und <code>publish.bat</code>.
      Theoretisch also nach wenigen Minuten, praktisch beim nächsten Veröffentlichen. Es gibt dabei nur <b>einen Wert pro Tag</b>; ein erneuter Scan am selben Tag überschreibt ihn.</div>
    <p class="muted small">Mit einem Battle.net-API-Zugang sammelt das Tool stattdessen automatisch jede Stunde alle Auktionen aller Häuser (inkl. Median, Mittelwert und Preisstaffel) – unabhängig davon, ob jemand im Spiel scannt.</p>
  </div>`;
}
async function viewData() {
  const [st, hs, lg] = await Promise.all([api("status"), api("houses"), api("import/log")]);
  const housesTable = () => table([
      { label: "ID", fmt: (h) => h.id }, { label: "Name", fmt: (h) => esc(h.name) }, { label: "Quelle", fmt: (h) => `<span class="pill">${esc(h.source)}</span>` },
      { label: "Region", fmt: (h) => esc(h.region) }, { label: "Fraktion", fmt: (h) => esc(h.faction || "–") },
      { label: "Auflösung", fmt: (h) => (h.granularity === "day" ? "Tag" : "Stunde") },
      { label: "Snapshots", num: true, fmt: (h) => num(h.snapshots) }, { label: "Items", num: true, fmt: (h) => num(h.items) },
      { label: "Von", fmt: (h) => dt(h.first_ts) }, { label: "Bis", fmt: (h) => dt(h.last_ts) }],
      hs.houses, { rowHref: (h) => `/?house=${h.id}` });
  if (st.static) {
    main.innerHTML = `
    <div class="pagehead"><h1>Datenquellen</h1><span class="sub">Statische Version · Datenstand ${dt(st.generated_at)} (${ago(st.generated_at)})</span></div>
    <div class="card"><h2>Über diese Seite</h2>
      <p>Dies ist ein statischer Export der Datenbank, gehostet auf GitHub Pages. Die Preise stammen aus Auktionshaus-Scans im Spiel (Addon Auctionator) und werden vom Betreiber regelmäßig neu veröffentlicht.
      Suche, Filter, Top-Movers, Deals und Vergleiche werden direkt im Browser berechnet. Historie: ${num(st.history_days)} Tage; Detaildaten für die jüngsten Snapshots.</p>
      <p>Eigene Instanz mit Import, Live-Sammler und vollständiger Historie: Code herunterladen${st.repo_url ? ` von <a href="${esc(st.repo_url)}" target="_blank" rel="noopener">${esc(st.repo_url.replace("https://", ""))}</a>` : ""} und <code>python run.py serve</code> starten (siehe README).</p>
    </div>
    ${dataFlowCard()}
    <div class="card flush mt"><h2>Auktionshäuser</h2>${housesTable()}</div>
    <div class="card flush mt"><h2>Import-Protokoll</h2>${table([
      { label: "Zeit", fmt: (x) => dt(x.ts) }, { label: "Quelle", fmt: (x) => esc(x.source) },
      { label: "Meldung", fmt: (x) => `<span class="small">${esc(x.message)}</span>` }], lg.log)}</div>`;
    return;
  }
  main.innerHTML = `
    <div class="pagehead"><h1>Datenquellen</h1><span class="sub">${num(st.counts.houses)} Häuser · ${num(st.counts.items)} Items · ${num(st.counts.snapshots)} Snapshots${st.db_size ? ` · DB ${num(st.db_size / 1048576, 1)} MB` : ""}</span></div>
    <div class="grid g2">
      <div class="card"><h2>Auctionator (Spielclient)</h2>
        <p class="muted">Liest die Preisdatenbank aus <code>WTF\\Account\\…\\SavedVariables\\Auctionator.lua</code> (Tageswerte: niedrigster/höchster Mindestpreis, Menge). Vorher im Spiel einen Scan im Auktionshaus durchführen und <b>/reload</b> oder ausloggen, damit WoW die Datei schreibt.</p>
        ${st.auctionator_paths.length ? `<ul>${st.auctionator_paths.map((x) => `<li><code>${esc(x.path)}</code> ${x.exists ? `<span class="muted small">(geändert ${dt(x.mtime)})</span>` : '<span class="down">nicht gefunden</span>'}</li>`).join("")}</ul>` : '<p class="note">Keine Auctionator.lua automatisch gefunden.</p>'}
        <p><button class="btn primary" id="autoImp" ${st.auctionator_paths.some((x) => x.exists) ? "" : "disabled"}>Jetzt importieren</button></p>
        <div class="dropzone" id="drop">Auctionator.lua hierher ziehen oder <label style="color:var(--link);cursor:pointer"><input type="file" id="file" accept=".lua,.bak" hidden>Datei wählen</label></div>
      </div>
      <div class="card"><h2>Battle.net API (live, stündlich)</h2>
        ${st.battlenet_configured ? `<p>Konfiguriert für Region(en): <b>${esc((st.battlenet_regions || []).join(", "))}</b></p>
          ${st.collector ? `<p class="muted small">Letzter Lauf: ${dt(st.collector.last_run)}${st.collector.next_run ? ` · nächster: ${dt(st.collector.next_run)}` : ""}${st.collector.running ? " · <b>läuft gerade …</b>" : ""}</p>
          ${st.collector.last_result ? `<p class="small">${esc(st.collector.last_result)}</p>` : ""}${st.collector.last_error ? `<p class="error small">${esc(st.collector.last_error)}</p>` : ""}` : ""}
          <button class="btn primary" id="collect">Snapshot jetzt holen</button>` :
          `<p class="note">Nicht konfiguriert. Unter <a href="https://develop.battle.net/access/clients" target="_blank" rel="noopener">develop.battle.net</a> einen API-Client anlegen und <code>client_id</code>/<code>client_secret</code> in <code>config.toml</code> eintragen (siehe README). Dann <code>python -m ahanalytics serve --collect</code>.</p>`}
        <p class="muted small">Endpunkte: <code>/data/wow/ruleset/{id}/auctions/{haus}</code> und <code>…/commodities</code>, Namespace <code>dynamic-forever-{region}</code>. Item-Namen und Icons kommen aus <code>static-forever-{region}</code>.</p>
      </div>
    </div>
    ${dataFlowCard()}
    <div class="card flush mt"><h2>Auktionshäuser</h2>${table([
      { label: "ID", fmt: (h) => h.id }, { label: "Name", fmt: (h) => esc(h.name) }, { label: "Quelle", fmt: (h) => `<span class="pill">${esc(h.source)}</span>` },
      { label: "Region", fmt: (h) => esc(h.region) }, { label: "Fraktion", fmt: (h) => esc(h.faction || "–") },
      { label: "Auflösung", fmt: (h) => (h.granularity === "day" ? "Tag" : "Stunde") },
      { label: "Snapshots", num: true, fmt: (h) => num(h.snapshots) }, { label: "Items", num: true, fmt: (h) => num(h.items) },
      { label: "Von", fmt: (h) => dt(h.first_ts) }, { label: "Bis", fmt: (h) => dt(h.last_ts) }],
      hs.houses, { rowHref: (h) => `/?house=${h.id}` })}</div>
    <div class="card flush mt"><h2>Import-Protokoll</h2>${table([
      { label: "Zeit", fmt: (x) => dt(x.ts) }, { label: "Quelle", fmt: (x) => esc(x.source) },
      { label: "Status", fmt: (x) => `<span class="${x.status === "ok" ? "up" : "down"}">${x.status === "ok" ? "✓ ok" : "✗ " + esc(x.status)}</span>` },
      { label: "Meldung", fmt: (x) => `<span class="small">${esc(x.message)}</span>` }], lg.log)}</div>`;
  const after = async (res) => { toast(res.summary || (res.results || []).map((x) => x.summary).join(" | ")); classCache = {}; await loadHouses(); viewData(); };
  $("#autoImp").onclick = async (e) => {
    e.target.disabled = true; e.target.textContent = "Importiere …";
    try { await after(await api("import/auctionator/auto", {}, { method: "POST" })); } catch (err) { toast("Fehler: " + err.message); e.target.disabled = false; }
  };
  const upload = async (file) => {
    toast(`Lade ${file.name} hoch …`);
    try { await after(await api("import/auctionator", { filename: file.name }, { method: "POST", body: await file.arrayBuffer() })); } catch (err) { toast("Fehler: " + err.message, 6000); }
  };
  $("#file").onchange = (e) => e.target.files[0] && upload(e.target.files[0]);
  const dz = $("#drop");
  dz.ondragover = (e) => { e.preventDefault(); dz.classList.add("over"); };
  dz.ondragleave = () => dz.classList.remove("over");
  dz.ondrop = (e) => { e.preventDefault(); dz.classList.remove("over"); if (e.dataTransfer.files[0]) upload(e.dataTransfer.files[0]); };
  const cb = $("#collect");
  if (cb) cb.onclick = async () => {
    try { const r = await api("collect", {}, { method: "POST" }); toast(r.started ? "Sammler gestartet – Ergebnis erscheint im Protokoll." : "Sammler läuft bereits."); } catch (err) { toast("Fehler: " + err.message); }
  };
}

// ======================================================== Autocomplete
/**
 * Suchvorschläge mit Icon, Qualitätsfarbe, Kategorie und Preis.
 * opts.pickFirstOnEnter: Enter ohne Auswahl übernimmt den ersten Treffer
 * (sonst wird das umgebende Formular normal abgeschickt).
 */
function attachAutocomplete(input, box, house, onPick, opts = {}) {
  let timer, items = [], total = 0, sel = -1, lastQ = "", seq = 0;
  const FOLD = { "ä": "a", "ö": "o", "ü": "u", "ß": "ss" };
  const fold = (t) => String(t || "").toLowerCase().replace(/[äöüß]/g, (c) => FOLD[c]);
  const hl = (name, q) => {
    if (!name) return "";
    // Position über die gefaltete Form suchen (gleiche Länge außer ß – dann ohne Markierung)
    const fn = fold(name), fq = fold(q);
    const i = fn.length === name.length ? fn.indexOf(fq) : name.toLowerCase().indexOf(q.toLowerCase());
    if (i < 0 || !q) return esc(name);
    return esc(name.slice(0, i)) + "<mark>" + esc(name.slice(i, i + q.length)) + "</mark>" + esc(name.slice(i + q.length));
  };
  const render = () => {
    if (!lastQ) { box.hidden = true; return; }
    box.hidden = false;
    if (!items.length) { box.innerHTML = `<div class="acempty">Keine Treffer für „${esc(lastQ)}“</div>`; return; }
    box.innerHTML = items.map((it, i) => {
      const cat = [it.class_name, it.subclass_name].filter(Boolean).map(esc).join(" › ");
      const en = it.name_en && it.name_en !== it.name ? `<span class="acen">${hl(it.name_en, lastQ)}</span> · ` : "";
      return `<a href="#/item/${it.item_id}?house=${it.house_id}" data-i="${i}" class="acrow ${i === sel ? "sel" : ""}">
        ${icon(it, "md")}
        <span class="acmain"><span class="acname q${it.quality ?? 1}">${hl(it.name, lastQ)}</span>
          <span class="acsub">${en}${cat || "Kategorie unbekannt"} · ID ${it.item_id}</span></span>
        <span class="acprice">${money(it.last_min)}<span class="acsub">${it.last_qty != null ? num(it.last_qty) + " Stk." : ""}</span></span></a>`;
    }).join("") + (opts.showAll !== false && total > items.length
      ? `<a href="#" data-all class="acall ${sel === items.length ? "sel" : ""}">Alle ${num(total)} Treffer anzeigen ›</a>` : "");
  };
  const query = (q) => {
    const my = ++seq;
    return api("search", { q, house: house || state.house, page_size: 8, sort: "relevance" }).then((r) => {
      if (my !== seq) return;
      items = r.items; total = r.total; sel = -1; lastQ = q; render();
    }).catch(() => { /* abgebrochen */ });
  };
  input.setAttribute("role", "combobox");
  input.setAttribute("aria-autocomplete", "list");
  input.addEventListener("input", () => {
    clearTimeout(timer);
    const q = input.value.trim();
    if (!q) { lastQ = ""; items = []; render(); return; }
    timer = setTimeout(() => query(q), 120);
  });
  input.addEventListener("focus", () => { if (lastQ && input.value.trim() === lastQ) render(); });
  input.addEventListener("keydown", (e) => {
    const max = items.length + (total > items.length && opts.showAll !== false ? 1 : 0) - 1;
    if (e.key === "ArrowDown" && !box.hidden) { sel = Math.min(max, sel + 1); render(); e.preventDefault(); }
    else if (e.key === "ArrowUp" && !box.hidden) { sel = Math.max(-1, sel - 1); render(); e.preventDefault(); }
    else if (e.key === "Enter") {
      if (sel >= 0 && sel < items.length) { e.preventDefault(); pick(items[sel]); }
      else if (sel === items.length && items.length) { e.preventDefault(); showAll(); }
      else if (opts.pickFirstOnEnter && items.length) { e.preventDefault(); pick(items[0]); }
      else box.hidden = true;
    } else if (e.key === "Escape") { box.hidden = true; sel = -1; }
  });
  box.addEventListener("mousedown", (e) => e.preventDefault()); // Fokus im Feld halten
  box.addEventListener("click", (e) => {
    if (e.target.closest("a[data-all]")) { e.preventDefault(); showAll(); return; }
    const a = e.target.closest("a[data-i]");
    if (a) { e.preventDefault(); pick(items[+a.dataset.i]); }
  });
  document.addEventListener("click", (e) => { if (!box.contains(e.target) && e.target !== input) box.hidden = true; });
  function reset() { box.hidden = true; input.value = ""; items = []; lastQ = ""; sel = -1; }
  function pick(it) { reset(); input.blur(); onPick(it); }
  function showAll() { const q = lastQ; reset(); go("/search", { q, house: house || state.house, sort: "relevance" }); }
}

// =================================================================== Start
async function loadHouses() {
  const d = await api("houses");
  state.houses = d.houses.filter((h) => h.snapshots > 0);
  if (!state.house || !houseById(state.house)) state.house = d.default ? String(d.default) : "";
  const sel = $("#houseSel");
  const groups = {};
  for (const h of state.houses) (groups[`${(h.region || "?").toUpperCase()} · ${h.source}`] ||= []).push(h);
  sel.innerHTML = state.houses.length ? Object.entries(groups).map(([g, hs]) => `<optgroup label="${esc(g)}">${hs.map((h) => `<option value="${h.id}" ${String(h.id) === String(state.house) ? "selected" : ""}>${esc(h.name)}</option>`).join("")}</optgroup>`).join("")
    : '<option value="">Keine Daten</option>';
  updateFreshness();
}
function updateFreshness() {
  const h = houseById(state.house), el = $("#fresh");
  if (!el) return;
  if (!h) { el.hidden = true; return; }
  el.hidden = false;
  el.textContent = `Stand: ${ago(h.last_ts)}`;
  el.title = `Letzter Datenpunkt: ${dt(h.last_ts)}${h.granularity === "day" ? " (Tageswerte aus Auctionator)" : ""}${STATIC ? " · statischer Export" : ""}`;
}

function initTheme() {
  const saved = store.get("theme", null);
  const t = saved || (matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark");
  document.documentElement.dataset.theme = t;
  $("#themeBtn").onclick = () => {
    const n = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = n; store.set("theme", n); route();
  };
}

(async function init() {
  initTheme();
  updateCompareBadge();
  STATIC = await StaticAPI.detect();
  try { await loadHouses(); } catch (e) { main.innerHTML = `<div class="error">Server nicht erreichbar: ${esc(e.message)}</div>`; return; }
  $("#houseSel").onchange = (e) => {
    state.house = e.target.value; store.set("house", state.house); classCache = {};
    updateFreshness();
    const r = parseHash();
    const p = { ...r.params }; delete p.house; delete p.page;
    go(r.path, p, true);
  };
  attachAutocomplete($("#quickInput"), $("#quickResults"), null, (it) => (location.hash = `/item/${it.item_id}?house=${it.house_id}`));
  $("#quickSearch").onsubmit = (e) => { e.preventDefault(); const q = $("#quickInput").value.trim(); if (q) { $("#quickResults").hidden = true; go("/search", { q, house: state.house }); } };
  window.addEventListener("hashchange", route);
  route();
})();
