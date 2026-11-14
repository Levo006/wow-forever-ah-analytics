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
    classCache = {};
    renderMarket();
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
const hname = (h) => (h ? h.label || h.name : "");
const groupId = (k) => state.houses.find((h) => h.kind === k)?.id;
/** <option>s mit Gruppen: Märkte, Verbund A, B, …, eigene Scans. */
function houseOptions(selected, { markets = true } = {}) {
  const groups = [];
  if (markets) groups.push(["Gesamt je Fraktion", state.houses.filter((h) => ["allianz", "horde", "neutral"].includes(h.kind)), hname]);
  const byV = {};
  for (const h of state.houses.filter((h) => !h.kind || h.kind === "haus")) (byV[h.verbund ? "Verbund " + h.verbund : "Auktionshäuser"] ||= []).push(h);
  for (const hs of Object.values(byV)) hs.sort((a, b) => ["Allianz", "Horde", "Neutral"].indexOf(a.faction) - ["Allianz", "Horde", "Neutral"].indexOf(b.faction));
  for (const [g, hs] of Object.entries(byV).sort()) groups.push([g, hs, (h) => h.verbund ? `${h.faction} #${String(h.ext_id).split("-").pop()}` : h.name]);
  groups.push(["Eigene Scans", state.houses.filter((h) => h.kind === "scan"), (h) => h.name]);
  return groups.filter(([, hs]) => hs.length).map(([g, hs, lab]) => `<optgroup label="${esc(g)}">${hs.map((h) =>
    `<option value="${h.id}" ${String(h.id) === String(selected) ? "selected" : ""}>${esc(lab(h))}</option>`).join("")}</optgroup>`).join("");
}
function scopeText(h) {
  if (!h) return "";
  if (h.kind === "allianz" || h.kind === "horde") return `Alle ${h.faction}-Auktionshäuser aller Server-Verbünde zusammengefasst`;
  if (h.kind === "neutral") return "Neutrale Auktionshäuser (Beutebucht, Gadgetzan, Everlook) aller Verbünde";
  if (h.kind === "scan") return "Eigene Auctionator-Scans (Tageswerte)";
  return h.verbund ? `Einzelnes Auktionshaus · ${h.faction} · Verbund ${h.verbund}` : "Einzelnes Auktionshaus";
}

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
  house: { label: "Markt", fmt: (r) => `<span class="pill">${esc(houseById(r.house_id)?.short || r.house_name)}</span>` },
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
/** Große Suche oben auf der Startseite (sofort sichtbar, Übersicht lädt darunter). */
function homeSearch(house) {
  const ex = ["Kupfererz", "Seidenstoff", "Rezept: Elixier", "Muster: Leinen", "Zähes Wolfsfleisch"];
  return `<form class="card hero" id="heroForm" role="search">
      <h1>Item suchen</h1>
      <p class="muted">Preise, Verlauf, Herkunft (Beute, Händler, Quests) und Verwendung (Berufe, Rezepte, was daraus wird) – für jedes Item, auch ohne AH-Angebot.</p>
      <div class="herobox"><input id="heroInput" type="search" name="q" autocomplete="off" placeholder="Item-Name (DE/EN) oder ID" aria-label="Item suchen">
        <button class="btn primary">Suchen</button><div id="heroResults" class="ac hero-ac" hidden></div></div>
      <div class="heroex muted small">Beispiele: ${ex.map((q) => `<a href="#/search?q=${encodeURIComponent(q)}${house ? "&house=" + house : ""}">${esc(q)}</a>`).join(" · ")}</div>
    </form>
    <div id="dash"><div class="loading">Lade Marktübersicht …</div></div>`;
}

async function viewDashboard(r) {
  const house = curHouse(r.params);
  main.innerHTML = homeSearch(house);
  const input = $("#heroInput");
  attachAutocomplete(input, $("#heroResults"), house, (it) => (location.hash = `/item/${it.item_id}${it.house_id ? "?house=" + it.house_id : ""}`));
  $("#heroForm").onsubmit = (e) => { e.preventDefault(); const q = input.value.trim(); if (q) { $("#heroResults").hidden = true; go("/search", { q, house }); } };
  if (!matchMedia("(pointer: coarse)").matches) input.focus();   // am Handy keine Tastatur aufklappen
  const d = await api("overview", { house });
  const k = d.kpis, h = d.house;
  const hh = houseById(h.id) || h;
  $("#dash").innerHTML = `
    <div class="pagehead"><h1>${esc(hname(hh))}</h1>
      <span class="sub">${esc(scopeText(hh))} · Stand ${dt(d.last_ts)} (${ago(d.last_ts)})</span></div>
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
  if (!p.house) p.house = state.house;
  const classes = await getClasses(p.house || state.house);
  const subs = classes.find((c) => String(c.class_id) === String(p.class))?.subclasses || [];
  const quals = (p.quality || "").split(",").filter(Boolean);
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
      <label class="f"><span>Markt</span><select name="house">${houseOptions(p.house)}</select></label>
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
    go("/search", np, true);
  };
  form.addEventListener("submit", (e) => { e.preventDefault(); submit(); });
  const qWrap = form.q.parentElement; qWrap.style.position = "relative";
  qWrap.insertAdjacentHTML("beforeend", '<div class="ac" id="qRes" hidden></div>');
  attachAutocomplete(form.q, $("#qRes"), p.house || state.house, (it) => (location.hash = `/item/${it.item_id}${it.house_id ? "?house=" + it.house_id : ""}`), { showAll: false });
  form.class.addEventListener("change", () => { form.subclass.value = ""; submit(); });
  $$("select, input[type=checkbox]", form).forEach((el) => el.name !== "class" && el.addEventListener("change", submit));
  $("#reset").onclick = () => go("/search", {}, true);
  const res = await api("search", { ...p, spark: 1, page_size: 50 });
  $("#sumline").textContent = `${num(res.total)} Treffer`;
  $("#res").innerHTML = table([COL.item, COL.price, COL.median, COL.avg30, COL.qty, COL.chg24, COL.chg7d, COL.vol, COL.value, COL.spark, COL.cmp], res.items, {
    sort: p.sort, dir: p.dir, onSort: (s, d) => setParams({ sort: s, dir: d, page: 1 }),
    rowHref: (x) => `/item/${x.item_id}${x.house_id ? "?house=" + x.house_id : ""}`, empty: "Keine Items passen zu den Filtern.",
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
const FACTIONS = [
  { k: "allianz", name: "Allianz", fac: "Allianz", sub: "alle Verbünde" },
  { k: "horde", name: "Horde", fac: "Horde", sub: "alle Verbünde" },
  { k: "neutral", name: "Neutral", fac: "Neutral", sub: "Schwarzmeerräuber" },
];
const GROUP_KINDS = FACTIONS.map((f) => f.k);

function wowTooltip(it) {
  const d = it.details;
  const lines = (d && d.lines) || [];
  if (!lines.length && !it.vendor_sell) return "";
  const col = { w: "", g: "cg", y: "cy", b: "cb", m: "cm" };
  return `<div class="wtip"><div class="tn q${it.quality ?? 1}">${esc(it.name)}</div>
    ${it.item_level && it.class_id != null && [2, 4].includes(+it.class_id) ? `<div class="cy">Gegenstandsstufe ${it.item_level}</div>` : ""}
    ${lines.map((l) => `<div class="ln ${col[l.c] || ""}"><span>${esc(l.t)}</span>${l.r ? `<span>${esc(l.r)}</span>` : ""}</div>`).join("")}
    ${it.vendor_sell ? `<div class="sell">Verkaufspreis: ${money(it.vendor_sell)}</div>` : ""}</div>`;
}

/** Aufklappbarer Abschnitt; Inhalt wird erst beim ersten Öffnen gerendert (Zustand wird gemerkt). */
function fold(key, title, summary = "", openByDefault = false) {
  const open = store.get("fold." + key, openByDefault);
  return `<details class="fold" data-fold="${key}" ${open ? "open" : ""}><summary><span class="ft">${title}</span>${summary ? `<span class="fs">${summary}</span>` : ""}<span class="chev" aria-hidden="true">›</span></summary><div class="fb" id="fb-${key}"></div></details>`;
}
function wireFolds(renderers) {
  $$("details.fold").forEach((d) => {
    const k = d.dataset.fold;
    const run = () => {
      if (d.open && renderers[k] && !d._done) {
        d._done = true;
        Promise.resolve(renderers[k]($("#fb-" + k))).catch((e) => { if (e.name !== "AbortError") $("#fb-" + k).innerHTML = `<div class="error">${esc(e.message)}</div>`; });
      }
    };
    d.addEventListener("toggle", () => { store.set("fold." + k, d.open); run(); });
    run();
  });
}

// ============================================================ Rezepte
let recipeIdxP = null;
/** Rezeptdaten einmal laden und indizieren (wird über Seitenwechsel hinweg behalten). */
function loadRecipes() {
  if (!recipeIdxP) recipeIdxP = api("recipes").then((d) => RecipeCore.buildIndex(d)).catch((e) => { recipeIdxP = null; throw e; });
  return recipeIdxP;
}
let questIdxP = null;
function loadQuests() {
  if (!questIdxP) questIdxP = api("quests").then((d) => RecipeCore.buildQuestIndex(d)).catch((e) => { questIdxP = null; throw e; });
  return questIdxP;
}
/** Rezepte + Quests/Sets; fehlt eine Datei, bleibt der jeweilige Teil leer. */
function loadGame() {
  return Promise.all([loadRecipes().catch(() => RecipeCore.buildIndex({})), loadQuests().catch(() => RecipeCore.buildQuestIndex({}))]);
}
/** Herkunft (QuestieDB) eines Items; ohne Daten null. */
function loadSources(id) {
  return api(`sources/${id}`).then((d) => RecipeCore.sourceView(d, id)).catch(() => null);
}
const RC_MAX_DEPTH = 8;

/** Kurzbeschreibung für die Kopfzeile des Abschnitts. */
function recipeSummary(idx, id, qidx = null, sv = null) {
  const uses = RecipeCore.usedIn(idx, id), made = RecipeCore.madeBy(idx, id);
  const quests = qidx ? qidx.byReward.get(id) || [] : [], sets = qidx ? RecipeCore.setsOf(qidx, id) : [];
  const teach = RecipeCore.teaches(idx, id);
  if (!uses.length && !made.length && !quests.length && !sets.length && !sv && !teach.length) return "keine Herkunft oder Verwendung bekannt";
  const parts = [];
  if (teach.length) parts.push(`lehrt ${teach.map((r) => `${r.prof}: ${r.name}`).join(", ")}`);
  const pl = (n, one, many) => `${num(n)} ${n === 1 ? one : many}`;
  if (sv?.dropsTotal) parts.push(`Beute von ${pl(sv.dropsTotal, "Gegner", "Gegnern")}`);
  if (sv?.objects.length) parts.push(`Fundort: ${sv.objects.slice(0, 2).map((o) => o.name).join(", ")}${sv.objects.length > 2 ? " …" : ""}`);
  if (sv?.vendorsTotal) parts.push(pl(sv.vendorsTotal, "Händler", "Händler"));
  if (sv?.containers.length) parts.push(`in ${pl(sv.containers.length, "Behälter", "Behältern")}`);
  if (sv?.contents.length) parts.push(`enthält ${pl(sv.contents.length, "Item", "Items")}`);
  if (sv?.starts.length) parts.push("startet eine Quest");
  if (sv?.quests.length) parts.push(`für ${pl(sv.quests.length, "Quest", "Quests")} benötigt`);
  if (quests.length) parts.push(`Belohnung aus ${num(quests.length)} Quest${quests.length === 1 ? "" : "s"}`);
  if (sets.length) parts.push(`Set: ${sets.map((s) => s.name).join(", ")}`);
  if (made.length) parts.push(`herstellbar · ${[...new Set(made.map((r) => r.prof))].join(", ")}`);
  if (uses.length) {
    const profs = [...new Set(uses.map((r) => r.prof))];
    parts.push(`Zutat in ${num(uses.length)} Rezept${uses.length === 1 ? "" : "en"} · ${profs.slice(0, 3).join(", ")}${profs.length > 3 ? ` +${profs.length - 3}` : ""}`);
    const down = RecipeCore.countTree(RecipeCore.usesTree(idx, id, { maxDepth: 4, maxNodes: 1500 })).items;
    if (down > uses.length) parts.push(`daraus ${num(down)} Items`);
  }
  return parts.join(" · ");
}

function renderRecipes(el, id, fk) {
  return Promise.all([loadGame(), loadSources(id)]).then(([[idx, qidx], sv]) => {
    const fac = FACTIONS.find((f) => f.k === fk) || FACTIONS[0];
    const hid = groupId(fk);
    const uses = RecipeCore.usedIn(idx, id), made = RecipeCore.madeBy(idx, id);
    const quests = RecipeCore.rewardedBy(qidx, id, fk), sets = RecipeCore.setsOf(qidx, id);
    const teach = RecipeCore.teaches(idx, id);
    if (!uses.length && !made.length && !quests.length && !sets.length && !sv && !teach.length) {
      el.innerHTML = '<div class="empty">Für dieses Item sind keine Herkunft (Beute, Händler, Quests, Rezepte) und keine Verwendung bekannt.</div>';
      return;
    }
    let questsAll = false, dropsAll = false, needAll = false;
    const profs = RecipeCore.professionsFor(idx, id);
    let prof = store.get("rec.prof", "");
    if (prof && !profs.some((p) => p.prof === prof)) prof = "";
    let best = store.get("rec.best", false);
    const opts = () => ({ mode: best ? "best" : "ah" });

    const info = (iid) => (idx.items[String(iid)] ? RecipeCore.itemInfo(idx, iid)
      : qidx.items[String(iid)] ? RecipeCore.itemInfo(qidx, iid) : RecipeCore.itemInfo({ items: sv?.names || {} }, iid));
    const label = (iid) => {
      const it = info(iid);
      const noPage = sv?.names?.[String(iid)]?.[4] === 0 && !idx.items[String(iid)] && !qidx.items[String(iid)];
      if (noPage) return `<a class="item" href="https://www.wowhead.com/forever/de/item=${iid}" target="_blank" rel="noopener" title="Hier ohne eigene Seite – bei Wowhead öffnen">${itemLabel({ id: iid, name: it.name, quality: it.quality, icon: it.icon }, false).replace(/^<span class="item">|<\/span>$/g, "")} ↗</a>`;
      return itemLabel({ id: iid, name: it.name, quality: it.quality, icon: it.icon, house_id: hid });
    };
    const resultCell = (r) => r.item ? label(r.item) : `<span class="item"><span class="ico bq1">✦</span><span title="${esc(r.name)}">${esc(r.name)}</span></span>`;
    const yieldTxt = (r) => (r.qmin === r.qmax ? num(r.qmin) : `${num(r.qmin)}–${num(r.qmax)}`) + "×";
    const noPrice = (title = "Derzeit kein Angebot im AH (evtl. Händlerware)") => `<span class="miss" title="${esc(title)}">kein Preis</span>`;
    const costCell = (m) => m.complete ? money(m.perUnit ?? m.cost)
      : `<span class="miss" title="${m.missing.length} Zutat(en) ohne AH-Preis">unvollständig</span>`;
    const priceCell = (iid) => { const p = iid ? RecipeCore.price(idx, fk, iid) : null; return iid ? (p ? money(p.min) : noPrice()) : '<span class="muted">—</span>'; };
    const marginCell = (r) => {
      if (!r.item) return '<span class="muted">—</span>';
      const g = RecipeCore.margin(idx, fk, r, opts());
      if (g.profit == null) return '<span class="muted">–</span>';
      return `<span class="${g.profit >= 0 ? "up" : "down"}">${g.profit >= 0 ? "+" : ""}${money(g.profit)}</span>${g.pct != null ? ` <span class="muted small">${num(g.pct, 0)} %</span>` : ""}`;
    };

    // ----- „Verwendet in“: Baumtabelle, Ebenen werden per Klick nachgeladen
    const useRow = (r, parentItem, depth, path) => {
      const reag = r.reagents.find((x) => x.id === parentItem);
      const cyc = r.item && path.includes(r.item);
      const next = r.item && !cyc ? RecipeCore.usedIn(idx, r.item).length : 0;
      const m = RecipeCore.materialCost(idx, fk, r, opts());
      const tog = cyc ? '<span class="rtog off" title="Kreislauf: dieses Item kommt im Pfad schon vor">↻</span>'
        : next && depth < RC_MAX_DEPTH ? `<button type="button" class="rtog" title="Daraus wird … (${next})" aria-expanded="false">▸</button>`
        : '<span class="rtog off"></span>';
      return `<tr class="nohover" data-d="${depth}" data-item="${r.item || ""}" data-path="${[...path, r.item].filter(Boolean).join(",")}">
        <td class="itemcell"><span class="rind" style="--d:${depth}">${tog}<span class="rname">${resultCell(r)}<span class="rcat">${depth ? esc(r.prof) + " · " : ""}${esc(r.cat)}${next && !cyc ? ` · <span title="Wird in ${next} weiteren Rezepten verwendet">→ ${next} weitere</span>` : ""}</span></span></span></td>
        <td class="n">${reag ? num(reag.q) + "×" : ""}</td>
        <td class="n">${r.item ? yieldTxt(r) : ""}</td>
        <td class="n">${costCell(m)}</td>
        <td class="n">${priceCell(r.item)}</td>
        <td class="n">${marginCell(r)}</td></tr>`;
    };
    const useHead = `<thead><tr><th>Ergebnis</th><th class="n" title="Benötigte Menge der Zutat in der Zeile darüber">Benötigt</th><th class="n">Ergibt</th>
      <th class="n" title="Materialkosten je hergestelltem Stück">Material/Stk</th><th class="n">AH-Preis</th><th class="n" title="AH-Preis nach 5 % Gebühr minus Materialkosten, je Stück">Spanne</th></tr></thead>`;

    // ----- „Hergestellt aus“: je Rezept Zutatenliste, herstellbare Zutaten aufklappbar
    const madeRows = (r, depth, path) => {
      const m = RecipeCore.materialCost(idx, fk, r, opts());
      return m.parts.map((x) => {
        const cyc = path.includes(x.id);
        const sub = !cyc && depth < RC_MAX_DEPTH ? RecipeCore.madeBy(idx, x.id) : [];
        const src = x.source === "craft" ? ' <span class="pill" title="Selbst herstellen ist günstiger als kaufen">selbst</span>' : "";
        return `<tr class="nohover" data-d="${depth}" data-mitem="${x.id}" data-path="${[...path, x.id].join(",")}">
          <td class="itemcell"><span class="rind" style="--d:${depth}">${cyc ? '<span class="rtog off" title="Kreislauf">↻</span>'
            : sub.length ? `<button type="button" class="rtog" title="Zutat selbst herstellen: ${esc(sub.map((s) => s.name).join(", "))}" aria-expanded="false">▸</button>` : '<span class="rtog off"></span>'}${label(x.id)}</span></td>
          <td class="n">${num(x.q)}×</td><td class="n">${x.unit == null ? noPrice() : money(x.unit) + src}</td><td class="n">${x.sum == null ? "" : money(x.sum)}</td></tr>`;
      }).join("");
    };
    const madeCard = (r) => {
      const m = RecipeCore.materialCost(idx, fk, r, opts());
      const g = RecipeCore.margin(idx, fk, r, opts());
      const sell = RecipeCore.price(idx, fk, r.item);
      const books = RecipeCore.taughtBy(idx, r).filter((b) => b !== id);
      const bookHtml = books.length ? `<div class="rbook muted small">Gelehrt durch ${books.map((b) => {
        const p = RecipeCore.price(idx, fk, b);
        return `${label(b)} <span>${p ? money(p.min) : noPrice("Derzeit kein Angebot im AH")}</span>`; }).join(" · ")}</div>` : "";
      return `<div class="rcard">
        <div class="rcardh"><b>${esc(r.name)}</b><span class="muted small">${esc(r.prof)}${r.cat ? " · " + esc(r.cat) : ""} · ergibt ${yieldTxt(r)}</span></div>${bookHtml}
        <div class="tscroll"><table class="t rt"><thead><tr><th>Zutat</th><th class="n">Menge</th><th class="n">Stückpreis</th><th class="n">Summe</th></tr></thead>
          <tbody data-kind="made">${madeRows(r, 0, [id])}</tbody></table></div>
        <div class="rfoot">
          <span>Material je Stück <b>${m.complete ? money(m.perUnit) : `≥ ${money(m.partial / r.qavg)} <span class="miss">${m.missing.length} ohne Preis</span>`}</b></span>
          <span>AH-Preis <b>${sell ? money(sell.min) : noPrice()}</b></span>
          <span>Spanne <b>${marginCell(r)}</b></span></div></div>`;
    };

    // ----- Quests: Belohnungen mit Preisen, wertvollste Wahl markiert
    const questRow = (q) => {
      const rw = RecipeCore.questRewards(qidx, fk, q);
      const mine = q.rewards.find((r) => r.id === id);
      const tags = [
        q.type ? `<span class="pill">${esc(q.type)}</span>` : "",
        q.faction ? `<span class="pill"><span class="fac ${esc(q.faction)}"></span>${esc(q.faction)}</span>` : "",
        q.classes ? `<span class="pill" title="Nur für diese Klassen">${esc(q.classes)}</span>` : "",
        q.repeatLabel ? `<span class="pill">${esc(q.repeatLabel)}</span>` : "",
        mine?.choice ? `<span class="pill" title="Eine Belohnung zur Wahl">zur Wahl</span>` : "",
      ].join("");
      const rewards = rw.list.length > 1 || !mine ? `<div class="qrw">${rw.list.map((r) => `<span class="qri ${r.id === id ? "me" : ""}">${label(r.id)}
          <span class="qp">${r.price ? money(r.price.min) : noPrice("Derzeit kein Angebot im AH")}${r.id === rw.best ? ' <span class="best" title="Wertvollste Wahlbelohnung im AH">★</span>' : ""}</span></span>`).join("")}</div>` : "";
      return `<div class="qrow"><div class="qt"><a href="https://www.wowhead.com/forever/de/quest=${q.id}" target="_blank" rel="noopener" title="Quest bei Wowhead öffnen">${esc(q.title)} ↗</a>
          <span class="muted small">${esc(q.zone || "")}</span>${tags}</div>${rewards}</div>`;
    };
    const questHtml = () => {
      if (!quests.length) return "";
      const shown = questsAll ? quests : quests.slice(0, 6);
      return `<h3>Questbelohnung <span class="muted small">${quests.length} Quest${quests.length === 1 ? "" : "s"}</span></h3>
        <div class="qlist">${shown.map(questRow).join("")}</div>
        ${quests.length > shown.length ? `<button type="button" class="btn small mt" id="qall">Alle ${quests.length} Quests anzeigen</button>` : ""}`;
    };
    // ----- Sets: Teile mit Preisen, Boni, Summe im AH
    const setHtml = () => !sets.length ? "" : `<h3>Teil eines Sets</h3><div class="rcards">${sets.map((s) => {
      const c = RecipeCore.setCost(qidx, fk, s);
      return `<div class="rcard"><div class="rcardh"><b>${esc(s.name)}</b><span class="muted small">${s.items.length} Teile</span></div>
        <div class="tscroll"><table class="t rt"><tbody>${c.parts.map((x) => `<tr class="nohover ${x.id === id ? "me" : ""}"><td class="itemcell">${label(x.id)}</td>
          <td class="n">${x.price ? money(x.price.min) : noPrice("Derzeit kein Angebot im AH")}</td></tr>`).join("")}</tbody></table></div>
        ${s.effects.length ? `<div class="sfx">${s.effects.map(([n, t]) => `<div><span class="muted">(${n})</span> ${esc(t)}</div>`).join("")}</div>` : ""}
        <div class="rfoot"><span>Alle Teile im AH <b>${c.have ? money(c.total) : noPrice()}</b>${c.complete ? "" : ` <span class="miss">${c.have} von ${c.count} angeboten</span>`}</span></div></div>`;
    }).join("")}</div>`;

    // ----- Herkunft aus der QuestieDB: Beute, Fundorte, Händler, Behälter, Quests
    const facName = { allianz: "Allianz", horde: "Horde" }[fk] || "";
    const wh = (kind, xid, text, title = "Bei Wowhead öffnen") =>
      `<a href="https://www.wowhead.com/forever/de/${kind}=${xid}" target="_blank" rel="noopener" title="${esc(title)}">${esc(text)} ↗</a>`;
    const facPill = (f) => f ? `<span class="pill"><span class="fac ${esc(f)}"></span>${esc(f)}</span>` : "";
    const otherLast = (a, b) => ((a.faction && facName && a.faction !== facName) ? 1 : 0) - ((b.faction && facName && b.faction !== facName) ? 1 : 0);
    const dropHtml = () => {
      if (!sv?.drops.length) return "";
      const shown = dropsAll ? sv.drops : sv.drops.slice(0, 10);
      const more = sv.dropsTotal - sv.drops.length;
      return `<h3>Beute von <span class="muted small">${num(sv.dropsTotal)} Gegner${sv.dropsTotal === 1 ? "" : "n"}${sv.hasChance ? "" : " · Dropchance nicht bekannt"}</span></h3>
        <div class="tscroll"><table class="t rt"><thead><tr><th>Gegner</th><th class="n">Stufe</th><th>Gebiet</th>${sv.hasChance ? '<th class="n">Chance</th>' : ""}</tr></thead><tbody>
        ${shown.map((n) => `<tr class="nohover"><td>${wh("npc", n.id, n.name)}${n.rank ? ` <span class="pill">${esc(n.rank)}</span>` : ""}</td>
          <td class="n">${esc(n.level)}</td><td>${esc(n.zone)}</td>${sv.hasChance ? `<td class="n">${n.chance == null ? "" : num(n.chance, n.chance < 10 ? 1 : 0) + " %"}</td>` : ""}</tr>`).join("")}</tbody></table></div>
        ${sv.drops.length > shown.length ? `<button type="button" class="btn small mt" id="dall">Alle ${sv.drops.length} anzeigen</button>` : ""}
        ${more > 0 && shown.length === sv.drops.length ? `<div class="muted small mt">… und ${num(more)} weitere – vollständige Liste bei ${wh("item", id, "Wowhead")}</div>` : ""}`;
    };
    const objHtml = () => !sv?.objects.length ? "" : `<h3>Fundort <span class="muted small">Abbau, Sammeln, Truhen</span></h3>
      <div class="olist">${sv.objects.map((o) => `<div class="orow">${wh("object", o.ids[0], o.name)}${o.zones.length
        ? ` <span class="muted small">${esc(o.zones.slice(0, 8).join(", "))}${o.zones.length > 8 ? ` +${o.zones.length - 8}` : ""}</span>` : ""}</div>`).join("")}</div>`;
    const vendorHtml = () => !sv?.vendors.length ? "" : `<h3>Verkauft von <span class="muted small">${num(sv.vendorsTotal)} Händler</span></h3>
      <div class="tscroll"><table class="t rt"><thead><tr><th>Händler</th><th>Gebiet</th><th>Fraktion</th></tr></thead><tbody>
      ${sv.vendors.slice().sort(otherLast).map((n) => `<tr class="nohover"><td>${wh("npc", n.id, n.name)}${n.sub ? ` <span class="muted small">&lt;${esc(n.sub)}&gt;</span>` : ""}</td>
        <td>${esc(n.zone)}</td><td>${facPill(n.faction)}</td></tr>`).join("")}</tbody></table></div>
      ${sv.vendorsTotal > sv.vendors.length ? `<div class="muted small mt">… und ${num(sv.vendorsTotal - sv.vendors.length)} weitere bei ${wh("item", id, "Wowhead")}</div>` : ""}`;
    const anyPrice = (iid) => RecipeCore.price(idx, fk, iid) || RecipeCore.price(qidx, fk, iid);
    const itemList = (ids) => `<div class="qrw">${ids.map((x) => { const p = anyPrice(x); return `<span class="qri">${label(x)}${p ? ` <span class="qp">${money(p.min)}</span>` : ""}</span>`; }).join("")}</div>`;
    const boxHtml = (which) => which === "in"
      ? (sv?.containers.length ? `<h3>Enthalten in <span class="muted small">Behälter, Taschen, Kisten</span></h3>${itemList(sv.containers)}` : "")
      : (sv?.contents.length ? `<h3>Inhalt <span class="muted small">${num(sv.contents.length)} mögliche Items</span></h3>${itemList(sv.contents)}` : "");
    const needHtml = () => {
      const list = [...(sv?.starts || []), ...(sv?.quests || []).slice().sort(otherLast)];
      if (!list.length) return "";
      const shown = needAll ? list : list.slice(0, 8);
      return `<h3>Für Quests <span class="muted small">Questziel oder benötigter Gegenstand</span></h3>
        <div class="qlist">${shown.map((q) => `<div class="qrow"><div class="qt">${wh("quest", q.id, q.title, "Quest bei Wowhead öffnen")}
          <span class="muted small">${esc(q.zone)}${q.level ? ` · Stufe ${q.level}` : ""}</span>${facPill(q.faction)}<span class="pill">${esc(q.kindLabel)}</span></div></div>`).join("")}</div>
        ${list.length > shown.length ? `<button type="button" class="btn small mt" id="nall">Alle ${list.length} Quests anzeigen</button>` : ""}`;
    };
    const srcNote = () => sv ? `<div class="muted small srcnote">Beute, Fundorte, Händler, Behälter und Questziele aus der
      <a href="https://github.com/Questie/QuestieDB" target="_blank" rel="noopener">QuestieDB</a> (Community-Daten, nicht vollständig; NPC- und Objektnamen englisch).</div>` : "";

    const draw = () => {
      const u = uses.filter((r) => !prof || r.prof === prof);
      const mk = made.filter((r) => !prof || r.prof === prof);
      const byProf = new Map();
      for (const r of u) (byProf.get(r.prof) || byProf.set(r.prof, []).get(r.prof)).push(r);
      el.innerHTML = `
        ${profs.length ? `<div class="rbar">
          <div class="rchips" id="rprof">${[["", "Alle Berufe", uses.length + made.length], ...profs.map((p) => [p.prof, p.prof, p.count])]
            .map(([k, l, n]) => `<button type="button" class="${k === prof ? "on" : ""}" data-p="${esc(k)}">${esc(l)} <span>${n}</span></button>`).join("")}</div>
          <label class="small" title="Zutaten, die sich günstiger selbst herstellen lassen, mit Herstellkosten statt AH-Preis rechnen"><input type="checkbox" id="rbest" ${best ? "checked" : ""}> günstigster Weg (selbst herstellen)</label>
        </div>` : ""}
        <div class="muted small rnote"><span class="fac ${fac.fac}"></span>Preise: ${esc(fac.name)} gesamt · typischer Tiefstpreis${profs.length ? " · Spanne nach 5 % AH-Gebühr" : ""}</div>
        ${teach.length ? `<h3>Lehrt Rezept <span class="muted small">Materialkosten und Spanne beim Herstellen</span></h3><div class="rcards">${teach.map(madeCard).join("")}</div>` : ""}
        ${dropHtml()}${objHtml()}${vendorHtml()}${boxHtml("in")}${questHtml()}
        ${mk.length ? `<h3>Hergestellt aus</h3><div class="rcards">${mk.map(madeCard).join("")}</div>` : ""}
        ${needHtml()}${setHtml()}${boxHtml("out")}
        ${u.length ? `<div class="rhead"><h3>Verwendet in · daraus wird …</h3><button type="button" class="btn small" id="rexp">Alles aufklappen</button></div>
          ${[...byProf.entries()].map(([pn, rs]) => `<div class="rprof"><h4>${esc(pn)} <span class="muted small">${rs.length} Rezept${rs.length === 1 ? "" : "e"}</span></h4>
            <div class="tscroll"><table class="t rt">${useHead}<tbody data-kind="use">${rs.map((r) => useRow(r, id, 0, [id])).join("")}</tbody></table></div></div>`).join("")}` : ""}
        ${prof && !u.length && !mk.length ? '<div class="empty">Keine Rezepte für diesen Beruf.</div>' : ""}
        ${srcNote()}`;
    };

    // Auf-/Zuklappen einer Zeile: Kinder direkt darunter einfügen bzw. entfernen
    const collapse = (tr) => {
      const d = +tr.dataset.d;
      let n = tr.nextElementSibling;
      while (n && +n.dataset.d > d) { const x = n.nextElementSibling; n.remove(); n = x; }
      const b = $(".rtog", tr); if (b) { b.textContent = "▸"; b.setAttribute("aria-expanded", "false"); }
    };
    const expand = (tr) => {
      const d = +tr.dataset.d, path = tr.dataset.path.split(",").map(Number);
      const kind = tr.parentElement.dataset.kind;
      let html = "";
      if (kind === "use") {
        const iid = +tr.dataset.item;
        html = RecipeCore.usedIn(idx, iid).map((r) => useRow(r, iid, d + 1, path)).join("");
      } else {
        const iid = +tr.dataset.mitem;
        html = RecipeCore.madeBy(idx, iid).map((r) => `<tr class="nohover rsub" data-d="${d + 1}"><td colspan="4"><span class="rind muted small" style="--d:${d + 1}">${esc(r.prof)} · ${esc(r.name)} · ergibt ${yieldTxt(r)}</span></td></tr>`
          + madeRows(r, d + 1, path)).join("");
      }
      tr.insertAdjacentHTML("afterend", html);
      const b = $(".rtog", tr); if (b) { b.textContent = "▾"; b.setAttribute("aria-expanded", "true"); }
    };
    el.onclick = (e) => {
      const pb = e.target.closest("#rprof button");
      if (pb) { prof = pb.dataset.p; store.set("rec.prof", prof); draw(); return; }
      if (e.target.closest("#qall")) { questsAll = true; draw(); return; }
      if (e.target.closest("#dall")) { dropsAll = true; draw(); return; }
      if (e.target.closest("#nall")) { needAll = true; draw(); return; }
      if (e.target.closest("#rexp")) {
        // bis 3 Ebenen, höchstens ~400 Zeilen
        for (let level = 0; level < 3; level++) {
          const rows = $$('tbody[data-kind="use"] tr', el).filter((tr) => +tr.dataset.d === level && $("button.rtog[aria-expanded=false]", tr));
          if ($$('tbody[data-kind="use"] tr', el).length > 400) break;
          rows.forEach(expand);
        }
        return;
      }
      const b = e.target.closest("button.rtog");
      if (!b) return;
      const tr = b.closest("tr");
      if (b.getAttribute("aria-expanded") === "true") collapse(tr); else expand(tr);
    };
    el.onchange = (e) => { if (e.target.id === "rbest") { best = e.target.checked; store.set("rec.best", best); draw(); } };
    draw();
    // Wenige direkte Verwendungen (z. B. Erz → Barren): erste Ebene gleich aufklappen
    const top = $$('tbody[data-kind="use"] tr', el);
    if (top.length && top.length <= 3) top.forEach((tr) => { if ($("button.rtog", tr)) expand(tr); });
  });
}

/** Kopfzeile „Herkunft & Verwendung“ füllen, sobald die Daten da sind; ganz ohne Daten ausblenden. */
function setRecipeSummary(id) {
  Promise.all([loadGame(), loadSources(id)]).then(([[idx, qidx], sv]) => {
    const fs = $('details[data-fold="rec"] .fs');
    if (!fs) return;
    if (!idx.recipes.length && !qidx.quests.length && !qidx.sets.length && !sv) { fs.closest("details").remove(); return; }
    fs.textContent = recipeSummary(idx, id, qidx, sv);
  });
}

async function viewItem(r) {
  const id = +r.parts[1];
  const p = { range: "7d", ...r.params };
  const info = await api(`item/${id}`);
  const it = info.item;
  if (!info.houses.length) {
    // Item ohne AH-Daten (z. B. nur aus Rezepten bekannt): Kopf + Herstellung/Verwendung
    const sh = houseById(state.house);
    const fk0 = (sh && (GROUP_KINDS.includes(sh.kind) ? sh.kind : sh.group)) || "allianz";
    main.innerHTML = `<div class="ihead"><div class="who">${icon(it, true)}
        <div style="min-width:0"><h1 class="q${it.quality ?? 1}">${esc(it.name)}</h1>
          <div class="en">${it.name_en && it.name_en !== it.name ? esc(it.name_en) + " · " : ""}ID ${id} · <a href="https://www.wowhead.com/forever/de/item=${id}" target="_blank" rel="noopener">Wowhead ↗</a></div>
          ${it.class_name || it.item_level ? `<div class="chips mt">${it.class_name ? `<span class="pill">${esc([...new Set([it.class_name, it.subclass_name].filter(Boolean))].join(" › "))}</span>` : ""}${it.item_level ? `<span class="pill">Stufe ${it.item_level}</span>` : ""}</div>` : ""}</div></div></div>
      <div class="card"><div class="empty">Dieses Item wurde bisher in keinem Auktionshaus angeboten (z. B. seelengebunden, Quest-Gegenstand oder nur selbst herstellbar).</div></div>
      <div class="folds">${fold("rec", "Herkunft & Verwendung", "Lade Rezepte und Quests …", true)}</div>`;
    wireFolds({ rec: (el) => renderRecipes(el, id, fk0) });
    setRecipeSummary(id);
    return;
  }

  // Auswahl: Haus aus URL/globaler Auswahl → zugehörige Fraktion
  const want = String(p.house || state.house || "");
  const wantH = houseById(want);
  let house = info.houses.find((h) => String(h.house_id) === want);
  let fk = house ? (GROUP_KINDS.includes(house.kind) ? house.kind : house.group) : (wantH && GROUP_KINDS.includes(wantH.kind) ? wantH.kind : wantH?.group);
  if (!fk) fk = "allianz";
  if (!house) house = info.houses.find((h) => h.kind === fk) || null;
  const fac = FACTIONS.find((f) => f.k === fk);
  const groupRow = (k) => info.houses.find((h) => h.kind === k);
  const total = groupRow(fk);
  // Häuser der Fraktion, auch ohne Angebot (dann „nicht angeboten“)
  const allSingles = state.houses.filter((h) => h.kind === "haus" && h.group === fk).sort((a, b) => (a.verbund || "").localeCompare(b.verbund || ""));
  const rowOf = (hid) => info.houses.find((h) => h.house_id === hid);
  const listed = allSingles.map((h) => rowOf(h.id)).filter((x) => x && x.last_min);
  const cheapest = listed.reduce((m, h) => (!m || h.last_min < m.last_min ? h : m), null);
  const hid = house?.house_id;
  const inCmp = state.compare.includes(id);

  // Kopf
  const catHref = (extra = "") => `#/categories?class=${it.class_id ?? -1}${extra}`;
  const tags = [
    it.class_name ? `<a class="tag" href="${catHref()}">${esc(it.class_name)}${it.subclass_name ? ` › ${esc(it.subclass_name)}` : ""}</a>` : "",
    it.slot ? `<span class="tag">${esc(it.slot)}</span>` : "",
    it.item_level ? `<span class="tag">Stufe <b>${it.item_level}</b></span>` : "",
    it.required_level ? `<span class="tag">ab Level <b>${it.required_level}</b></span>` : "",
    it.is_commodity ? `<span class="tag">Handelsware</span>` : "",
    it.vendor_sell ? `<span class="tag">Händler zahlt ${money(it.vendor_sell)}</span>` : "",
  ].filter(Boolean).join("");

  // Fraktions-Reiter mit Preis
  const tabs = FACTIONS.map((f) => {
    const g = groupRow(f.k);
    return `<button type="button" class="ftab ${f.k} ${f.k === fk ? "on" : ""}" data-k="${f.k}" role="tab" aria-selected="${f.k === fk}">
      <span class="fn"><span><span class="fac ${f.fac}"></span>${f.name}</span><small>${f.sub}</small></span>
      <span class="fp">${g ? money(g.last_min) : '<span class="muted">nicht angeboten</span>'}</span>
      ${g ? `<span class="fd">${pct(g.chg24_min)}</span>` : ""}</button>`;
  }).join("");

  // Karten: Gesamt + Verbünde
  const card = (row, title, sub, opts = {}) => {
    const hidC = row ? row.house_id : opts.hid;
    const on = hidC && hidC === hid;
    if (!row || !row.last_min) return `<div class="hcard off ${opts.total ? "total" : ""}"><div class="ht"><b>${title}</b><span>${sub}</span></div><div class="hp muted">nicht angeboten</div></div>`;
    return `<button type="button" class="hcard ${opts.total ? "total" : ""} ${on ? "on" : ""}" data-h="${hidC}">
      <div class="ht"><b>${title}</b><span>${sub}</span></div>
      <div class="hp">${money(row.last_min)}</div>
      <div class="hm"><span>Median ${money(row.last_median)}</span><span>${compact(row.last_qty)} Stück</span></div>
      <div class="hm"><span>24 h ${pct(row.chg24_min)}</span>${cheapest && row.house_id === cheapest.house_id && listed.length > 1 ? '<span class="best">★ günstigstes</span>' : ""}</div></button>`;
  };
  const cards = card(total, `${fac.name} gesamt`, "typischer Tiefstpreis", { total: true, hid: groupId(fk) }) +
    allSingles.map((h) => card(rowOf(h.id), `Verbund ${h.verbund || "?"}`, `#${String(h.ext_id).split("-").pop()}`, { hid: h.id })).join("");

  // Hinweise
  const hints = [];
  if (house && it.vendor_sell && house.last_min && house.last_min < it.vendor_sell)
    hints.push(`💰 Günstiger als der Händlerpreis (${money(it.vendor_sell)}) – kaufen und beim Händler verkaufen lohnt sich.`);
  if (house && house.avg7_min && house.last_min && house.last_min < house.avg7_min * 0.85)
    hints.push(`▼ Gerade ${num((1 - house.last_min / house.avg7_min) * 100, 0)} % unter dem 7-Tage-Schnitt.`);

  const selName = !house ? fac.name : GROUP_KINDS.includes(house.kind) ? `${fac.name} gesamt` : `${fac.name} · Verbund ${house.verbund || "?"}`;
  const daily = house?.granularity === "day";
  const ranges = daily ? ["7d", "30d", "90d", "all"] : ["24h", "7d", "30d", "all"];
  if (!ranges.includes(p.range)) p.range = daily ? "30d" : "7d";
  const shown = new Set((p.show || "min,median").split(","));
  const overlay = p.cmp === "1";

  main.innerHTML = `
    <div class="crumbs"><a href="#/categories">Kategorien</a> › <a href="${catHref()}">${esc(it.class_name || "Ohne Metadaten")}</a>${it.subclass_name ? ` › <a href="${catHref(`&subclass=${it.subclass_id}`)}">${esc(it.subclass_name)}</a>` : ""}</div>
    <div class="ihead">
      <div class="who">${icon(it, true)}
        <div style="min-width:0"><h1 class="q${it.quality ?? 1}">${esc(it.name)}</h1>
          <div class="en">${it.name_en && it.name_en !== it.name ? esc(it.name_en) + " · " : ""}ID ${id} · <a href="https://www.wowhead.com/forever/de/item=${id}" target="_blank" rel="noopener">Wowhead ↗</a></div>
          ${tags ? `<div class="chiprow">${tags}</div>` : ""}</div></div>
      <div class="actions">
        <button class="btn" id="cmpBtn">${inCmp ? "✓ Im Vergleich" : "+ Vergleich"}</button>
        <a class="btn" href="#/compare?items=${[...new Set([...state.compare, id])].join(",")}${hid ? "&house=" + hid : ""}">Vergleichen ›</a>
      </div></div>
    <div class="ftabs" role="tablist" id="ftabs">${tabs}</div>
    <div class="hcards" id="hcards">${cards}</div>
    ${hints.map((h) => `<div class="hint">${h}</div>`).join("")}
    ${house ? `
    <div class="card">
      <div class="cardhead"><h2>Preisverlauf <span class="muted small" style="font-weight:400">· ${esc(selName)}</span></h2>
        <div class="right">
          <div class="seg" id="rng">${ranges.map((x) => `<button data-r="${x}" class="${x === p.range ? "on" : ""}">${x === "all" ? "Alles" : x.replace("d", " T").replace("h", " h")}</button>`).join("")}</div>
          <details class="pop"><summary class="btn small" title="Anzeige anpassen">Anzeige ▾</summary>
            <div class="popbody" id="tog">
              ${[["min", "Min", "--s1"], ["p10", "10 %-Perzentil", "--s7"], ["median", "Median", "--s2"], ["mean", "Mittelwert", "--s3"], ["max", "Max", "--s4"]].map(([k, l, c]) =>
                `<label><input type="checkbox" value="${k}" ${shown.has(k) ? "checked" : ""}><span class="sw" style="background:var(${c})"></span>${l}</label>`).join("")}
              <label><input type="checkbox" value="out" ${p.out !== "0" ? "checked" : ""}>Ausreißer markieren</label>
              <hr><label title="Mindestpreis aller Verbünde dieser Fraktion in einem Chart"><input type="checkbox" value="cmp" ${overlay ? "checked" : ""}>Verbünde vergleichen</label>
            </div></details>
        </div></div>
      <div class="chartbox"><canvas id="pc"></canvas></div>
      <div class="muted small" id="pcnote" style="margin-top:6px"></div>
    </div>
    <div class="folds">
      ${fold("rec", "Herkunft & Verwendung", "Lade Rezepte und Quests …", true)}
      ${fold("kpi", "Kennzahlen", house.avg7_min ? `Ø 7 T ${money(house.avg7_min)} · 7 T ${pct(house.chg7d_min)}` : "")}
      ${fold("qty", "Angebotsmenge", house.last_qty != null ? `${num(house.last_qty)} Stück aktuell` : "")}
      ${fold("season", "Tageszeit & Wochentag", "Wann ist es am günstigsten?")}
      ${fold("all", "Alle Auktionshäuser im Vergleich", `${info.houses.filter((h) => h.kind === "haus").length} Häuser`)}
      ${fold("out", "Ausreißer", "")}
      ${fold("snaps", "Einzelne Snapshots", "")}
      ${fold("info", "Item-Details", it.details?.lines?.length ? "Tooltip wie im Spiel" : "")}
    </div>` : `<div class="card"><div class="empty">Bei der ${esc(fac.name)} derzeit nicht angeboten.</div></div>
    <div class="folds">${fold("rec", "Herkunft & Verwendung", "Lade Rezepte und Quests …", true)}${fold("all", "Alle Auktionshäuser im Vergleich", "")}${fold("info", "Item-Details", "")}</div>`}`;
  setRecipeSummary(id);
  const recFold = (el) => renderRecipes(el, id, fk);

  // Interaktion
  $("#ftabs").onclick = (e) => { const b = e.target.closest("button[data-k]"); if (b) { const g = groupId(b.dataset.k); if (g) setParams({ house: g }); } };
  $("#hcards").onclick = (e) => { const b = e.target.closest("button[data-h]"); if (b) setParams({ house: b.dataset.h }); };
  $("#cmpBtn").onclick = (e) => { toggleCompare(id); e.target.textContent = state.compare.includes(id) ? "✓ Im Vergleich" : "+ Vergleich"; };

  const allTable = (el) => {
    const rows = info.houses.filter((h) => GROUP_KINDS.includes(h.kind) || h.kind === "haus")
      .sort((a, b) => (GROUP_KINDS.indexOf(a.group || a.kind) - GROUP_KINDS.indexOf(b.group || b.kind)) || ((GROUP_KINDS.includes(b.kind) ? 1 : 0) - (GROUP_KINDS.includes(a.kind) ? 1 : 0)) || (a.verbund || "").localeCompare(b.verbund || ""));
    const ref = (h) => groupRow(h.group || h.kind);
    el.innerHTML = table([
      { label: "Auktionshaus", fmt: (h) => GROUP_KINDS.includes(h.kind) ? `<b><span class="fac ${esc(h.faction)}"></span>${esc(h.faction)} gesamt</b>` : `<span class="fac ${esc(h.faction)}"></span>${esc(h.faction)} · Verbund ${esc(h.verbund || "?")}` },
      { label: "Min", num: true, fmt: (h) => money(h.last_min) },
      { label: "Median", num: true, fmt: (h) => money(h.last_median) },
      { label: "Ø 7 T", num: true, fmt: (h) => money(h.avg7_min) },
      { label: "Stück", num: true, fmt: (h) => num(h.last_qty) },
      { label: "Δ 24 h", num: true, fmt: (h) => pct(h.chg24_min) },
      { label: "vs. Fraktion", num: true, title: "Mindestpreis im Vergleich zum Fraktions-Gesamtpreis", fmt: (h) => GROUP_KINDS.includes(h.kind) || !ref(h)?.last_min ? '<span class="muted">—</span>' : pct((h.last_min / ref(h).last_min - 1) * 100, 0) },
    ], rows, { rowHref: (h) => `/item/${id}?house=${h.house_id}&range=${p.range}` });
  };
  const infoBox = (el) => {
    el.innerHTML = `<div class="infogrid">${wowTooltip(it) || '<div class="muted">Für dieses Item liegen keine Tooltip-Daten vor.</div>'}
      <table class="t kv"><tbody>
        ${[["Item-ID", id], ["Englischer Name", esc(it.name_en || "–")], ["Qualität", esc(it.quality_name || "–")], ["Kategorie", esc([it.class_name, it.subclass_name].filter(Boolean).join(" › ") || "–")],
          ["Gegenstandsstufe", it.item_level || "–"], ["Benötigte Stufe", it.required_level || "–"], ["Platz", esc(it.slot || "–")],
          ["Handelsware", it.is_commodity ? "ja (stapelweise gehandelt)" : "nein"], ["Händler-Verkaufspreis", it.vendor_sell ? money(it.vendor_sell) : "–"]]
          .map(([k, v]) => `<tr class="nohover"><td class="muted">${k}</td><td>${v}</td></tr>`).join("")}
      </tbody></table></div>`;
  };
  if (!house) { wireFolds({ rec: recFold, all: allTable, info: infoBox }); return; }

  $("#rng").onclick = (e) => { const b = e.target.closest("button[data-r]"); if (b) setParams({ range: b.dataset.r }); };
  $("#tog").onchange = () => {
    const sel = $$("#tog input:checked").map((x) => x.value);
    setParams({ show: sel.filter((x) => !["out", "cmp"].includes(x)).join(","), out: sel.includes("out") ? "" : "0", cmp: sel.includes("cmp") ? "1" : "" });
  };

  const anaRange = p.range === "24h" ? "7d" : p.range;
  const metric = p.metric || (house.last_median != null ? "median" : "min");
  const [hist, ana] = await Promise.all([
    api(`item/${id}/history`, { house: hid, range: p.range, max_points: 800 }),
    api(`item/${id}/analysis`, { house: hid, range: anaRange, metric, tz: TZ }),
  ]);
  const pts = hist.points;
  const span = pts.length ? pts[pts.length - 1].ts - pts[0].ts : 86400;

  // Preis-Chart (optional: alle Verbünde der Fraktion überlagert)
  const ds = [];
  if (overlay) {
    const members = [total, ...allSingles.map((h) => rowOf(h.id))].filter(Boolean);
    const series = await Promise.all(members.map((m) => api(`item/${id}/history`, { house: m.house_id, range: p.range, max_points: 400 })));
    members.forEach((m, i) => ds.push(line(GROUP_KINDS.includes(m.kind) ? `${fac.name} gesamt` : `Verbund ${m.verbund}`,
      series[i].points.map((x) => ({ x: x.ts, y: x.min, ts: x.ts })), SERIES[i], GROUP_KINDS.includes(m.kind) ? { borderWidth: 3 } : { borderWidth: 1.6 })));
  } else {
    for (const [k, l, c] of [["min", "Min", "--s1"], ["p10", "10 %-Perzentil", "--s7"], ["median", "Median", "--s2"], ["mean", "Mittel", "--s3"], ["max", "Max", "--s4"]]) {
      if (!shown.has(k) || !pts.some((x) => x[k] != null)) continue;
      ds.push(line(l, pts.map((x) => ({ x: x.ts, y: x[k], ts: x.ts })), c, k === "max" ? { borderDash: [4, 3], borderWidth: 1.5 } : {}));
    }
    if (p.out !== "0" && ana.outliers.length) {
      const lo = pts.length ? pts[0].ts : 0;
      const o = ana.outliers.filter((x) => x.ts >= lo);
      if (o.length) ds.push({ type: "scatter", label: "Ausreißer", data: o.map((x) => ({ x: x.ts, y: x.value, ts: x.ts })), pointRadius: 5, pointHoverRadius: 7,
        backgroundColor: css("--s8"), borderColor: css("--surface-1"), borderWidth: 2, pointStyle: "triangle" });
    }
  }
  $("#pcnote").textContent = overlay ? "Mindestpreis je Verbund · Klick auf einen Eintrag in der Legende blendet ihn aus" : `${hist.raw_points} Datenpunkte · Klick auf den Chart öffnet den Snapshot`;
  if (pts.length < 2) $("#pc").parentElement.innerHTML = `<div class="empty">${pts.length ? "Erst ein Datenpunkt – der Verlauf füllt sich stündlich." : "Keine Preisdaten im Zeitraum."}</div>`;
  else if (!ds.length) $("#pc").parentElement.innerHTML = '<div class="empty">Keine Kennzahl ausgewählt – über „Anzeige ▾“ einblenden.</div>';
  else timeLineChart($("#pc"), ds, { span, onClick: overlay ? undefined : (pt) => (location.hash = `/snapshot/${hid}/${pt.ts}?item=${id}`) });

  // Aufklappbare Abschnitte
  const enough = (house.last_ts || 0) - (house.first_ts || house.last_ts || 0) >= 2 * 86400;
  const t = enough ? ana.trend || {} : {}, v = enough ? ana.volatility || {} : {};
  wireFolds({
    rec: recFold,
    kpi: (el) => {
      el.innerHTML = `<div class="mini">
        <div class="kpi"><div class="l">Ø 7 Tage</div><div class="v">${money(house.avg7_min)}</div><div class="d">Ø 30 T ${money(house.avg30_min)}</div></div>
        <div class="kpi"><div class="l">30-Tage-Spanne</div><div class="v">${money(house.low30_min)}</div><div class="d">bis ${money(house.high30_min)}</div></div>
        <div class="kpi"><div class="l">Änderung 7 Tage</div><div class="v">${pct(house.chg7d_min)}</div><div class="d">24 h ${pct(house.chg24_min)}</div></div>
        <div class="kpi" title="Lineare Regression auf ln(Preis) über ${anaRange}"><div class="l">Trend</div><div class="v">${t.pct_per_day == null ? "–" : (t.pct_per_day > 0 ? "+" : "") + num(t.pct_per_day, 1) + " %/Tag"}</div><div class="d">${esc(t.direction || "ab 2 Tagen Daten")}</div></div>
        <div class="kpi" title="Tägliche Schwankung (Std.-Abw. der Log-Renditen)"><div class="l">Schwankung</div><div class="v">${v.daily == null ? "–" : num(v.daily * 100, 1) + " %/Tag"}</div><div class="d">${v.daily == null ? "ab 2 Tagen Daten" : v.daily < 0.03 ? "stabil" : v.daily < 0.1 ? "normal" : "stark"}</div></div>
        <div class="kpi"><div class="l">Auktionen</div><div class="v">${num(house.last_auctions)}</div><div class="d">Median ${money(house.last_median)}</div></div>
      </div>`;
    },
    qty: (el) => {
      if (!pts.length) { el.innerHTML = '<div class="empty">Keine Daten.</div>'; return; }
      el.innerHTML = '<div class="chartbox sm"><canvas></canvas></div>';
      makeChart($("canvas", el), {
        type: "bar",
        data: { datasets: [{ label: "Menge", data: pts.map((x) => ({ x: x.ts, y: x.quantity })), backgroundColor: css("--s1") + "99", borderRadius: 2, barPercentage: 1, categoryPercentage: 1 }] },
        options: {
          responsive: true, maintainAspectRatio: false, parsing: false,
          scales: { x: { type: "linear", ticks: { callback: timeTick(span), maxRotation: 0 }, grid: { display: false } }, y: { ticks: { callback: (v) => compact(v) } } },
          plugins: { legend: { display: false }, tooltip: { callbacks: { title: (i) => dt(i[0].parsed.x), label: (c) => ` Menge: ${num(c.parsed.y)}` } } },
        },
      });
    },
    season: (el) => {
      const se = ana.seasonality || {};
      el.innerHTML = `<div class="grid g2"><div><h3>Tageszeit</h3><div class="chartbox sm" id="hbox"><canvas id="hc"></canvas></div></div>
        <div><h3>Wochentag</h3><div class="chartbox sm" id="wbox"><canvas id="wc"></canvas></div></div></div>`;
      if (se.hourly) divergingBars($("#hc"), se.hourly.map((x) => x.hour + " h"), se.hourly.map((x) => x.pct ?? 0), "Tageszeit");
      else $("#hbox").innerHTML = `<div class="empty">Ab 2 Tagen stündlicher Daten.</div>`;
      if (se.weekday) divergingBars($("#wc"), se.weekday.map((x) => x.day), se.weekday.map((x) => x.pct ?? 0), "Wochentag");
      else $("#wbox").innerHTML = `<div class="empty">Ab 14 Tagen Historie.</div>`;
    },
    all: allTable,
    out: (el) => {
      el.innerHTML = table([
        { label: "Zeitpunkt", fmt: (o) => dt(o.ts) },
        { label: "Art", fmt: (o) => `<span class="${o.kind === "Spitze" ? "up" : "down"}">${o.kind === "Spitze" ? "▲" : "▼"} ${o.kind}</span>` },
        { label: "Wert", num: true, fmt: (o) => money(o.value) },
        { label: "Erwartet", num: true, fmt: (o) => money(o.expected) },
        { label: "Abw.", num: true, fmt: (o) => pct(o.deviation_pct, 0) }],
        ana.outliers.slice().reverse().slice(0, 10), { rowHref: (o) => `/snapshot/${hid}/${o.ts}?item=${id}`, empty: "Keine Ausreißer im Zeitraum." });
    },
    snaps: (el) => {
      const load = async (page) => {
        const s = await api(`item/${id}/snapshots`, { house: hid, page, page_size: 10 });
        el.innerHTML = table([
          { label: "Zeitpunkt", fmt: (x) => x.daily ? `${dt(x.ts, false)} <span class="pill" title="Mittel aus ${x.n} Snapshots">Tagesmittel</span>`
              : x.merged ? `${dt(x.ts)} <span class="pill" title="Mittel aus ${x.n} stündlichen Snapshots">Ø ${x.n} Std.</span>` : dt(x.ts) },
          { label: "Min", num: true, fmt: (x) => money(x.min) },
          { label: "Median", num: true, fmt: (x) => money(x.median) },
          { label: "Menge", num: true, fmt: (x) => num(x.quantity) },
          { label: "Δ Min", num: true, fmt: (x) => pct(x.chg_min) },
        ], s.rows, { rowHref: (x) => (x.daily || x.merged ? null : `/snapshot/${hid}/${x.ts}?item=${id}`) }) + pager(s.total, s.page, s.page_size, load);
      };
      return load(1);
    },
    info: infoBox,
  });
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
    <div class="crumbs"><a href="#/?house=${hid}">${esc(hname(houseById(hid)) || s.house.name)}</a> › Snapshots › ${dt(ts)}${f ? ` › <a href="#/item/${f.item.item_id}?house=${hid}">${esc(f.item.name)}</a>` : ""}</div>
    <div class="pagehead"><h1>Snapshot ${dt(ts)}</h1><span class="pill">${esc(hname(houseById(hid)) || s.house.name)}</span><span class="pill">${q ? "Tageswert" : "Stunden-Snapshot"}</span>
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
        <select id="hs" class="right">${houseOptions(house)}</select>
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
    <div class="pagehead"><h1>Top-Movers</h1><span class="sub">Größte Preisänderungen · ${esc(hname(h))}</span></div>
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
  return `<div class="card mt"><h2>Woher kommen die Preise?</h2>
    <ol class="flow">
      <li><b>Blizzard</b> veröffentlicht etwa stündlich alle Auktionen aller Auktionshäuser (Battle.net API).</li>
      <li><b>Zusammenfassen:</b> Je Fraktion (Allianz, Horde, Neutral) werden die Auktionshäuser aller Server-Verbünde gebündelt; jedes einzelne Haus bleibt zusätzlich sichtbar.</li>
      <li><b>Veröffentlichen:</b> Die Seite wird danach automatisch neu gebaut. Eine neue Auktion erscheint so nach etwa 1–2 Stunden.</li>
    </ol>
  </div>`;
}
async function viewData() {
  const [st, hs, lg] = await Promise.all([api("status"), api("houses"), api("import/log")]);
  const housesTable = () => table([
      { label: "Bezeichnung", fmt: (h) => `<span class="fac ${esc(h.faction)}"></span>${esc(hname(h))}` }, { label: "Blizzard-Name", fmt: (h) => `<span class="muted">${esc(h.kind === "haus" ? h.name : "–")}</span>` },
      { label: "Region", fmt: (h) => esc(h.region) }, { label: "Fraktion", fmt: (h) => esc(h.faction || "–") },
      { label: "Auflösung", fmt: (h) => (h.granularity === "day" ? "Tag" : "Stunde") },
      { label: "Snapshots", num: true, fmt: (h) => num(h.snapshots) }, { label: "Items", num: true, fmt: (h) => num(h.items) },
      { label: "Von", fmt: (h) => dt(h.first_ts) }, { label: "Bis", fmt: (h) => dt(h.last_ts) }],
      hs.houses, { rowHref: (h) => `/?house=${h.id}` });
  if (st.static) {
    main.innerHTML = `
    <div class="pagehead"><h1>Datenquellen</h1><span class="sub">Statische Version · Datenstand ${dt(st.generated_at)} (${ago(st.generated_at)})</span></div>
    <div class="card"><h2>Über diese Seite</h2>
      <p>Preisanalyse für das Auktionshaus von WoW: Forever (EU). Die Daten werden stündlich automatisch aktualisiert; Suche, Filter und Analysen laufen direkt im Browser. Historie: ${num(st.history_days)} Tage.</p>
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
      { label: "Bezeichnung", fmt: (h) => `<span class="fac ${esc(h.faction)}"></span>${esc(hname(h))}` }, { label: "Blizzard-Name", fmt: (h) => `<span class="muted">${esc(h.kind === "haus" ? h.name : "–")}</span>` },
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
      return `<a href="#/item/${it.item_id}${it.house_id ? "?house=" + it.house_id : ""}" data-i="${i}" class="acrow ${i === sel ? "sel" : ""}">
        ${icon(it, "md")}
        <span class="acmain"><span class="acname q${it.quality ?? 1}">${hl(it.name, lastQ)}</span>
          <span class="acsub">${en}${cat || "Kategorie unbekannt"} · ID ${it.item_id}</span></span>
        <span class="acprice">${it.no_ah ? '<span class="acsub">nicht im AH</span>' : `${money(it.last_min)}<span class="acsub">${it.last_qty != null ? num(it.last_qty) + " Stk." : ""}</span>`}</span></a>`;
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
  renderMarket();
}
function renderMarket() {
  const h = houseById(state.house);
  $$("#market button").forEach((b) => {
    b.hidden = !groupId(b.dataset.k);
    b.classList.toggle("on", !!h && h.kind === b.dataset.k);
  });
  const single = h && (h.kind === "haus" || h.kind === "scan");
  const hasGroups = !!(groupId("allianz") || groupId("horde") || groupId("neutral"));
  const sel = $("#houseSel");
  sel.innerHTML = state.houses.length
    ? (hasGroups ? '<option value="">Einzelnes AH …</option>' : "") + houseOptions(state.house, { markets: !hasGroups })
    : '<option value="">Keine Daten</option>';
  sel.value = single || !hasGroups ? state.house : "";
  sel.classList.toggle("on", !!single && hasGroups);
  updateFreshness();
}
function setHouse(id) {
  if (!id) return;
  state.house = String(id); store.set("house", state.house); classCache = {};
  renderMarket();
  const r = parseHash();
  const p = { ...r.params };
  delete p.house; delete p.page;
  go(r.path, p, true);
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
  $("#houseSel").onchange = (e) => setHouse(e.target.value || groupId(houseById(state.house)?.group || "allianz") || groupId("allianz"));
  $("#market").addEventListener("click", (e) => { const b = e.target.closest("button[data-k]"); if (b) setHouse(groupId(b.dataset.k)); });
  attachAutocomplete($("#quickInput"), $("#quickResults"), null, (it) => (location.hash = `/item/${it.item_id}${it.house_id ? "?house=" + it.house_id : ""}`));
  $("#quickSearch").onsubmit = (e) => { e.preventDefault(); const q = $("#quickInput").value.trim(); if (q) { $("#quickResults").hidden = true; go("/search", { q, house: state.house }); } };
  window.addEventListener("hashchange", route);
  route();
})();
