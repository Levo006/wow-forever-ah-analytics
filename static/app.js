/* Forever AH-Analytics – Single-Page-Frontend (ohne Build-Schritt). */
"use strict";

// ================================================================ Basics
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const t = (s) => AHI18N.t(s);
const tf = (s, ...args) => AHI18N.tf(s, ...args);
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
    const data = await r.json().catch(() => ({ error: t("Ungültige Antwort") }));
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
  if (g) out += `<span class="g">${g.toLocaleString(AHI18N.loc())}</span><i class="g">g</i>`;
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
  if (g >= 100) return `${g.toLocaleString(AHI18N.loc())}g`;
  if (g) return `${g}g ${s}s`;
  if (s) return `${s}s ${k}c`;
  return `${k}c`;
}
const num = (v, d = 0) => (v == null || isNaN(v) ? "–" : Number(v).toLocaleString(AHI18N.loc(), { maximumFractionDigits: d, minimumFractionDigits: d }));
const compact = (v) => (v == null ? "–" : Intl.NumberFormat(AHI18N.loc(), { notation: "compact", maximumFractionDigits: 1 }).format(v));
function pct(v, d = 1) {
  if (v == null || isNaN(v)) return '<span class="muted">–</span>';
  const cls = v > 0.05 ? "up" : v < -0.05 ? "down" : "muted";
  const arrow = v > 0.05 ? "▲" : v < -0.05 ? "▼" : "■";
  return `<span class="chg ${cls}">${arrow} ${v > 0 ? "+" : ""}${num(v, d)} %</span>`;
}
const dt = (ts, withTime = true) => ts ? new Date(ts * 1000).toLocaleString(AHI18N.loc(), withTime ? { day: "2-digit", month: "2-digit", year: "2-digit", hour: "2-digit", minute: "2-digit" } : { day: "2-digit", month: "2-digit", year: "numeric" }) : "–";
function ago(ts) {
  if (!ts) return "–";
  const s = Date.now() / 1000 - ts;
  if (s < 3600) return tf("vor {0} Min.", Math.max(1, Math.round(s / 60)));
  if (s < 86400) return tf("vor {0} Std.", Math.round(s / 3600));
  return tf("vor {0} Tagen", Math.round(s / 86400));
}
const QUAL = (q) => AHI18N.qual(q);
const QUAL_ABBR = (q) => AHI18N.qualAbbr(q);

function iconUrl(icon) {
  if (!icon) return null;
  if (/^https?:/.test(icon)) return icon;
  return `https://wow.zamimg.com/images/wow/icons/medium/${encodeURIComponent(icon)}.jpg`;
}
function icon(it, size = "") {
  const q = it.quality ?? 1;
  const url = iconUrl(it.icon);
  const letter = esc((AHI18N.dispName(it) || "?").replace(/^Item #/, "#").slice(0, 1));
  const cls = `ico ${size === true ? "big" : size || ""} bq${q}`;
  if (!url) return `<span class="${cls}">${letter}</span>`;
  return `<img class="${cls}" src="${url}" alt="" loading="lazy" onerror="this.outerHTML='<span class=&quot;${cls}&quot;>${letter}</span>'">`;
}
function itemLabel(it, link = true) {
  const id = it.item_id ?? it.id;
  const name = `<span class="q${it.quality ?? 1}">${esc(AHI18N.dispName(it) || "Item #" + id)}${it.translation_missing && AHI18N.get() === "de" ? `<span class="muted small listing-status">${it.aliases?.length ? esc(it.aliases.join(", ")) + " " + t("(Suchalias)") + " · " : ""}${t("DE-Name unbestätigt")}</span>` : ""}</span>`;
  const inner = `${icon(it)}${name}${it.catalog_test ? `<span class="pill small">${t("Client-Testhinweis")}</span>` : ""}`;
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
  snapshot: viewSnapshot, compare: viewCompare, watchlist: viewWatchlist, liquidity: viewLiquidity, data: viewData,
};
// Alte Lesezeichen: Top-Movers und Deals sind in der Übersicht aufgegangen.
const REDIRECTS = { movers: "/", deals: "/" };

async function route() {
  const r = parseHash();
  state.route = r;
  if (r.params.house) r.params.house = resolveHouseId(r.params.house);
  if (r.params.house && houseById(r.params.house) && String(r.params.house) !== String(state.house)) {
    state.house = String(r.params.house); store.set("house", state.house);
    classCache = {};
    renderMarket();
  }
  inflight.forEach((c) => c.abort()); inflight.clear();
  state.charts.forEach((c) => c.destroy()); state.charts = [];
  const key = r.parts[0] || "";
  if (REDIRECTS[key]) { go(REDIRECTS[key], {}, true); return; }
  $$("#nav a").forEach((a) => a.classList.toggle("active", (a.dataset.route === (key || "dashboard"))));
  const view = routes[key] || viewNotFound;
  main.innerHTML = `<div class="loading">${t("Lade …")}</div>`;
  try {
    await view(r);
  } catch (e) {
    if (e.name === "AbortError") return;
    main.innerHTML = `<div class="error"><b>${t("Fehler:")}</b> ${esc(e.message)}</div>` +
      (/Keine Daten/.test(e.message) ? `<p class="mt"><a class="btn primary" href="#/data">${t("Zu den Datenquellen")}</a></p>` : "");
  }
}
function viewNotFound() { main.innerHTML = `<div class="empty">${t("Seite nicht gefunden.")}</div>`; }
const curHouse = (p) => resolveHouseId(p?.house || state.house || "");
const houseById = (id) => state.houses.find((h) => String(h.id) === String(id));
const hname = (h) => (h ? AHI18N.houseName(h) : "");
const rulesetKey = () => houseById(state.house)?.ruleset_key || "normal";
const ruleName = (h) => h ? (AHI18N.get() === "en" ? h.ruleset_label_en : h.ruleset_label) || "Normal" : "Normal";
let legacyAliases = {};
const resolveHouseId = (id) => legacyAliases[String(id)] || id;
const groupId = (k) => state.houses.find((h) => h.group === k && h.ruleset_key === rulesetKey())?.id;
/** <option>s mit Gruppen: Märkte, Regelsatz A, B, …, eigene Scans. */
function houseOptions(selected, { markets = true } = {}) {
  const groups = [];
  for (const key of ["normal", "pvp", "rp", "rppvp"]) {
    const hs = state.houses.filter(h=>h.ruleset_key===key);
    if (hs.length) groups.push([ruleName(hs[0]), hs]);
  }
  return groups.map(([label, hs])=>`<optgroup label="${esc(label)}">${hs.map(h=>`<option value="${h.id}" ${String(h.id)===String(selected)?"selected":""}>${esc(AHI18N.faction(h.faction))}</option>`).join("")}</optgroup>`).join("");
}

function scopeText(h) {
  if (!h) return "";
  return `${ruleName(h)} · ${AHI18N.faction(h.faction)} · ${t("Handel nur innerhalb desselben Regelsatzes.")}`;
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
    if (span <= 2 * 86400) return d.toLocaleTimeString(AHI18N.loc(), { hour: "2-digit", minute: "2-digit" });
    if (span <= 60 * 86400) return d.toLocaleDateString(AHI18N.loc(), { day: "2-digit", month: "2-digit" }) + (span <= 4 * 86400 && d.getHours() ? " " + d.getHours() + "h" : "");
    return d.toLocaleDateString(AHI18N.loc(), { month: "short", year: "2-digit" });
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
  const gaps = [];
  const steps = data.slice(1).map((p,i)=>p.x-data[i].x).filter(x=>x>0).sort((a,b)=>a-b);
  const typical = steps.length ? steps[Math.floor(steps.length/2)] : 3600;
  // Gemittelte Tages-/6-h-Zeilen (n>1) folgen ihrem groben Raster: großer Schritt = Verdichtungsstufe, keine Lücke.
  // Nur echte mehrtägige Lücken trennen; im feinen Stundenraster gilt die bisherige 2,5×-Regel.
  const coarse = (r) => (r.n ?? 1) > 1;
  for (const p of data) {
    const prev = gaps[gaps.length-1];
    if (prev && p.x-prev.x > (coarse(prev) || coarse(p) ? 3*86400 : Math.max(3*3600,typical*2.5))) gaps.push({x:prev.x+1,y:null});
    gaps.push(p);
  }
  data = gaps;
  return { label, data, borderColor: c, backgroundColor: c, borderWidth: 2, pointRadius: 0, pointHoverRadius: 4, tension: 0.15, spanGaps: false, ...extra };
}
function divergingBars(canvas, labels, values, title) {
  const pos = css("--div-pos"), neg = css("--div-neg");
  return makeChart(canvas, {
    type: "bar",
    data: { labels, datasets: [{ label: title, data: values, backgroundColor: values.map((v) => (v >= 0 ? pos : neg)), borderRadius: 4, borderSkipped: false, maxBarThickness: 26 }] },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false }, tooltip: { callbacks: { label: (c) => ` ${c.parsed.y > 0 ? "+" : ""}${num(c.parsed.y, 2)} % ${t("ggü. normal")}` } } },
      scales: { y: { ticks: { callback: (v) => v + " %" } }, x: { grid: { display: false } } },
    },
  });
}

// ================================================================ Tabellen
/** Generische sortierbare Tabelle. cols: {key,label,num,sort,fmt(row)} */
function table(cols, rows, { sort, dir, onSort, rowHref, empty = t("Keine Einträge.") } = {}) {
  if (!rows.length) return `<div class="empty">${empty}</div>`;
  const th = cols.map((c) => {
    const s = c.sort ? `sortable ${sort === c.sort ? "sorted" + (dir === "asc" ? " asc" : "") : ""}` : "";
    return `<th class="${c.num ? "n" : ""} ${s}" ${c.sort ? `data-sort="${c.sort}"` : ""} ${c.title ? `title="${esc(c.title)}"` : ""}>${c.label}</th>`;
  }).join("");
  const tb = rows.map((r, i) => { const href = rowHref && rowHref(r); return `<tr data-i="${i}" ${href ? `data-href="${esc(href)}"` : 'class="nohover"'}>`; }).map((open, i) => open +
    cols.map((c) => `<td data-label="${esc(c.label || "")}" class="${c.num ? "n" : ""} ${c.cls || ""}">${c.fmt ? c.fmt(rows[i]) : esc(rows[i][c.key])}</td>`).join("") + "</tr>").join("");
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
  return `<div class="pager" id="${id}">${tf("{0} Einträge · Seite {1} / {2}", num(total), page, pages)}
    <button class="btn small" data-p="${page - 1}" ${page <= 1 ? "disabled" : ""}>${t("‹ Zurück")}</button>
    <button class="btn small" data-p="${page + 1}" ${page >= pages ? "disabled" : ""}>${t("Weiter ›")}</button></div>`;
}

function listingNote(r) {
  if (r.no_ah) return r.has_history ? t("Keine Preisdaten im gewählten Markt") : t("Noch keine Preisbeobachtung");
  if (r.is_current === false) return r.availability === "not_observed" ? t("Im letzten Snapshot keine Preisbeobachtung") : t("Historischer Preis · Angebot unbestätigt");
  return `${t("Im letzten Snapshot beobachtet")} · ${dt(r.last_ts)}`;
}
function listingPrice(r) {
  return `${money(r.last_min)}${r.is_current === false || r.no_ah ? `<span class="muted small listing-status">${esc(listingNote(r))}${r.last_ts ? " · " + t("zuletzt") + " " + dt(r.last_ts) : ""}</span>` : ""}`;
}
const COL = {
  item: { label: "Item", sort: "name", cls: "itemcell", fmt: (r) => itemLabel(r) },
  house: { label: t("Markt"), fmt: (r) => `<span class="pill">${esc(houseById(r.house_id) ? AHI18N.houseShort(houseById(r.house_id)) : r.house_name)}</span>` },
  price: { label: t("Referenzpreis"), num: true, sort: "price", fmt: (r) => listingPrice(r) },
  median: { label: t("Median"), num: true, sort: "median", fmt: (r) => money(r.last_median) },
  avg7: { label: t("Ø Referenz 7 T"), num: true, fmt: (r) => money(r.avg7_min) },
  avg30: { label: t("Ø Referenz 30 T"), num: true, sort: "avg30", fmt: (r) => money(r.avg30_min) },
  qty: { label: t("Menge"), num: true, sort: "qty", fmt: (r) => r.is_current === false ? "–" : num(r.last_qty) },
  avgqty: { label: t("Ø Menge 7 T"), num: true, sort: "avg_qty", fmt: (r) => num(r.avg7_qty) },
  chg24: { label: "Δ 24 h", num: true, sort: "chg24", fmt: (r) => r.is_current === false ? "–" : pct(r.chg24_min) },
  chg7d: { label: "Δ 7 T", num: true, sort: "chg7d", fmt: (r) => r.is_current === false ? "–" : pct(r.chg7d_min) },
  vol: { label: t("Volatilität"), num: true, sort: "vol", title: t("Variationskoeffizient des Referenzpreises (7 Tage)"), fmt: (r) => r.cv7_min == null ? "–" : num(r.cv7_min * 100, 1) + " %" },
  value: { label: t("Marktwert"), num: true, sort: "value", title: t("Referenzpreis × angebotene Menge"), fmt: (r) => money(r.market_value, { short: true }) },
  spark: { label: t("14 Tage"), fmt: (r) => sparkline(r.spark) },
  updated: { label: t("Stand"), sort: "updated", fmt: (r) => `<span class="muted small">${dt(r.last_ts)}</span>` },
  cmp: { label: "", fmt: (r) => `<button class="btn small" data-cmp="${r.item_id}" title="${t("Zum Vergleich hinzufügen")}">${state.compare.includes(r.item_id) ? "✓" : "+"} ${t("Vgl.")}</button>` },
  liq: { label: t("Angebotssignale"), fmt: (r) => liqBadge(r) },
}

/** Liquiditäts-Badge: Signale aus Angebotsbewegungen, nie ein Verkaufsnachweis. */
function liqBadge(x) {
  if (x.liq_score === null || x.liq_score === undefined)
    return `<span class="pill" title="${t("Für dieses Haus liegen noch keine Umsatzsignale vor - die Auswertung entsteht aus den stündlichen Marktläufen.")}">${t("Signale unbekannt")}</span>`;
  const tip = tf("{0} Umsatzsignale (davon {1} Ausverkäufe) in 7 Tagen · {2} % der geprüften Stunden gelistet · geschätzt aus Angebotsbewegungen, kein Verkaufsnachweis", num(x.liq_events), num(x.liq_vanish), num((x.liq_presence || 0) * 100, 0));
  if ((x.liq_events || 0) <= 1)
    return `<span class="pill liq-l" title="${tip}">${t("kaum Angebotssignale")}</span>`;
  if (x.liq_score >= 60) return `<span class="pill liq-h" title="${tip}">${t("viele Angebotssignale")}</span>`;
  if (x.liq_score >= 30) return `<span class="pill liq-m" title="${tip}">${t("mittlere Angebotssignale")}</span>`;
  return `<span class="pill liq-l" title="${tip}">${t("wenige Angebotssignale")}</span>`;
}
document.addEventListener("click", (e) => {
  const b = e.target.closest("button[data-cmp]");
  if (!b) return;
  e.stopPropagation();
  toggleCompare(+b.dataset.cmp);
  b.textContent = (state.compare.includes(+b.dataset.cmp) ? "✓" : "+") + " " + t("Vgl.");
}, true);
function toggleCompare(id) {
  const i = state.compare.indexOf(id);
  if (i >= 0) state.compare.splice(i, 1);
  else {
    if (state.compare.length >= 8) { toast(t("Maximal 8 Items im Vergleich.")); return; }
    state.compare.push(id);
    toast(t("Zum Vergleich hinzugefügt."), 1500);
  }
  store.set("compare", state.compare);
  updateCompareBadge();
}
function updateCompareBadge() {
  const b = $("#cmpCount");
  b.hidden = !state.compare.length;
  b.textContent = state.compare.length;
}

// Private Merkliste: nur Item-IDs im Browser, Preise immer im gewaehlten Markt.
function watchIds() {
  const saved = store.get("watchlist", []);
  return Array.isArray(saved) ? [...new Set(saved.filter(n => Number.isSafeInteger(n) && n > 0))].slice(0, 30) : [];
}
document.addEventListener("click", e => {
  const b = e.target.closest("button[data-watch]");
  if (!b) return;
  e.preventDefault();
  const id = Number(b.dataset.watch), ids = watchIds();
  if (!Number.isSafeInteger(id) || id < 1) return;
  if (ids.includes(id)) ids.splice(ids.indexOf(id), 1);
  else { if (ids.length >= 30) { toast(t("Maximal 30 Items auf der Merkliste.")); return; } ids.push(id); }
  store.set("watchlist", ids);
  if (state.route?.parts[0] === "watchlist") route();
  else b.textContent = ids.includes(id) ? "★ " + t("Gemerkt") : "☆ " + t("Merken");
});
async function viewWatchlist(r) {
  const house = curHouse(r.params), ids = watchIds();
  main.innerHTML = `<div class="pagehead"><h1>${t("Merkliste")}</h1><span class="sub">${esc(hname(houseById(house)))}</span></div>
    <p class="note">${t("Nur in diesem Browser gespeichert. Keine Anmeldung, keine Benachrichtigungen. Auf Item-Seiten mit dem Stern merken, bis zu 30 Items.")}</p>
    <div class="card flush" id="watchRows"><div class="loading">${t("Lade …")}</div></div>`;
  if (!ids.length) { $("#watchRows").innerHTML = `<div class="empty">${t("Noch keine Items gemerkt.")} <a href="#/search">${t("Item suchen")}</a></div>`; return; }
  const result = await api("search", {house, item_ids:ids.join(","), page_size:30});
  if (state.route !== r) return;
  const rows = ids.map(id => result.items.find(x => x.item_id === id) || {item_id:id, name:`Item #${id}`, no_ah:true});
  $("#watchRows").innerHTML = table([{...COL.item, sort:null}, {...COL.price, sort:null}, {...COL.qty, sort:null}, {...COL.chg24, sort:null},
    {label:t("Stand"), fmt:x=>x.last_ts ? ago(x.last_ts) : "–"},
    {label:"", fmt:x=>`<button class="btn small" data-watch="${x.item_id}" aria-label="${t("Entfernen")}: ${esc(AHI18N.dispName(x))}">×</button>`}], rows,
    {rowHref:x=>`/item/${x.item_id}?house=${house}`});
}

// Ein zufälliger Händler pro Seite, angedockt an das Layout. Kein Pop-up, kein Klick.
const GOBLIN_MOODS = [
  ["merchant", ["Willste meine Waren sehen?", "Wenn ichs nicht hab, willstes auch nicht."]],
  ["coin", ["Ah, potenzieller Kunde ...", "Zeit ist Geld, Freund!"]],
  ["courier", ["Zeit ist Geld, Freund!", "Nichts gefunden? Ich schau noch mal im Lager nach."]],
  ["discount", ["Mach ich dir n Angebot!", "Hab das Beste im Angebot, wo gibt!"]],
  ["sword", ["Hab das Beste im Angebot, wo gibt!", "Für jedes Abenteuer die passenden Waren."]],
  ["potions", ["Für jedes Abenteuer die passenden Waren.", "Für jeden Kampf den richtigen Trank."]],
  ["treasure", ["Cha-ching!", "Ich erkenn einen Käufer, wenn ich einen sehe."]],
  ["alchemist", ["Für jeden Kampf den richtigen Trank.", "Ah, potenzieller Kunde ..."]],
];
const goblinPick = (arr) => arr[Math.floor(Math.random() * arr.length)];
function goblinScene(extra = "") {
  const [image, quotes] = goblinPick(GOBLIN_MOODS);
  return `<figure class="goblin-ambient ${extra}" aria-label="${t("Goblin-Händler")}"><figcaption>${esc(t(goblinPick(quotes)))}</figcaption><img src="assets/goblins/${image}.webp" alt="" width="480" height="320" loading="lazy" decoding="async"></figure>`;
}

// ============================================================= Übersicht
/** Große Suche oben auf der Startseite (sofort sichtbar, Übersicht lädt darunter). */
function homeSearch(house) {
  const ex = AHI18N.get() === "en" ? ["Copper Ore", "Silk Cloth", "Recipe: Elixir", "Pattern: Linen", "Tough Wolf Meat"] : ["Kupfererz", "Seidenstoff", "Rezept: Elixier", "Muster: Leinen", "Zähes Wolfsfleisch"];
  return `<form class="card hero" id="heroForm" role="search">
      <div class="hero-copy"><h1>${t("Was suchst du?")}</h1>
      <div class="herobox"><input id="heroInput" type="search" name="q" autocomplete="off" placeholder="${t("Item-Name (DE/EN) oder ID")}" aria-label="${t("Item suchen")}">
        <button class="btn primary">${t("Suchen")}</button><div id="heroResults" class="ac hero-ac" hidden></div></div>
      <div class="heroex muted small">${ex.map((q) => `<a href="#/search?q=${encodeURIComponent(q)}${house ? "&house=" + house : ""}">${esc(q)}</a>`).join(" · ")}</div></div>
      ${goblinScene("hero-goblin")}
    </form>
    <div class="adventure-links" aria-label="${t("Schnelleinstieg")}">
      <a href="#/search${house ? "?house=" + house : ""}" class="adventure-link"><span class="adventure-icon">⌕</span><span><b>${t("Schatzsuche")}</b></span></a>
      <a href="#/liquidity${house ? "?house=" + house : ""}" class="adventure-link"><span class="adventure-icon">↗</span><span><b>${t("Marktkompass")}</b></span></a>
      <a href="#/categories?class=7${house ? "&house=" + house : ""}" class="adventure-link"><span class="adventure-icon">◇</span><span><b>${t("Für deine Berufe")}</b></span></a>
    </div>
    <p class="watch-entry"><a class="btn" href="#/watchlist">☆ ${t("Merkliste")}${watchIds().length ? " · " + watchIds().length : ""}</a></p>
    <div id="dash"><div class="loading">${t("Lade Marktübersicht …")}</div></div>`;
}

async function viewDashboard(r) {
  const house = curHouse(r.params);
  main.innerHTML = homeSearch(house) + `<div id="newsTop" class="mt"></div>`;
  renderNews($("#newsTop"));
  const input = $("#heroInput");
  attachAutocomplete(input, $("#heroResults"), house, (it) => (location.hash = `/item/${it.item_id}${it.house_id ? "?house=" + it.house_id : ""}`));
  $("#heroForm").onsubmit = (e) => { e.preventDefault(); const q = input.value.trim(); if (q) { $("#heroResults").hidden = true; go("/search", { q, house }); } };
  if (!matchMedia("(pointer: coarse)").matches) input.focus();   // am Handy keine Tastatur aufklappen
  const d = await api("overview", { house });
  const k = d.kpis, h = d.house;
  const hh = houseById(h.id) || h;
  $("#dash").innerHTML = `
    <div class="pagehead market-heading"><div><div class="eyebrow">${t("DER MARKT AUF EINEN BLICK")}</div><h1>${esc(hname(hh))}</h1></div>
      <span class="sub">${esc(scopeText(hh))} · ${t("Stand")} ${dt(d.last_ts)} (${ago(d.last_ts)})</span></div>
    <div class="kpis kpis3">
      <div class="kpi"><div class="l">${t("Beobachtete Items")}</div><div class="v">${num(k.items)}</div></div>
      <div class="kpi"><div class="l">${t("Angebotswert (Referenz × Menge)")}</div><div class="v">${money(k.market_value, { short: true })}</div></div>
      <div class="kpi"><div class="l">${t("Median Preisänderung 24 h")}</div><div class="v">${k.comparisons_reliable ? pct(k.median_chg24) : "–"}</div></div>
    </div>
    <div class="secthead mt"><h2><span class="section-star" aria-hidden="true">✦</span> ${t("Im Fokus")}</h2></div>
    <details class="focus-method small muted"><summary>${t("Wie wird ausgewaehlt?")}</summary><p>${t("Verbrauchbares (ohne Sonstiges) und Handwerkswaren mit aktuellem vollstaendigem Marktstand: ab 1 Silber, 5 Stueck, 24 Beobachtungen, 5 Angebotssignale und 50 % Praesenz. Extreme Aenderungen ueber 100 % und Rabatte ueber 75 % werden hier ausgeblendet, nicht gekappt. Originalwerte bleiben am Item. Keine Kaufempfehlung oder Verkaufsgarantie.")}</p></details>
    <div class="grid g2">
      <div class="card flush"><div class="cardhead" style="padding:14px 16px 0"><h2>${t("▲ Steigt")}</h2><span class="right small muted">24 h</span></div>${moversMini(d.movers.gainers.slice(0, 6))}</div>
      <div class="card flush"><div class="cardhead" style="padding:14px 16px 0"><h2>${t("Unter dem Wochenschnitt")}</h2><span class="right small muted" title="${t("Mindestens 15 % unter dem 7-Tage-Schnitt und mit beobachtetem Umsatz")}">${t("≥ 15 % unter Ø 7 T · Angebotssignale")}</span></div>
        ${table([{ ...COL.item, sort: null }, { label: t("Preis"), num: true, fmt: (x) => money(x.last_min) }, { label: t("Rabatt"), num: true, fmt: (x) => `<span class="chg up">−${num(x.discount, 0)} %</span>` }],
          d.deals.deals.slice(0, 6), { rowHref: (x) => `/item/${x.item_id}?house=${x.house_id}`, empty: t("Aktuell keine passenden Angebote mit Angebotssignalen.") })}</div>
    </div>
    <details class="card more-analysis mt" id="moreAn"><summary>${t("Mehr Analyse")}</summary>
      <div class="grid g32 mt">
        <div class="card"><div class="cardhead"><h2>${t("Beobachteter Angebotswert")}</h2></div>
          <div class="chartbox md"><canvas id="mkt"></canvas></div></div>
        <div class="card flush"><h2>${t("Kategorien")}</h2>${table([
          { label: t("Kategorie"), fmt: (c) => esc(AHI18N.trClass(c.class_name)) },
          { label: "Items", num: true, fmt: (c) => num(c.items) },
          { label: t("Marktwert"), num: true, fmt: (c) => money(c.market_value, { short: true }) },
        ], d.categories.slice(0, 9), { rowHref: (c) => `/categories?class=${c.class_id}&house=${h.id}` })}</div>
      </div>
      <div class="grid g2 mt"><div class="card flush"><div class="cardhead" style="padding:14px 16px 0"><h2>${t("▼ Fällt")}</h2><span class="right small muted">24 h</span></div>${moversMini(d.movers.losers.slice(0, 6))}</div>
        <div class="card"><p class="note">${esc(marketQualityNote(d.quality))} <a href="#/data">${t("Laufprotokoll & Quellen")}</a></p>
        <p class="note">${t("Was sich wirklich lohnt, hängt vom Umsatz ab:")} <a href="#/liquidity?house=${h.id}">${t("Zur Liquiditäts-Auswertung ›")}</a></p></div></div>
    </details>`;
  const s = d.market_series;
  const drawChart = () => {
    if (s.length > 1) {
      timeLineChart($("#mkt"), [line(t("Marktwert"), s.map((p) => ({ x: p.ts, y: p.total_value })), "--s1", { fill: false })],
        { span: s[s.length - 1].ts - s[0].ts, yFmt: moneyText, minY: 0 });
    } else $("#mkt").parentElement.innerHTML = `<div class="empty">${t("Zu wenige Snapshots für einen Verlauf.")}</div>`;
  };
  const more = $("#moreAn");
  let drawn = false;
  more.addEventListener("toggle", () => { if (more.open && !drawn) { drawn = true; drawChart(); } });
}
// Newsboard: eigener Abschnitt über der Suche – kuratierte Gold-Guides + jüngste WoW-Forever-/Blue-News
// (data/news.json aus dem stündlichen Export; fehlt die Datei, bleibt der Platz leer).
const NEWS_ICONS = {
  guide: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="4"/></svg>',
  blue: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4.5 4.5h15v11h-10l-5 4z"/></svg>',
  news: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4.5 5h12v14h-12z"/><path d="M16.5 8.5h3V19h-3"/><path d="M7.5 9h6M7.5 12.5h6"/></svg>',
};
async function renderNews(slot) {
  let d;
  try {
    const r = await fetch("data/news.json", { cache: "no-cache" });
    if (!r.ok) return;
    d = await r.json();
  } catch { return; }
  const guides = (d.guides || []).slice(0, 3);
  const seenNews = new Set();
  const items = (d.items || []).filter(it => { const key = it.title.toLowerCase().replace(/^\[(eu|us)\]\s*/i, "").trim(); if (seenNews.has(key)) return false; seenNews.add(key); return true; }).slice(0, 4);
  if (!guides.length && !items.length) return;
  const row = (it) => {
    const kind = it.kind === "guide" ? "guide" : it.kind === "blue" ? "blue" : "news";
    return `<a class="newsrow ${kind}" href="${esc(it.url)}" target="_blank" rel="noopener noreferrer"><span class="newsico">${NEWS_ICONS[kind]}</span><span class="newsrow-text"><b>${esc(it.title)}</b><small>${esc(it.source)}${it.published_ts ? " · " + ago(it.published_ts) : ""}</small></span></a>`;
  };
  const col = (title, list) => list.length ? `<div class="newsboard-col"><div class="newsboard-sub">${t(title)}</div>${list.map(row).join("")}</div>` : "";
  const openAttr = store.get("newsOpen", false) ? " open" : "";
  slot.innerHTML = `<details class="newsboard"${openAttr}><summary class="newsboard-head"><span class="newsboard-title"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 10v4h3l5 4V6L7 10H4z"/><path d="M15.5 9.5a4 4 0 0 1 0 5"/></svg>${t("Neuigkeiten & Gold-Guides")}</span><span class="newsboard-side"><span class="newsboard-note muted small">${t("Externe Quellen, stündlich aktualisiert")}</span><span class="news-caret" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="m6 9 6 6 6-6"/></svg></span></span></summary>
    <div class="newsboard-cols">${col("Gold-Guides", guides)}${col("Aktuelle News", items)}</div></details>`;
  slot.querySelector("details").addEventListener("toggle", e => store.set("newsOpen", e.target.open));
}

function moversMini(rows) {
  return table([{ ...COL.item, sort: null }, { label: t("Preis"), num: true, fmt: (x) => money(x.last_min) }, { label: "Δ", num: true, fmt: (x) => pct(x.chg24_min ?? x.chg7d_min) }],
    rows, { rowHref: (x) => `/item/${x.item_id}?house=${x.house_id}`, empty: t("Keine Änderungen.") });
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
    <div class="goblin-heading"><div class="pagehead"><h1>${t("Item-Suche")}</h1><span class="sub" id="sumline"></span></div>${goblinScene("head-goblin")}</div>
    <form class="card filters" id="ff">
      <label class="f"><span>${t("Name / ID")}</span><input type="search" name="q" value="${esc(p.q || "")}" placeholder="${t("Deutsch oder Englisch, z. B. Silberblatt, Silverleaf, 765")}"></label>
      <label class="f"><span>${t("Klasse")}</span><select name="class"><option value="">${t("Alle")}</option>${classes.map((c) => `<option value="${c.class_id}" ${String(p.class) === String(c.class_id) ? "selected" : ""}>${esc(AHI18N.trClass(c.class_name))} (${c.items})</option>`).join("")}</select></label>
      <label class="f"><span>${t("Unterklasse")}</span><select name="subclass" ${subs.length ? "" : "disabled"}><option value="">${t("Alle")}</option>${subs.map((s) => `<option value="${s.subclass_id}" ${String(p.subclass) === String(s.subclass_id) ? "selected" : ""}>${esc(AHI18N.trSubclass(s.subclass_name))} (${s.items})</option>`).join("")}</select></label>
      <div class="f"><span>${t("Qualität")}</span><div class="qual">${[0, 1, 2, 3, 4, 5].map((q) => `<label class="q${q}" title="${QUAL(q)}"><input type="checkbox" name="quality" value="${q}" ${quals.includes(String(q)) ? "checked" : ""}><span>${QUAL_ABBR(q)}</span></label>`).join("")}</div></div>
      <div class="f"><span>${t("Preis in Gold")}</span><div class="range"><input type="number" step="any" min="0" name="min_price" value="${gold(p.min_price)}" placeholder="${t("von")}">–<input type="number" step="any" min="0" name="max_price" value="${gold(p.max_price)}" placeholder="${t("bis")}"></div></div>
      <div class="f"><span>${t("Menge (aktuell)")}</span><div class="range"><input type="number" min="0" name="min_qty" value="${esc(p.min_qty || "")}" placeholder="${t("von")}">–<input type="number" min="0" name="max_qty" value="${esc(p.max_qty || "")}" placeholder="${t("bis")}"></div></div>
      <label class="f"><span>${t("Ø Menge 7 T ≥")}</span><input type="number" min="0" name="min_avg_qty" value="${esc(p.min_avg_qty || "")}"></label>
      <label class="f"><span>${t("Markt")}</span><select name="house">${houseOptions(p.house)}</select></label>
      <label class="f"><span>${t("Typ")}</span><select name="commodity"><option value="">${t("Alle")}</option><option value="1" ${p.commodity === "1" ? "selected" : ""}>${t("Handelswaren")}</option><option value="0" ${p.commodity === "0" ? "selected" : ""}>${t("Einzelitems")}</option></select></label>
      <div class="f"><span>&nbsp;</span><div style="display:flex;gap:6px"><button class="btn primary">${t("Filtern")}</button><button class="btn" type="button" id="reset">${t("Zurücksetzen")}</button></div></div>
    </form>
    <div class="card flush" id="res"><div class="loading">${t("Suche …")}</div></div>`;
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
  $("#sumline").textContent = tf("{0} Treffer", num(res.total));
  $("#res").innerHTML = table([COL.item, COL.price, COL.median, COL.avg30, COL.qty, COL.chg24, COL.chg7d, COL.liq, COL.value, COL.spark, COL.cmp], res.items, {
    sort: p.sort, dir: p.dir, onSort: (s, d) => setParams({ sort: s, dir: d, page: 1 }),
    rowHref: (x) => `/item/${x.item_id}${x.house_id ? "?house=" + x.house_id : ""}`, empty: t("Keine Items passen zu den Filtern."),
  }) + pager(res.total, res.page, res.page_size, (pg) => setParams({ page: pg }));
}

// ============================================================ Kategorien
// Filter je Kategorie: Berufe (Item ist Zutat oder Ergebnis eines Rezepts),
// Stufe (benötigte Stufe), Spanne (lehrt Rezept mit Gewinn); Qualität und
// „nur gelistet“ gibt es überall. Beruf-IDs aus data/recipes.json.
const CAT_FILTERS = {
  0: { profs: [171, 164, 202, 333, 185, 129] },                    // Verbrauchbar
  7: { profs: [171, 164, 202, 165, 197, 333, 185, 129, 186] },     // Handwerkswaren
  9: { margin: true },                                             // Rezepte
  2: { lvl: true },                                                // Waffen
  4: { lvl: true },                                                // Rüstung
};
const CAT_PROF_NAMES = { 171: "Alchimie", 164: "Schmiedekunst", 202: "Ingenieurskunst", 165: "Lederverarbeitung",
  197: "Schneiderei", 333: "Verzauberkunst", 185: "Kochkunst", 129: "Erste Hilfe", 186: "Bergbau" };

function catFilterBar(cls, p, ridx) {
  const cfg = CAT_FILTERS[cls.class_id] || {};
  const groups = [];
  if (cfg.profs?.length && ridx) {
    const avail = new Set(ridx.professions || []);
    const sel = new Set((p.prof || "").split(",").filter(Boolean));
    const chips = cfg.profs.filter((id) => avail.has(CAT_PROF_NAMES[id])).map((id) =>
      `<button type="button" class="${sel.has(String(id)) ? "on" : ""}" data-prof="${id}">${esc(AHI18N.trProf(CAT_PROF_NAMES[id]))}</button>`).join("");
    if (chips) groups.push(`<div class="f"><span>${t("Beruf")}</span><div class="rchips">${chips}</div></div>`);
  }
  const qsel = p.quality || "";
  const qchip = (v, label) => `<button type="button" class="${qsel === v ? "on" : ""}" data-qual="${v}">${label}</button>`;
  groups.push(`<div class="f"><span>${t("Qualität")}</span><div class="rchips">${qchip("", t("Alle"))}${qchip("2,3,4,5", QUAL(2) + "+")}${qchip("3,4,5", QUAL(3) + "+")}${qchip("4,5", QUAL(4) + "+")}</div></div>`);
  if (cfg.lvl) groups.push(`<div class="f"><span>${t("Stufe")}</span><div class="range"><input type="number" min="1" max="80" name="min_lvl" value="${esc(p.min_lvl || "")}" placeholder="${t("von")}">–<input type="number" min="1" max="80" name="max_lvl" value="${esc(p.max_lvl || "")}" placeholder="${t("bis")}"></div></div>`);
  const misc = [`<button type="button" class="${p.listed === "1" ? "on" : ""}" data-listed="1">${t("Nur aktuell gelistet")}</button>`];
  if (cfg.margin && ridx && ridx.teaches && ridx.teaches.size)
    misc.push(`<button type="button" class="${p.margin === "1" ? "on" : ""}" data-margin="1">${t("Mit Gewinnspanne")}</button>`);
  groups.push(`<div class="f"><span>${t("Markt")}</span><div class="rchips">${misc.join("")}</div></div>`);
  return `<div class="card filters catfilter" id="catfilter">${groups.join("")}</div>`;
}

function wireCatFilterBar(p) {
  const bar = $("#catfilter");
  if (!bar) return;
  $$("button[data-prof]", bar).forEach((b) => (b.onclick = () => {
    const cur = new Set((p.prof || "").split(",").filter(Boolean));
    if (cur.has(b.dataset.prof)) cur.delete(b.dataset.prof); else cur.add(b.dataset.prof);
    setParams({ prof: [...cur].join(","), page: 1 });
  }));
  $$("button[data-qual]", bar).forEach((b) => (b.onclick = () => setParams({ quality: b.dataset.qual, page: 1 })));
  const lb = $("button[data-listed]", bar);
  if (lb) lb.onclick = () => setParams({ listed: p.listed === "1" ? "" : "1", page: 1 });
  const mb = $("button[data-margin]", bar);
  if (mb) mb.onclick = () => setParams({ margin: p.margin === "1" ? "" : "1", page: 1 });
  $$("input[name=min_lvl], input[name=max_lvl]", bar).forEach((inp) => (inp.onchange = () =>
    setParams({ min_lvl: $("input[name=min_lvl]", bar)?.value || "", max_lvl: $("input[name=max_lvl]", bar)?.value || "", page: 1 })));
}

async function viewCategories(r) {
  const house = curHouse(r.params);
  const classes = await getClasses(house);
  const p = r.params;
  const cls = classes.find((c) => String(c.class_id) === String(p.class));
  const sub = cls?.subclasses.find((s) => String(s.subclass_id) === String(p.subclass));
  const cfg = cls ? CAT_FILTERS[cls.class_id] || {} : {};
  let ridx = null;
  if (cls && (cfg.profs?.length || cfg.margin)) ridx = await loadRecipes().catch(() => null);
  const crumbs = [`<a href="#/categories?house=${house}">${t("Alle Kategorien")}</a>`];
  if (cls) crumbs.push(`<a href="#/categories?house=${house}&class=${cls.class_id}">${esc(AHI18N.trClass(cls.class_name))}</a>`);
  if (sub) crumbs.push(esc(AHI18N.trSubclass(sub.subclass_name)));
  const maxv = (arr) => Math.max(1, ...arr.map((x) => x.market_value || 0));
  // Aktive Filter beim Wechsel in eine Unterkategorie mitnehmen
  const fq = ["prof", "quality", "listed", "min_lvl", "max_lvl", "margin"].filter((k) => p[k]).map((k) => `&${k}=${encodeURIComponent(p[k])}`).join("");
  const card = (x, href, name) => `<a class="cat" href="${href}"><div class="nm">${esc(name)}</div>
      <div class="row"><span>${num(x.items)} Items</span><span>${money(x.market_value, { short: true })}</span></div>
      <div class="bar" style="width:${(100 * (x.market_value || 0)) / card.max}%"></div></a>`;
  let body = "";
  if (!cls) {
    card.max = maxv(classes);
    body = `<div class="catgrid">${classes.map((c) => card(c, `#/categories?house=${house}&class=${c.class_id}`, AHI18N.trClass(c.class_name))).join("")}</div>`;
  } else if (!sub && cls.subclasses.length > 1) {
    card.max = maxv(cls.subclasses);
    body = `<div class="catgrid">${cls.subclasses.map((s) => card(s, `#/categories?house=${house}&class=${cls.class_id}&subclass=${s.subclass_id ?? -1}${fq}`, AHI18N.trSubclass(s.subclass_name))).join("")}</div>
      ${catFilterBar(cls, p, ridx)}<div class="card flush" id="catitems"><div class="loading">${t("Lade Items …")}</div></div>`;
  } else {
    body = `${catFilterBar(cls, p, ridx)}<div class="card flush" id="catitems"><div class="loading">${t("Lade Items …")}</div></div>`;
  }
  main.innerHTML = `<div class="crumbs">${crumbs.join(" › ")}</div>
    <div class="goblin-heading"><div class="pagehead"><h1>${esc(sub ? AHI18N.trSubclass(sub.subclass_name) : cls ? AHI18N.trClass(cls.class_name) : t("Kategorien"))}</h1></div>${goblinScene("head-goblin")}</div>${body}`;
  wireCatFilterBar(p);
  if (cls) {
    const sp = { house, class: cls.class_id, sort: p.sort || "value", dir: p.dir || "desc", page: p.page || 1, page_size: 50, spark: 1 };
    if (sub) sp.subclass = sub.subclass_id ?? -1;
    if (cls.class_id === -1) sp.class = -1;
    for (const k of ["prof", "quality", "listed", "min_lvl", "max_lvl", "margin"]) if (p[k]) sp[k] = p[k];
    const res = await api("search", sp);
    $("#catitems").innerHTML = `<h2>Items <span class="muted small catcount">· ${num(res.total)} ${t("Treffer")}</span></h2>` + table([COL.item, COL.price, COL.median, COL.avg30, COL.qty, COL.chg24, COL.chg7d, COL.liq, COL.value, COL.spark, COL.cmp], res.items, {
      sort: sp.sort, dir: sp.dir, onSort: (s, d) => setParams({ sort: s, dir: d, page: 1 }),
      rowHref: (x) => `/item/${x.item_id}?house=${x.house_id}`, empty: t("Keine Items passen zu den Filtern."),
    }) + pager(res.total, res.page, res.page_size, (pg) => setParams({ page: pg }));
  }
}

// ================================================================= Item
const FACTIONS = [
  { k: "allianz", name: "Allianz", fac: "Allianz", sub: "alle Regelsätze" },
  { k: "horde", name: "Horde", fac: "Horde", sub: "alle Regelsätze" },
  { k: "neutral", name: "Neutral", fac: "Neutral", sub: "Schwarzmeerräuber" },
];
const facName = (f) => f.k === "neutral" ? t("Schwarzmeerräuber") : AHI18N.faction(f.name);
const facSub = (f) => f.k === "neutral" ? t("Schwarzmeerräuber") : ruleName(houseById(state.house));
/** Der Name in der jeweils anderen Sprache (für die Unterzeile), sonst null. */
function altName(it) {
  if (AHI18N.get() === "en") return it.name && it.name_en && it.name !== it.name_en ? it.name : null;
  return it.name_en && it.name_en !== it.name ? it.name_en : null;
}
const GROUP_KINDS = FACTIONS.map((f) => f.k);

function wowTooltip(it) {
  const d = AHI18N.get() === "en" ? it.details_en : it.details;
  const lines = (d && d.lines) || [];
  if (!lines.length && !it.vendor_sell) return "";
  const col = { w: "", g: "cg", y: "cy", b: "cb", m: "cm" };
  return `<div class="wtip"><div class="tn q${it.quality ?? 1}">${esc(AHI18N.dispName(it))}</div>
    ${it.item_level && it.class_id != null && [2, 4].includes(+it.class_id) ? `<div class="cy">${t("Gegenstandsstufe")} ${it.item_level}</div>` : ""}
    ${lines.map((l) => `<div class="ln ${col[l.c] || ""}"><span>${esc(l.t)}</span>${l.r ? `<span>${esc(l.r)}</span>` : ""}</div>`).join("")}
    ${it.vendor_sell ? `<div class="sell">${t("Verkaufspreis:")} ${money(it.vendor_sell)}</div>` : ""}</div>`;
}

function originalItemPanel(it, withGoblin = true) {
  const binding = AHI18N.binding(it.binding);
  const fallback = wowTooltip(it) || `<div class="wtip"><div class="tn q${it.quality ?? 1}">${esc(AHI18N.dispName(it))}</div>${binding ? `<div>${binding}</div>` : ""}${it.item_level ? `<div class="cy">${t("Gegenstandsstufe")} ${it.item_level}</div>` : ""}${it.slot ? `<div>${esc(AHI18N.trSlot(it.slot))}</div>` : ""}${it.required_level ? `<div>${t("Benötigt Stufe")} ${it.required_level}</div>` : ""}<p class="cm">${t("Weitere Original-Stats werden geladen.")}</p></div>`;
  return `<section class="original-item-panel">${withGoblin ? goblinScene("panel-goblin") : ""}<div class="original-item-label"><span>${t("ORIGINAL-ITEMTEXT")}</span></div><div data-original-tooltip="${Number(it.id || it.item_id)}">${fallback}<p class="tooltip-provenance">${t("Vorhandene Blizzard-Daten · vollständiger Forever-Text wird geladen …")}</p></div></section>`;
}

/** Aufklappbarer Abschnitt; Inhalt wird erst beim ersten Öffnen gerendert (Zustand wird gemerkt). */
function fold(key, title, summary = "", openByDefault = false) {
  const open = store.get("fold." + key, openByDefault);
  return `<details class="fold" ${key === "rec" ? 'id="item-recipes"' : ""} data-fold="${key}" ${open ? "open" : ""}><summary><span class="ft">${title}</span>${summary ? `<span class="fs">${summary}</span>` : ""}<span class="chev" aria-hidden="true">›</span></summary><div class="fb" id="fb-${key}"></div></details>`;
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
  if (!uses.length && !made.length && !quests.length && !sets.length && !sv && !teach.length) return t("keine Herkunft oder Verwendung bekannt");
  const parts = [];
  if (teach.length) parts.push(tf("{0} gelehrte Rezepte", num(teach.length)));
  if (made.length) parts.push(t("herstellbar"));
  if (uses.length) parts.push(tf("Zutat in {0} Rezepten", num(uses.length)));
  if (sv || quests.length || sets.length) parts.push(t("Herkunft & Details"));
  return parts.join(" · ");
}

function renderRecipes(el, id, fk) {
  return Promise.all([loadGame(), loadSources(id)]).then(([[idx, qidx], sv]) => {
    const fac = FACTIONS.find((f) => f.k === fk) || FACTIONS[0];
    // Each render gets its own price view. Never mutate the shared recipe cache.
    const priceKey = `${rulesetKey()}:${fk}`;
    idx = {...idx, prices: {...idx.prices, [fk]: idx.prices[priceKey] || {items:{}}}};
    qidx = {...qidx, prices: {...qidx.prices, [fk]: qidx.prices[priceKey] || {items:{}}}};
    const hid = groupId(fk);
    const uses = RecipeCore.usedIn(idx, id), made = RecipeCore.madeBy(idx, id);
    const quests = RecipeCore.rewardedBy(qidx, id, fk), sets = RecipeCore.setsOf(qidx, id);
    const teach = RecipeCore.teaches(idx, id);
    if (!uses.length && !made.length && !quests.length && !sets.length && !sv && !teach.length) {
      el.innerHTML = `<div class="empty">${t("Für dieses Item sind keine Herkunft (Beute, Händler, Quests, Rezepte) und keine Verwendung bekannt.")}</div>`;
      return;
    }
    let questsAll = false, dropsAll = false, needAll = false, originsOpen = false;
    const profs = RecipeCore.professionsFor(idx, id);
    let prof = store.get("rec.prof", "");
    if (prof && !profs.some((p) => p.prof === prof)) prof = "";
    let best = store.get("rec.best", false);
    const opts = () => ({ mode: best ? "best" : "ah" });

    const info = (iid) => (idx.items[String(iid)] ? RecipeCore.itemInfo(idx, iid)
      : qidx.items[String(iid)] ? RecipeCore.itemInfo(qidx, iid) : RecipeCore.itemInfo({ items: sv?.names || {} }, iid));
    const label = (iid) => {
      const it = info(iid);
      const nm = AHI18N.dispName(it);
      const noPage = sv?.names?.[String(iid)]?.[5] === 0 && !idx.items[String(iid)] && !qidx.items[String(iid)];
      if (noPage) return `<a class="item" href="${AHI18N.whUrl("item", iid)}" target="_blank" rel="noopener" title="${t("Hier ohne eigene Seite – bei Wowhead öffnen")}">${itemLabel({ id: iid, name: nm, quality: it.quality, icon: it.icon }, false).replace(/^<span class="item">|<\/span>$/g, "")} ↗</a>`;
      return itemLabel({ id: iid, name: nm, quality: it.quality, icon: it.icon, house_id: hid });
    };
    const recipeName = r => AHI18N.get() === "en" && r.item ? AHI18N.dispName(info(r.item)) : r.name;
    const resultCell = (r) => r.item ? label(r.item) : `<span class="item"><span class="ico bq1">✦</span><span title="${esc(r.name)}">${esc(r.name)}</span></span>`;
    const yieldTxt = (r) => (r.qmin === r.qmax ? num(r.qmin) : `${num(r.qmin)}–${num(r.qmax)}`) + "×";
    const noPrice = (title = t("Derzeit kein Angebot im AH (evtl. Händlerware)")) => `<span class="miss" title="${esc(title)}">${t("kein Preis")}</span>`;
    const costCell = (m) => m.complete ? money(m.perUnit ?? m.cost)
      : `<span class="miss" title="${tf("{0} Zutat(en) ohne AH-Preis", m.missing.length)}">${t("unvollständig")}</span>`;
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
      const tog = cyc ? `<span class="rtog off" title="${t("Kreislauf: dieses Item kommt im Pfad schon vor")}">↻</span>`
        : next && depth < RC_MAX_DEPTH ? `<button type="button" class="rtog" title="${tf("Daraus wird … ({0})", next)}" aria-expanded="false">▸</button>`
        : '<span class="rtog off"></span>';
      return `<tr class="nohover" data-d="${depth}" data-item="${r.item || ""}" data-path="${[...path, r.item].filter(Boolean).join(",")}">
        <td class="itemcell"><span class="rind" style="--d:${depth}">${tog}<span class="rname">${resultCell(r)}<span class="rcat">${depth ? esc(AHI18N.trProf(r.prof)) + " · " : ""}${esc(AHI18N.trCat(r.cat))}${next && !cyc ? ` · <span title="${tf("Wird in {0} weiteren Rezepten verwendet", next)}">→ ${next} ${t("weitere")}</span>` : ""}</span></span></span></td>
        <td class="n">${reag ? num(reag.q) + "×" : ""}</td>
        <td class="n">${r.item ? yieldTxt(r) : ""}</td>
        <td class="n">${costCell(m)}</td>
        <td class="n">${priceCell(r.item)}</td>
        <td class="n">${marginCell(r)}</td></tr>`;
    };
    const useHead = `<thead><tr><th>${t("Ergebnis")}</th><th class="n" title="${t("Benötigte Menge der Zutat in der Zeile darüber")}">${t("Benötigt")}</th><th class="n">${t("Ergibt")}</th>
      <th class="n" title="${t("Materialkosten je hergestelltem Stück")}">${t("Material/Stk")}</th><th class="n">${t("AH-Preis")}</th><th class="n" title="${t("AH-Preis nach 5 % Gebühr minus Materialkosten, je Stück")}">${t("Spanne")}</th></tr></thead>`;

    // ----- „Hergestellt aus“: je Rezept Zutatenliste, herstellbare Zutaten aufklappbar
    const madeRows = (r, depth, path) => {
      const m = RecipeCore.materialCost(idx, fk, r, opts());
      return m.parts.map((x) => {
        const cyc = path.includes(x.id);
        const sub = !cyc && depth < RC_MAX_DEPTH ? RecipeCore.madeBy(idx, x.id) : [];
        const src = x.source === "craft" ? ` <span class="pill" title="${t("Selbst herstellen ist günstiger als kaufen")}">${t("selbst")}</span>` : "";
        return `<tr class="nohover" data-d="${depth}" data-mitem="${x.id}" data-path="${[...path, x.id].join(",")}">
          <td class="itemcell"><span class="rind" style="--d:${depth}">${cyc ? `<span class="rtog off" title="${t("Kreislauf")}">↻</span>`
            : sub.length ? `<button type="button" class="rtog" title="${tf("Zutat selbst herstellen: {0}", esc(sub.map((s) => s.name).join(", ")))}" aria-expanded="false">▸</button>` : '<span class="rtog off"></span>'}${label(x.id)}</span></td>
          <td class="n">${num(x.q)}×</td><td class="n">${x.unit == null ? noPrice() : money(x.unit) + src}</td><td class="n">${x.sum == null ? "" : money(x.sum)}</td></tr>`;
      }).join("");
    };
    const madeCard = (r) => {
      const m = RecipeCore.materialCost(idx, fk, r, opts());
      const g = RecipeCore.margin(idx, fk, r, opts());
      const sell = RecipeCore.price(idx, fk, r.item);
      const books = RecipeCore.taughtBy(idx, r).filter((b) => b !== id);
      const bookHtml = books.length ? `<div class="rbook muted small">${t("Gelehrt durch")} ${books.map((b) => {
        const p = RecipeCore.price(idx, fk, b);
        return `${label(b)} <span>${p ? money(p.min) : noPrice(t("Derzeit kein Angebot im AH"))}</span>`; }).join(" · ")}</div>` : "";
      return `<div class="rcard">
        <div class="rcardh"><b>${esc(recipeName(r))}</b><span class="muted small">${esc(AHI18N.trProf(r.prof))}${r.cat ? " · " + esc(AHI18N.trCat(r.cat)) : ""} · ${t("ergibt")} ${yieldTxt(r)}</span></div>${bookHtml}
        <div class="tscroll"><table class="t rt"><thead><tr><th>${t("Zutat")}</th><th class="n">${t("Menge")}</th><th class="n">${t("Stückpreis")}</th><th class="n">${t("Summe")}</th></tr></thead>
          <tbody data-kind="made">${madeRows(r, 0, [id])}</tbody></table></div>
        <div class="rfoot">
          <span>${t("Material je Stück")} <b>${m.complete ? money(m.perUnit) : `≥ ${money(m.partial / r.qavg)} <span class="miss">${tf("{0} ohne Preis", m.missing.length)}</span>`}</b></span>
          <span>${t("AH-Preis")} <b>${sell ? money(sell.min) : noPrice()}</b></span>
          <span>${t("Spanne")} <b>${marginCell(r)}</b></span></div></div>`;
    };

    // ----- Quests: Belohnungen mit Preisen, wertvollste Wahl markiert
    const questRow = (q) => {
      const rw = RecipeCore.questRewards(qidx, fk, q);
      const mine = q.rewards.find((r) => r.id === id);
      const tags = [
        q.type ? `<span class="pill">${esc(AHI18N.trQuestType(q.type))}</span>` : "",
        q.faction ? `<span class="pill"><span class="fac ${esc(q.faction)}"></span>${esc(AHI18N.faction(q.faction))}</span>` : "",
        q.classes ? `<span class="pill" title="${t("Nur für diese Klassen")}">${esc(q.classes)}</span>` : "",
        q.repeatLabel ? `<span class="pill">${esc(q.repeatLabel)}</span>` : "",
        mine?.choice ? `<span class="pill" title="${t("Eine Belohnung zur Wahl")}">${t("zur Wahl")}</span>` : "",
      ].join("");
      const rewards = rw.list.length > 1 || !mine ? `<div class="qrw">${rw.list.map((r) => `<span class="qri ${r.id === id ? "me" : ""}">${label(r.id)}
          <span class="qp">${r.price ? money(r.price.min) : noPrice(t("Derzeit kein Angebot im AH"))}${r.id === rw.best ? ` <span class="best" title="${t("Wertvollste Wahlbelohnung im AH")}">★</span>` : ""}</span></span>`).join("")}</div>` : "";
      return `<div class="qrow"><div class="qt"><a href="${AHI18N.whUrl("quest", q.id)}" target="_blank" rel="noopener" title="${t("Quest bei Wowhead öffnen")}"${AHI18N.questAttr(q.id)}>${esc(AHI18N.questTitle(q.id, q.title))} ↗</a>
          <span class="muted small">${esc(AHI18N.trZone(q.zone || ""))}</span>${tags}</div>${rewards}</div>`;
    };
    const questHtml = () => {
      if (!quests.length) return "";
      const shown = questsAll ? quests : quests.slice(0, 6);
      return `<h3>${t("Questbelohnung")} <span class="muted small">${quests.length} ${t("Quests")}</span></h3>
        <div class="qlist">${shown.map(questRow).join("")}</div>
        ${quests.length > shown.length ? `<button type="button" class="btn small mt" id="qall">${tf("Alle {0} Quests anzeigen", quests.length)}</button>` : ""}`;
    };
    // ----- Sets: Teile mit Preisen, Boni, Summe im AH
    const setHtml = () => !sets.length ? "" : `<h3>${t("Teil eines Sets")}</h3><div class="rcards">${sets.map((s) => {
      const c = RecipeCore.setCost(qidx, fk, s);
      return `<div class="rcard"><div class="rcardh"><b>${esc(s.name)}</b><span class="muted small">${tf("{0} Teile", s.items.length)}</span></div>
        <div class="tscroll"><table class="t rt"><tbody>${c.parts.map((x) => `<tr class="nohover ${x.id === id ? "me" : ""}"><td class="itemcell">${label(x.id)}</td>
          <td class="n">${x.price ? money(x.price.min) : noPrice(t("Derzeit kein Angebot im AH"))}</td></tr>`).join("")}</tbody></table></div>
        ${s.effects.length ? `<div class="sfx">${s.effects.map(([n, tx]) => `<div><span class="muted">(${n})</span> ${esc(tx)}</div>`).join("")}</div>` : ""}
        <div class="rfoot"><span>${t("Alle Teile im AH")} <b>${c.have ? money(c.total) : noPrice()}</b>${c.complete ? "" : ` <span class="miss">${tf("{0} von {1} angeboten", c.have, c.count)}</span>`}</span></div></div>`;
    }).join("")}</div>`;

    // ----- Herkunft aus der QuestieDB: Beute, Fundorte, Händler, Behälter, Quests
    const facKey = fk;
    const wh = (kind, xid, text, title = t("Bei Wowhead öffnen")) =>
      `<a href="${AHI18N.whUrl(kind, xid)}" target="_blank" rel="noopener" title="${esc(title)}">${esc(text)} ↗</a>`;
    const facPill = (f) => f ? `<span class="pill"><span class="fac ${esc(f)}"></span>${esc(AHI18N.faction(f))}</span>` : "";
    const otherLast = (a, b) => ((a.faction && facKey && a.faction !== facKey) ? 1 : 0) - ((b.faction && facKey && b.faction !== facKey) ? 1 : 0);
    const dropHtml = () => {
      if (!sv?.drops.length) return "";
      const shown = dropsAll ? sv.drops : sv.drops.slice(0, 10);
      const more = sv.dropsTotal - sv.drops.length;
      return `<h3>${t("Beute von")} <span class="muted small">${tf("{0} Gegner", num(sv.dropsTotal))}${sv.hasChance ? "" : " · " + t("Dropchance nicht bekannt")}</span></h3>
        <div class="tscroll"><table class="t rt"><thead><tr><th>${t("Gegner")}</th><th class="n">${t("Stufe")}</th><th>${t("Gebiet")}</th>${sv.hasChance ? `<th class="n">${t("Chance")}</th>` : ""}</tr></thead><tbody>
        ${shown.map((n) => `<tr class="nohover"><td>${wh("npc", n.id, n.name)}${n.rank ? ` <span class="pill">${esc(AHI18N.trRank(n.rank))}</span>` : ""}</td>
          <td class="n">${esc(n.level)}</td><td>${esc(AHI18N.trZone(n.zone))}</td>${sv.hasChance ? `<td class="n">${n.chance == null ? "" : num(n.chance, n.chance < 10 ? 1 : 0) + " %"}</td>` : ""}</tr>`).join("")}</tbody></table></div>
        ${sv.drops.length > shown.length ? `<button type="button" class="btn small mt" id="dall">${tf("Alle {0} anzeigen", sv.drops.length)}</button>` : ""}
        ${more > 0 && shown.length === sv.drops.length ? `<div class="muted small mt">${tf("… und {0} weitere – vollständige Liste bei {1}", num(more), wh("item", id, "Wowhead"))}</div>` : ""}`;
    };
    const objHtml = () => !sv?.objects.length ? "" : `<h3>${t("Fundort")} <span class="muted small">${t("Abbau, Sammeln, Truhen")}</span></h3>
      <div class="olist">${sv.objects.map((o) => `<div class="orow">${wh("object", o.ids[0], o.name)}${o.zones.length
        ? ` <span class="muted small">${esc(o.zones.slice(0, 8).map(AHI18N.trZone).join(", "))}${o.zones.length > 8 ? ` +${o.zones.length - 8}` : ""}</span>` : ""}</div>`).join("")}</div>`;
    const vendorHtml = () => !sv?.vendors.length ? "" : `<h3>${t("Verkauft von")} <span class="muted small">${tf("{0} Händler", num(sv.vendorsTotal))}</span></h3>
      <div class="tscroll"><table class="t rt"><thead><tr><th>${t("Händler")}</th><th>${t("Gebiet")}</th><th>${t("Fraktion")}</th></tr></thead><tbody>
      ${sv.vendors.slice().sort(otherLast).map((n) => `<tr class="nohover"><td>${wh("npc", n.id, n.name)}${n.sub ? ` <span class="muted small">&lt;${esc(n.sub)}&gt;</span>` : ""}</td>
        <td>${esc(AHI18N.trZone(n.zone))}</td><td>${facPill(n.faction)}</td></tr>`).join("")}</tbody></table></div>
      ${sv.vendorsTotal > sv.vendors.length ? `<div class="muted small mt">${tf("… und {0} weitere bei {1}", num(sv.vendorsTotal - sv.vendors.length), wh("item", id, "Wowhead"))}</div>` : ""}`;
    const anyPrice = (iid) => RecipeCore.price(idx, fk, iid) || RecipeCore.price(qidx, fk, iid);
    const itemList = (ids) => `<div class="qrw">${ids.map((x) => { const p = anyPrice(x); return `<span class="qri">${label(x)}${p ? ` <span class="qp">${money(p.min)}</span>` : ""}</span>`; }).join("")}</div>`;
    const boxHtml = (which) => which === "in"
      ? (sv?.containers.length ? `<h3>${t("Enthalten in")} <span class="muted small">${t("Behälter, Taschen, Kisten")}</span></h3>${itemList(sv.containers)}` : "")
      : (sv?.contents.length ? `<h3>${t("Inhalt")} <span class="muted small">${tf("{0} mögliche Items", num(sv.contents.length))}</span></h3>${itemList(sv.contents)}` : "");
    const needHtml = () => {
      const list = [...(sv?.starts || []), ...(sv?.quests || []).slice().sort(otherLast)];
      if (!list.length) return "";
      const shown = needAll ? list : list.slice(0, 8);
      return `<h3>${t("Für Quests")} <span class="muted small">${t("Questziel oder benötigter Gegenstand")}</span></h3>
        <div class="qlist">${shown.map((q) => `<div class="qrow"><div class="qt">${wh("quest", q.id, AHI18N.questTitle(q.id, q.title), t("Quest bei Wowhead öffnen")).replace("<a ", `<a ${AHI18N.questAttr(q.id)} `)}
          <span class="muted small">${esc(AHI18N.trZone(q.zone))}${q.level ? ` · ${t("Stufe")} ${q.level}` : ""}</span>${facPill(q.faction)}<span class="pill">${esc(AHI18N.trQKind(q.kindLabel))}</span></div></div>`).join("")}</div>
        ${list.length > shown.length ? `<button type="button" class="btn small mt" id="nall">${tf("Alle {0} Quests anzeigen", list.length)}</button>` : ""}`;
    };
    const srcNote = () => sv ? `<div class="muted small srcnote">${t("Beute, Fundorte, Händler, Behälter und Questziele aus der")}
      <a href="https://github.com/Questie/QuestieDB" target="_blank" rel="noopener">QuestieDB</a> ${t("(Community-Daten, nicht vollständig; NPC- und Objektnamen englisch).")}</div>` : "";

    const draw = () => {
      const u = uses.filter((r) => !prof || r.prof === prof);
      const mk = made.filter((r) => !prof || r.prof === prof);
      const byProf = new Map();
      for (const r of u) (byProf.get(r.prof) || byProf.set(r.prof, []).get(r.prof)).push(r);
      el.innerHTML = `
        ${profs.length ? `<div class="rbar">
          <div class="rchips" id="rprof">${[["", t("Alle Berufe"), uses.length + made.length], ...profs.map((p) => [p.prof, AHI18N.trProf(p.prof), p.count])]
            .map(([k, l, n]) => `<button type="button" class="${k === prof ? "on" : ""}" data-p="${esc(k)}">${esc(l)} <span>${n}</span></button>`).join("")}</div>
          <label class="small" title="${t("Zutaten, die sich günstiger selbst herstellen lassen, mit Herstellkosten statt AH-Preis rechnen")}"><input type="checkbox" id="rbest" ${best ? "checked" : ""}> ${t("günstigster Weg (selbst herstellen)")}</label>
        </div>` : ""}
        <div class="muted small rnote"><span class="fac ${fac.fac}"></span>${t("Preise:")} ${esc(ruleName(houseById(state.house)))} · ${esc(facName(fac))}${profs.length ? " · " + t("Spanne nach 5 % AH-Gebühr, ohne Verkaufsgarantie") : ""}</div>
        ${teach.length ? `<h3>${t("Lehrt Rezept")} <span class="muted small">${t("Materialkosten und Spanne beim Herstellen")}</span></h3><div class="rcards">${teach.map(madeCard).join("")}</div>` : ""}
        ${mk.length ? `<h3>${t("Hergestellt aus")}</h3><div class="rcards">${mk.map(madeCard).join("")}</div>` : ""}
        ${u.length ? `<div class="rhead"><h3>${t("Verwendet in · daraus wird …")}</h3><button type="button" class="btn small" id="rexp">${t("Alles aufklappen")}</button></div>
          ${[...byProf.entries()].map(([pn, rs]) => `<div class="rprof"><h4>${esc(AHI18N.trProf(pn))} <span class="muted small">${tf("{0} Rezepte", rs.length)}</span></h4>
            <div class="tscroll"><table class="t rt">${useHead}<tbody data-kind="use">${rs.map((r) => useRow(r, id, 0, [id])).join("")}</tbody></table></div></div>`).join("")}` : ""}
        ${prof && !u.length && !mk.length ? `<div class="empty">${t("Keine Rezepte für diesen Beruf.")}</div>` : ""}
        ${sv || quests.length || sets.length ? `<details class="recipe-origins" ${originsOpen ? "open" : ""}><summary>${t("Fundorte, Quests & weitere Details")}</summary>${dropHtml()}${objHtml()}${vendorHtml()}${boxHtml("in")}${questHtml()}${needHtml()}${setHtml()}${boxHtml("out")}${srcNote()}</details>` : ""}`;
      const origins = $(".recipe-origins", el);
      if (origins) origins.ontoggle = () => { originsOpen = origins.open; };
      AHI18N.hydrateQuestTitles(el);
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
        html = RecipeCore.madeBy(idx, iid).map((r) => `<tr class="nohover rsub" data-d="${d + 1}"><td colspan="4"><span class="rind muted small" style="--d:${d + 1}">${esc(AHI18N.trProf(r.prof))} · ${esc(r.name)} · ${t("ergibt")} ${yieldTxt(r)}</span></td></tr>`
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

function catalogNote(it) {
  if (!it.catalog_build) return "";
  const status = it.catalog_state === "readable" ? t("Im Client lesbar benannt") : it.catalog_state === "unreadable" ? t("Basis-ID im Client vorhanden; Namens-/Detaildatensatz nicht lesbar") : t("Nicht in der gepinnten Client-Basistabelle; bisherige Metadaten bleiben erhalten");
  const binding = ({0: t("nicht gebunden laut Client"), 1: t("beim Aufheben gebunden"), 2: t("beim Anlegen gebunden"), 3: t("bei Benutzung gebunden"), 4: t("Questbindung")})[it.binding];
  return `<div class="note mt">${tf("Forever-Client {0}", esc(it.catalog_build))} · ${esc(status)}${it.catalog_test ? " · " + t("Test-/Alteintrag-Hinweis im Namen") : ""}${binding ? " · " + esc(binding) : ""}. ${t("Ein Clientdatensatz bestätigt weder Erhältlichkeit noch aktuelle AH-Handelbarkeit.")}</div>`;
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
    main.innerHTML = `<div class="ihead item-banner"><div class="who">${icon(it, true)}
        <div style="min-width:0"><h1 class="q${it.quality ?? 1}">${esc(AHI18N.dispName(it))}</h1>
          <div class="en">${altName(it) ? esc(altName(it)) + " · " : ""}ID ${id} · <a href="${AHI18N.whUrl("item", id)}" target="_blank" rel="noopener">Wowhead ↗</a></div>
          ${it.class_name || it.item_level ? `<div class="chips mt">${it.class_name ? `<span class="pill">${esc([...new Set([AHI18N.trClass(it.class_name), AHI18N.trSubclass(it.subclass_name)].filter(Boolean))].join(" › "))}</span>` : ""}${it.item_level ? `<span class="pill">${t("Stufe")} ${it.item_level}</span>` : ""}</div>` : ""}</div></div></div>
      <div class="card"><div class="empty">${t("Für dieses Item liegt seit Beginn unserer Erfassung keine Preisbeobachtung vor. Daraus lässt sich keine sichere Aussage zur Handelbarkeit ableiten.")}</div></div>
      ${originalItemPanel(it)}
      ${catalogNote(it)}
      <div class="folds">${fold("rec", t("Rezepte & Verwendung"), t("Lade Rezepte und Quests …"), true)}</div>`;
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
  if (!house) house = null;
  const fac = FACTIONS.find((f) => f.k === fk);
  const groupRow = (k) => info.houses.find((h) => h.group === k && h.ruleset_key === rulesetKey());
  const total = groupRow(fk);
  // Häuser der Fraktion, auch ohne Angebot (dann „keine Preisbeobachtung“)
  const allSingles = state.houses.filter((h) => h.kind === "haus" && h.group === fk).sort((a, b) => (a.verbund || "").localeCompare(b.verbund || ""));
  const rowOf = (hid) => info.houses.find((h) => h.house_id === hid);
  const listed = allSingles.map((h) => rowOf(h.id)).filter((x) => x?.is_current && x.last_min);
  const cheapest = listed.reduce((m, h) => (!m || h.last_min < m.last_min ? h : m), null);
  const hid = house?.house_id;
  const inCmp = state.compare.includes(id);

  // Kopf
  const catHref = (extra = "") => `#/categories?class=${it.class_id ?? -1}${extra}`;
  const tags = [
    it.class_name ? `<a class="tag" href="${catHref()}">${esc(AHI18N.trClass(it.class_name))}${it.subclass_name ? ` › ${esc(AHI18N.trSubclass(it.subclass_name))}` : ""}</a>` : "",
    it.slot ? `<span class="tag">${esc(AHI18N.trSlot(it.slot))}</span>` : "",
    it.item_level ? `<span class="tag">${t("Stufe")} <b>${it.item_level}</b></span>` : "",
    it.required_level ? `<span class="tag">${t("ab Level")} <b>${it.required_level}</b></span>` : "",
    it.is_commodity ? `<span class="tag">${t("Handelsware")}</span>` : "",
    it.vendor_sell ? `<span class="tag">${t("Händler zahlt")} ${money(it.vendor_sell)}</span>` : "",
  ].filter(Boolean).join("");

  // Fraktions-Reiter mit Preis
  const tabs = FACTIONS.map((f) => {
    const g = groupRow(f.k);
    return `<button type="button" class="ftab ${f.k} ${f.k === fk ? "on" : ""}" data-k="${f.k}" role="tab" aria-selected="${f.k === fk}">
      <span class="fn"><span><span class="fac ${f.fac}"></span>${facName(f)}</span><small>${facSub(f)}</small></span>
      <span class="fp">${g ? listingPrice(g) : `<span class="muted missing-price">${t("kein Preis<br>beobachtet")}</span>`}</span>
      ${g ? `<span class="fd">${g.is_current ? pct(g.chg24_min) : t("historisch")}</span>` : ""}</button>`;
  }).join("");

  // Karten: Gesamt + Regelsätze
  const card = (row, title, sub, opts = {}) => {
    const hidC = row ? row.house_id : opts.hid;
    const on = hidC && hidC === hid;
    if (!row || !row.last_min) return `<div class="hcard off ${opts.total ? "total" : ""}"><div class="ht"><b>${title}</b><span>${sub}</span></div><div class="hp muted">${t("keine Preisbeobachtung")}</div></div>`;
    return `<button type="button" class="hcard ${opts.total ? "total" : ""} ${on ? "on" : ""}" data-h="${hidC}">
      <div class="ht"><b>${title}</b><span>${sub}</span></div>
      <div class="hp">${listingPrice(row)}</div>
      <div class="hm"><span>${t("Median")} ${money(row.last_median)}</span><span class="${row.is_current ? "hq" : ""}">${row.is_current ? compact(row.last_qty) + " " + t("Stück") : t("historisch")}</span></div>
      <div class="hm"><span>${row.is_current ? "24 h " + pct(row.chg24_min) : t("zuletzt") + " " + dt(row.last_ts)}</span>${cheapest && row.house_id === cheapest.house_id && listed.length > 1 ? `<span class="best">${t("★ günstigstes")}</span>` : ""}</div>
      ${row.liq_score != null ? `<div class="hm"><span>${liqBadge(row)}</span></div>` : ""}</button>`;
  };
  const cards = ""; // Ruleset selection lives above the faction selector, not duplicate cards.

  // Hinweise
  const hints = [];
  const priceMethod = house ? `${house.market_complete ? t("Vollständiger Snapshot") : t("Teilstand / Vollständigkeit nicht belegt")}. ${house.last_ts < Date.now()/1000-10800 ? t("Quellstand älter als 3 Stunden.") + " " : ""}${t("Referenzpreis:")} ${house.price_basis==="robust_reference" ? t("mengengewichteter Median der hausweisen P10-Preise, kein günstigstes Angebot") : t("günstigstes beobachtetes Stückangebot")}. ${t("Günstigstes separat beobachtetes Angebot:")} ${house.last_lowest == null ? t("für diesen historischen Stand nicht gesondert gespeichert") : moneyText(house.last_lowest)}. ${t("Handel nur innerhalb desselben Regelsatzes.")}` : "";
  if (house && !house.is_current) hints.push(`${listingNote(house)}. ${t("Letzte Preisbeobachtung:")} ${dt(house.last_ts)}. ${t("Die Historie bleibt sichtbar.")}`);
  if (it.translation_missing && AHI18N.get() === "de") hints.push(`Deutscher Forever-Name noch nicht bestätigt.${it.aliases?.length ? " Suchalias: " + it.aliases.join(", ") + "." : " Anzeige mit englischem Fallback."}`);
  if (house?.is_current && house.last_ts >= Date.now() / 1000 - 3 * 3600 && it.vendor_sell && house.last_min && house.last_min < it.vendor_sell)
    hints.push(tf("💰 Günstiger als der Händlerpreis ({0}) – Abweichung prüfen: Markt, tatsächlicher Kaufpreis und Gebühren können abweichen.", money(it.vendor_sell)));
  if (house?.is_current && house.last_ts >= Date.now() / 1000 - 3 * 3600 && house.avg7_min && house.last_min && house.last_min < house.avg7_min * 0.85)
    hints.push(tf("▼ Gerade {0} % unter dem 7-Tage-Schnitt.", num((1 - house.last_min / house.avg7_min) * 100, 0)));

  const selName = !house ? facName(fac) : GROUP_KINDS.includes(house.kind) ? `${facName(fac)} ${t("gesamt")}` : `${ruleName(house)} · ${facName(fac)}`;
  const daily = house?.granularity === "day";
  const ranges = daily ? ["7d", "30d", "90d", "all"] : ["24h", "7d", "30d", "all"];
  if (!ranges.includes(p.range)) p.range = daily ? "30d" : "7d";
  const shown = new Set((p.show || "min,median").split(","));
  const overlay = p.cmp === "1";

  main.innerHTML = `
    <div class="crumbs"><a href="#/categories">${t("Kategorien")}</a> › <a href="${catHref()}">${esc(it.class_name ? AHI18N.trClass(it.class_name) : t("Ohne Metadaten"))}</a>${it.subclass_name ? ` › <a href="${catHref(`&subclass=${it.subclass_id}`)}">${esc(AHI18N.trSubclass(it.subclass_name))}</a>` : ""}</div>
    <div class="ihead item-banner">
      <div class="who">${icon(it, true)}
        <div style="min-width:0"><h1 class="q${it.quality ?? 1}">${esc(AHI18N.dispName(it))}</h1>
          <div class="en">${altName(it) ? esc(altName(it)) + " · " : ""}ID ${id} · <a href="${AHI18N.whUrl("item", id)}" target="_blank" rel="noopener">Wowhead ↗</a></div>
          ${tags ? `<div class="chiprow">${tags}</div>` : ""}</div></div>
      <div class="actions">
        <button class="btn" data-watch="${id}">${watchIds().includes(id) ? "★ " + t("Gemerkt") : "☆ " + t("Merken")}</button>
        <button class="btn" id="cmpBtn">${inCmp ? t("✓ Im Vergleich") : t("+ Vergleich")}</button>
        <a class="btn" href="#/compare?items=${[...new Set([...state.compare, id])].join(",")}${hid ? "&house=" + hid : ""}">${t("Vergleichen ›")}</a>
      </div></div>
    <section class="item-price-summary card" aria-label="${t("Aktueller Marktstand")}">
      <div><span class="small muted">${esc(ruleName(houseById(state.house)))} · ${facName(fac)}</span><div class="item-current-price">${house ? listingPrice(house) : "–"}</div><span class="small muted">${t("Referenzpreis")}</span></div>
      <div><span class="small muted">${t("Menge")}</span><b>${house?.is_current ? num(house.last_qty) : "–"}</b><span class="small muted">${house?.last_ts ? ago(house.last_ts) : t("Keine Preisbeobachtung im gewählten Markt.")}</span></div>
      <a class="btn" href="#item-recipes" id="recipeJump">${t("Rezepte & Verwendung")} ↓</a>
    </section>
    ${originalItemPanel(it)}
    <div class="ftabs" role="tablist" id="ftabs">${tabs}</div>
    <div class="hcards" id="hcards">${cards}</div>
    ${house ? `<div class="item-market-note muted small">${house.market_complete ? t("Vollständiger Snapshot") : t("Teilstand / Vollständigkeit nicht belegt")} · ${t("Günstigstes beobachtetes Angebot:")} ${house.last_lowest==null ? t("nicht gesondert gespeichert") : money(house.last_lowest)}${house.last_ts < Date.now()/1000-10800 ? " · " + t("veraltet") : ""}</div><details class="card item-quality"><summary>${t("Preisbegriffe & Datenqualität")}</summary>${catalogNote(it)}<p>${priceMethod}</p><p>${t("Ältere Reihenpunkte sind teils Mittelwerte. Ein Tief der Referenzreihe ist kein garantiertes Angebotstief. Angebotene Menge ist weder Absatz noch Liquidität.")}</p></details>` : ""}
    ${hints.map((h) => `<div class="hint">${h}</div>`).join("")}
    ${house ? `
    <div class="card">
      <div class="cardhead"><h2>${t("Preisverlauf")} <span class="muted small" style="font-weight:400">· ${esc(selName)}</span></h2>
        <div class="right">
          <div class="tgl" id="tog">
            ${[["median", t("Median")], ["lowest", t("Günstigstes")]].map(([k, l]) => `<button type="button" data-k="${k}" class="${shown.has(k) ? "on" : ""}">${l}</button>`).join("")}
            <button type="button" data-k="out" class="${p.out !== "0" ? "on" : ""}" title="${t("Ungewöhnliche Preispunkte markieren")}">${t("Ausreißer")}</button>
            <button type="button" data-k="cmp" class="${overlay ? "on" : ""}" title="${t("Referenzpreis aller Regelsätze dieser Fraktion in einem Chart")}">${t("Regelsätze")}</button>
          </div>
          <div class="seg" id="rng">${ranges.map((x) => `<button data-r="${x}" class="${x === p.range ? "on" : ""}">${x === "all" ? t("Alles") : x.replace("d", " T").replace("h", " h")}</button>`).join("")}</div>
        </div></div>
      <div class="chartbox"><canvas id="pc"></canvas></div>
      <div class="muted small" id="pcnote" style="margin-top:6px"></div>
    </div>
    <div class="folds">
      ${fold("rec", t("Rezepte & Verwendung"), t("Lade Rezepte und Quests …"), true)}
      ${fold("liq", t("Angebotssignale"), house.liq_score != null ? `Score ${num(house.liq_score)} · ${tf("{0} Signale / 7 T", num(house.liq_events))}` : t("noch keine Daten"), false)}
      ${fold("kpi", t("Kennzahlen & Muster"), house.avg7_min ? `Ø 7 T ${money(house.avg7_min)} · 7 T ${pct(house.chg7d_min)}` : "")}
      <details class="expert-analysis"><summary>${t("Analyse-Details")} <span>${t("Regelsätze vergleichen · Snapshots · Ausreißer")}</span></summary><div class="expert-analysis-body">
      ${fold("all", t("Regelsätze vergleichen"), `${info.houses.filter((h) => h.kind === "haus").length} ${t("Häuser")}`)}
      ${fold("raw", t("Snapshots & Ausreißer"), t("Preispunkte prüfen"))}</div></details>
      ${fold("info", t("Item-Details"), it.details?.lines?.length ? t("Tooltip wie im Spiel") : "")}
    </div>` : `<div class="card"><div class="empty">${t("Keine Preisbeobachtung im gewählten Markt.")}</div></div>
    <div class="folds">${fold("rec", t("Rezepte & Verwendung"), t("Lade Rezepte und Quests …"), true)}${fold("all", t("Alle Auktionshäuser im Vergleich"), "")}${fold("info", t("Item-Details"), "")}</div>`}`;
  const recipeJump = $("#recipeJump");
  recipeJump.onclick = e => { e.preventDefault(); const target = $('details[data-fold="rec"]'); if (target) { target.open = true; target.scrollIntoView({behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block:"start"}); } };
  setRecipeSummary(id);
  const recFold = (el) => renderRecipes(el, id, fk);

  // Interaktion
  $("#ftabs").onclick = (e) => { const b = e.target.closest("button[data-k]"); if (b) { const g = groupId(b.dataset.k); if (g) setParams({ house: g }); } };
  $("#hcards").onclick = (e) => { const b = e.target.closest("button[data-h]"); if (b) setParams({ house: b.dataset.h }); };
  $("#cmpBtn").onclick = (e) => { toggleCompare(id); e.target.textContent = state.compare.includes(id) ? t("✓ Im Vergleich") : t("+ Vergleich"); };

  const allTable = (el) => {
    const rows = info.houses.filter((h) => GROUP_KINDS.includes(h.kind) || h.kind === "haus")
      .sort((a, b) => (GROUP_KINDS.indexOf(a.group || a.kind) - GROUP_KINDS.indexOf(b.group || b.kind)) || ((GROUP_KINDS.includes(b.kind) ? 1 : 0) - (GROUP_KINDS.includes(a.kind) ? 1 : 0)) || (a.verbund || "").localeCompare(b.verbund || ""));
    const ref = (h) => groupRow(h.group || h.kind);
    el.innerHTML = `<p class="note">${t("Handel nur innerhalb desselben Regelsatzes.")}</p>` + table([
      { label: t("Auktionshaus"), fmt: (h) => GROUP_KINDS.includes(h.kind) ? `<b><span class="fac ${esc(h.faction)}"></span>${esc(AHI18N.faction(h.faction))} ${t("gesamt")}</b>` : `<span class="fac ${esc(h.faction)}"></span>${esc(AHI18N.faction(h.faction))} · ${esc(ruleName(h))}` },
      { label: t("Referenz"), num: true, fmt: (h) => listingPrice(h) },
      { label: t("Median"), num: true, fmt: (h) => money(h.last_median) },
      { label: t("Ø 7 T"), num: true, fmt: (h) => money(h.avg7_min) },
      { label: t("Stück"), num: true, fmt: (h) => h.is_current ? num(h.last_qty) : "–" },
      { label: "Δ 24 h", num: true, fmt: (h) => pct(h.chg24_min) },
      { label: t("vs. Auswahl"), num: true, title: t("Preis im Vergleich zum gewählten Regelsatz"), fmt: (h) => GROUP_KINDS.includes(h.kind) || !ref(h)?.last_min ? '<span class="muted">—</span>' : pct((h.last_min / ref(h).last_min - 1) * 100, 0) },
    ], rows, { rowHref: (h) => `/item/${id}?house=${h.house_id}&range=${p.range}` });
  };
  const infoBox = (el) => {
    el.innerHTML = `<div class="infogrid">${wowTooltip(it) || `<div class="muted">${t("Für dieses Item liegen keine Tooltip-Daten vor.")}</div>`}
      <table class="t kv"><tbody>
        ${[["Item-ID", id], [AHI18N.get() === "en" ? t("Deutscher Name") : t("Englischer Name"), esc((AHI18N.get() === "en" ? it.name : it.name_en) || "–")], [t("Qualität"), esc(QUAL(it.quality) || "–")], [t("Kategorie"), esc([AHI18N.trClass(it.class_name), AHI18N.trSubclass(it.subclass_name)].filter(Boolean).join(" › ") || "–")],
          [t("Gegenstandsstufe"), it.item_level || "–"], [t("Benötigte Stufe"), it.required_level || "–"], [t("Platz"), esc(it.slot ? AHI18N.trSlot(it.slot) : "–")],
          [t("Handelsware"), it.is_commodity ? t("ja (stapelweise gehandelt)") : t("nein")], [t("Händler-Verkaufspreis"), it.vendor_sell ? money(it.vendor_sell) : "–"]]
          .map(([k, v]) => `<tr class="nohover"><td class="muted">${k}</td><td>${v}</td></tr>`).join("")}
      </tbody></table></div>`;
  };
  if (!house) { wireFolds({ rec: recFold, all: allTable, info: infoBox }); return; }

  $("#rng").onclick = (e) => { const b = e.target.closest("button[data-r]"); if (b) setParams({ range: b.dataset.r }); };
  $("#tog").onclick = (e) => {
    const b = e.target.closest("button[data-k]"); if (!b) return;
    const k = b.dataset.k;
    if (k === "out") { setParams({ out: p.out === "0" ? "" : "0" }); return; }
    if (k === "cmp") { setParams({ cmp: overlay ? "" : "1" }); return; }
    const next = new Set((p.show || "min,median").split(","));
    next.has(k) ? next.delete(k) : next.add(k);
    next.add("min");
    setParams({ show: [...next].join(",") });
  };

  const anaRange = p.range === "24h" ? "7d" : p.range;
  const metric = p.metric || (house.last_median != null ? "median" : "min");
  const [hist, ana] = await Promise.all([
    api(`item/${id}/history`, { house: hid, range: p.range, max_points: 800 }),
    api(`item/${id}/analysis`, { house: hid, range: anaRange, metric, tz: TZ }),
  ]);
  const pts = hist.points;
  const span = pts.length ? pts[pts.length - 1].ts - pts[0].ts : 86400;

  // Preis-Chart (optional: alle Regelsätze der Fraktion überlagert)
  const ds = [];
  if (overlay) {
    const members = allSingles.map((h) => rowOf(h.id)).filter(Boolean);
    const series = await Promise.all(members.map((m) => api(`item/${id}/history`, { house: m.house_id, range: p.range, max_points: 400 })));
    members.forEach((m, i) => ds.push(line(GROUP_KINDS.includes(m.kind) ? `${facName(fac)} ${t("gesamt")}` : ruleName(m),
      series[i].points.map((x) => ({ x: x.ts, y: x.min, ts: x.ts, n: x.n ?? 1 })), SERIES[i], GROUP_KINDS.includes(m.kind) ? { borderWidth: 3 } : { borderWidth: 1.6 })));
  } else {
    for (const [k, l, c] of [["min", t("Referenz"), "--s1"], ["lowest", t("Günstigstes Angebot"), "--s6"], ["median", t("Median"), "--s2"]]) {
      if (!shown.has(k) || !pts.some((x) => x[k] != null)) continue;
      ds.push(line(l, pts.map((x) => ({ x: x.ts, y: x[k], ts: x.ts, n: x.n ?? 1 })), c));
    }
    if (p.out !== "0" && ana.outliers.length) {
      const lo = pts.length ? pts[0].ts : 0;
      const o = ana.outliers.filter((x) => x.ts >= lo);
      if (o.length) ds.push({ type: "scatter", label: t("Ausreißer"), data: o.map((x) => ({ x: x.ts, y: x.value, ts: x.ts })), pointRadius: 5, pointHoverRadius: 7,
        backgroundColor: css("--s8"), borderColor: css("--surface-1"), borderWidth: 2, pointStyle: "triangle" });
    }
  }
  $("#pcnote").textContent = overlay ? t("Referenzpreis je Regelsatz · Klick auf einen Eintrag in der Legende blendet ihn aus") : tf("{0} Reihenpunkte · ältere Punkte teils gemittelt; Lücken werden nicht überbrückt · Klick öffnet den Snapshot", hist.raw_points);
  if (pts.length < 2) $("#pc").parentElement.innerHTML = `<div class="empty">${pts.length ? t("Erst ein Datenpunkt – der Verlauf füllt sich stündlich.") : t("Keine Preisdaten im Zeitraum.")}</div>`;
  else if (!ds.length) $("#pc").parentElement.innerHTML = `<div class="empty">${t("Keine Kennzahl ausgewählt – über die Schalter oben einblenden.")}</div>`;
  else timeLineChart($("#pc"), ds, { span, onClick: overlay ? undefined : (pt) => (location.hash = `/snapshot/${hid}/${pt.ts}?item=${id}`) });

  // Aufklappbare Abschnitte
  const enough = (house.last_ts || 0) - (house.first_ts || house.last_ts || 0) >= 2 * 86400;
  const trnd = enough ? ana.trend || {} : {}, v = enough ? ana.volatility || {} : {};
  wireFolds({
    rec: recFold,
    kpi: (el) => {
      el.innerHTML = `<div class="mini">
        <div class="kpi"><div class="l">${t("Ø 7 Tage")}</div><div class="v">${money(house.avg7_min)}</div><div class="d">${t("Ø 30 T")} ${money(house.avg30_min)}</div></div>
        <div class="kpi"><div class="l">${t("Spanne der Referenzreihe (30 T)")}</div><div class="v">${money(house.low30_min)}</div><div class="d">${tf("bis {0} · ältere Punkte teils Tagesmittel, kein garantiertes Angebotstief", money(house.high30_min))}</div></div>
        <div class="kpi"><div class="l">${t("Änderung 7 Tage")}</div><div class="v">${pct(house.chg7d_min)}</div><div class="d">24 h ${pct(house.chg24_min)}</div></div>
        <div class="kpi" title="${tf("Lineare Regression auf ln(Preis) über {0}", anaRange)}"><div class="l">Trend</div><div class="v">${trnd.pct_per_day == null ? "–" : (trnd.pct_per_day > 0 ? "+" : "") + num(trnd.pct_per_day, 1) + " " + t("%/Tag")}</div><div class="d">${esc(trnd.direction ? AHI18N.trTrend(trnd.direction) : t("ab 2 Tagen Daten"))}</div></div>
        <div class="kpi" title="${t("Tägliche Schwankung (Std.-Abw. der Log-Renditen)")}"><div class="l">${t("Schwankung")}</div><div class="v">${v.daily == null ? "–" : num(v.daily * 100, 1) + " " + t("%/Tag")}</div><div class="d">${v.daily == null ? t("ab 2 Tagen Daten") : v.daily < 0.03 ? t("stabil") : v.daily < 0.1 ? t("normal") : t("stark")}</div></div>
        <div class="kpi"><div class="l">${t("Auktionen")}</div><div class="v">${num(house.last_auctions)}</div><div class="d">Median ${money(house.last_median)}</div></div>
      </div>
      <h3 style="margin:16px 0 6px">${t("Angebotsmenge")}</h3>
      <div class="chartbox sm"><canvas id="kq"></canvas></div>
      <h3 style="margin:16px 0 6px">${t("Wann ist es am günstigsten?")}</h3>
      <div class="grid g2"><div><h3>${t("Tageszeit")}</h3><div class="chartbox sm" id="hbox"><canvas id="hc"></canvas></div></div>
        <div><h3>${t("Wochentag")}</h3><div class="chartbox sm" id="wbox"><canvas id="wc"></canvas></div></div></div>`;
      if (pts.length) {
        makeChart($("#kq", el), {
          type: "bar",
          data: { datasets: [{ label: "Menge", data: pts.map((x) => ({ x: x.ts, y: x.quantity })), backgroundColor: css("--s1") + "99", borderRadius: 2, barPercentage: 1, categoryPercentage: 1 }] },
          options: {
            responsive: true, maintainAspectRatio: false, parsing: false,
            scales: { x: { type: "linear", ticks: { callback: timeTick(span), maxRotation: 0 }, grid: { display: false } }, y: { ticks: { callback: (v) => compact(v) } } },
            plugins: { legend: { display: false }, tooltip: { callbacks: { title: (i) => dt(i[0].parsed.x), label: (c) => ` ${t("Menge")}: ${num(c.parsed.y)}` } } },
          },
        });
      } else $("#kq", el).parentElement.innerHTML = `<div class="empty">${t("Keine Daten.")}</div>`;
      const se = ana.seasonality || {};
      if (se.hourly) divergingBars($("#hc", el), se.hourly.map((x) => x.hour + " h"), se.hourly.map((x) => x.pct ?? 0), t("Tageszeit"));
      else $("#hbox", el).innerHTML = `<div class="empty">${t("Ab 2 Tagen stündlicher Daten.")}</div>`;
      if (se.weekday) divergingBars($("#wc", el), se.weekday.map((x) => AHI18N.trWeekday(x.day)), se.weekday.map((x) => x.pct ?? 0), t("Wochentag"));
      else $("#wbox", el).innerHTML = `<div class="empty">${t("Ab 14 Tagen Historie.")}</div>`;
    },
    liq: (el) => {
      const days = (info.liquidity || {})[String(house.house_id)] || [];
      if (house.liq_score == null && !days.length) {
        el.innerHTML = `<div class="empty">${t("Für dieses Haus liegen noch keine Umsatzsignale vor. Die Auswertung entsteht aus den stündlichen Marktläufen und braucht einige Stunden Vorlauf.")}</div>`;
        return;
      }
      const kpis = house.liq_score != null ? `<div class="mini">
        <div class="kpi"><div class="l">${t("Umsatz-Score (7 Tage)")}</div><div class="v">${num(house.liq_score)} / 100</div><div class="d">${liqBadge(house)}</div></div>
        <div class="kpi"><div class="l">${t("Umsatzsignale")}</div><div class="v">${num(house.liq_events)}</div><div class="d">${tf("davon {0} komplette Ausverkäufe", num(house.liq_vanish))}</div></div>
        <div class="kpi"><div class="l">${t("Gelistet")}</div><div class="v">${num((house.liq_presence || 0) * 100, 0)} %</div><div class="d">${t("der geprüften Stunden-Snapshots")}</div></div>
        <div class="kpi"><div class="l">${t("Verschwundene Menge")}</div><div class="v">${num(house.liq_units)}</div><div class="d">${t("obere Schätzung, enthält Abläufe und Zurücknahmen")}</div></div>
      </div>` : "";
      const hist = days.length ? `<h3 style="margin:14px 0 6px">${t("Tägliche Signale")}</h3>` + table([
        { label: t("Tag"), fmt: (x) => new Date(x.day * 86400e3).toLocaleDateString(AHI18N.loc(), { weekday: "short", day: "2-digit", month: "2-digit" }) },
        { label: t("gelistet"), num: true, fmt: (x) => x.expected ? num(100 * x.present / x.expected, 0) + " %" : "–" },
        { label: t("Signale"), num: true, fmt: (x) => num(x.events) },
        { label: t("Ausverkäufe"), num: true, fmt: (x) => num(x.vanish) },
        { label: t("Menge"), num: true, fmt: (x) => num(x.units) }],
        [...days].reverse().slice(0, 14), { empty: t("Noch keine abgeschlossenen Tage.") }) : "";
      el.innerHTML = kpis + hist + `<p class="note" style="margin-top:10px">${t("Umsatzsignale entstehen aus beobachteten Angebotsbewegungen: Ausverkäufe und Mengeneinbrüche zwischen geprüften Stunden-Snapshots. Teilstände nach Abruffehlern sind ausgeklammert. Ein tatsächlicher Verkauf ist damit wahrscheinlich, aber nicht bewiesen – Auktionen können auch ablaufen oder zurückgenommen worden sein.")}</p>`;
    },
    all: allTable,
    raw: (el) => {
      el.innerHTML = `<h3 style="margin:2px 0 8px">${t("Ausreißer im Zeitraum")}</h3><div id="rawout"></div>
        <h3 style="margin:16px 0 8px">${t("Einzelne Snapshots")}</h3><div id="rawsnaps"><div class="loading">${t("Lade …")}</div></div>`;
      $("#rawout", el).innerHTML = table([
        { label: t("Zeitpunkt"), fmt: (o) => dt(o.ts) },
        { label: t("Art"), fmt: (o) => `<span class="${o.kind === "Spitze" ? "up" : "down"}">${o.kind === "Spitze" ? "▲" : "▼"} ${AHI18N.trOutlierKind(o.kind)}</span>` },
        { label: t("Wert"), num: true, fmt: (o) => money(o.value) },
        { label: t("Erwartet"), num: true, fmt: (o) => money(o.expected) },
        { label: t("Abw."), num: true, fmt: (o) => pct(o.deviation_pct, 0) }],
        ana.outliers.slice().reverse().slice(0, 10), { rowHref: (o) => `/snapshot/${hid}/${o.ts}?item=${id}`, empty: t("Keine Ausreißer im Zeitraum.") });
      const load = async (page) => {
        const s = await api(`item/${id}/snapshots`, { house: hid, page, page_size: 10 });
        $("#rawsnaps", el).innerHTML = table([
          { label: t("Zeitpunkt"), fmt: (x) => x.daily ? `${dt(x.ts, false)} <span class="pill" title="${tf("Mittel aus {0} Snapshots", x.n)}">${t("Tagesmittel")}</span>`
              : x.merged ? `${dt(x.ts)} <span class="pill" title="${tf("Mittel aus {0} stündlichen Snapshots", x.n)}">${tf("Ø {0} Std.", x.n)}</span>` : dt(x.ts) },
          { label: t("Referenz"), num: true, fmt: (x) => money(x.min) },
          { label: t("Median"), num: true, fmt: (x) => money(x.median) },
          { label: t("Menge"), num: true, fmt: (x) => num(x.quantity) },
          { label: t("Δ Referenz"), num: true, fmt: (x) => pct(x.chg_min) },
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
    <div class="crumbs"><a href="#/?house=${hid}">${esc(hname(houseById(hid)) || s.house.name)}</a> › ${t("Snapshots")} › ${dt(ts)}${f ? ` › <a href="#/item/${f.item.item_id}?house=${hid}">${esc(AHI18N.dispName(f.item))}</a>` : ""}</div>
    <div class="pagehead"><h1>Snapshot ${dt(ts)}</h1><span class="pill">${esc(hname(houseById(hid)) || s.house.name)}</span><span class="pill">${q ? t("Tageswert") : t("Stunden-Snapshot")}</span>
      <div class="actions">
        ${s.prev_ts ? `<a class="btn" href="#/snapshot/${hid}/${s.prev_ts}${f ? "?item=" + f.item.item_id : ""}">${t("‹ Vorheriger")}</a>` : ""}
        ${s.next_ts ? `<a class="btn" href="#/snapshot/${hid}/${s.next_ts}${f ? "?item=" + f.item.item_id : ""}">${t("Nächster ›")}</a>` : ""}
      </div></div>
    <div class="kpis">
      <div class="kpi"><div class="l">Items</div><div class="v">${num(s.snapshot.item_count)}</div></div>
      <div class="kpi"><div class="l">${t("Auktionen")}</div><div class="v">${num(s.snapshot.auction_count)}</div></div>
      <div class="kpi"><div class="l">${t("Marktwert")}</div><div class="v">${money(s.snapshot.total_value, { short: true })}</div></div>
      <div class="kpi"><div class="l">${t("Quelle")}</div><div class="v" style="font-size:16px">${esc(s.snapshot.source)}</div><div class="d">${t("importiert")} ${dt(s.snapshot.imported_at)}</div></div>
    </div>
    ${f ? `<div class="grid g32">
      <div class="card"><div class="cardhead"><h2>${itemLabel({ ...f.item, house_id: hid })} – ${t("Preisstaffel")}</h2></div>
        ${f.ladder ? `<div class="chartbox md"><canvas id="lc"></canvas></div><p class="muted small">${tf("Angebotene Menge je Preisstufe (günstigste {0} Stufen). Tooltip zeigt kumulierte Menge und Gesamtkosten bis zu dieser Stufe.", f.ladder.length)}</p>` :
          `<div class="note">${t("Für diesen Snapshot ist keine Preisstaffel gespeichert (nur für die jüngsten Snapshots der Live-Quelle bzw. nicht bei Auctionator-Tageswerten).")}</div>`}</div>
      <div class="card"><h2>${t("Kennzahlen")}</h2>${f.stats ? `<table class="t"><tbody>
        ${[[t("Referenz"), f.stats.min], [t("10 %-Perzentil"), f.stats.p10], [t("Median"), f.stats.median], [t("Mittel"), f.stats.mean], ["Max", f.stats.max]].map(([l, v]) => `<tr class="nohover"><td>${l}</td><td class="n">${money(v)}</td></tr>`).join("")}
        <tr class="nohover"><td>${t("Menge")}</td><td class="n">${num(f.stats.quantity)}</td></tr>
        <tr class="nohover"><td>${t("Auktionen")}</td><td class="n">${num(f.stats.auctions)}</td></tr>
        ${f.ladder ? `<tr class="nohover"><td>${t("Kosten für 20 Stück")}</td><td class="n">${money(buyCost(f.ladder, 20))}</td></tr><tr class="nohover"><td>${t("Kosten für 100 Stück")}</td><td class="n">${money(buyCost(f.ladder, 100))}</td></tr>` : ""}
        </tbody></table>` : `<div class="empty">${t("Item in diesem Snapshot nicht gelistet.")}</div>`}</div>
    </div>` : ""}
    <div class="card flush mt">
      <div class="cardhead" style="padding:14px 16px 0"><h2>${t("Alle Items in diesem Snapshot")}</h2>
        <form class="right" id="sf"><input type="search" name="q" value="${esc(p.q || "")}" placeholder="${t("Filtern …")}"></form></div>
      ${table([
        { label: "Item", sort: "name", cls: "itemcell", fmt: (x) => itemLabel({ ...x, house_id: hid }, false) },
        { label: t("Referenz"), num: true, sort: "price", fmt: (x) => money(x.min) },
        { label: t("Median"), num: true, fmt: (x) => money(x.median) },
        { label: t("Menge"), num: true, sort: "qty", fmt: (x) => num(x.quantity) },
        { label: t("Auktionen"), num: true, sort: "auctions", fmt: (x) => num(x.auctions) },
        { label: t("Δ ggü. vorher"), num: true, sort: "chg", fmt: (x) => pct(x.chg) },
        { label: t("Marktwert"), num: true, sort: "value", fmt: (x) => money(x.value, { short: true }) },
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
          { type: "bar", label: t("Menge"), data: f.ladder.map(([, qn]) => qn), backgroundColor: css("--s1"), borderRadius: 3, maxBarThickness: 22 },
        ],
      },
      options: { responsive: true, maintainAspectRatio: false, scales: { x: { ticks: { maxRotation: 50, autoSkip: true }, grid: { display: false } }, y: { ticks: { callback: (v) => compact(v) }, title: { display: true, text: t("Stück") } } },
        plugins: { legend: { display: false }, tooltip: { callbacks: {
          label: (c) => ` ${tf("{0} Stück zu {1}", num(c.parsed.y), moneyText(f.ladder[c.dataIndex][0]))}`,
          afterLabel: (c) => ` ${tf("kumuliert: {0} Stück für {1}", num(cumv[c.dataIndex]), moneyText(buyCost(f.ladder, cumv[c.dataIndex])))}` } } } },
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
    <div class="pagehead"><h1>${t("Vergleich")}</h1><span class="sub">${t("Bis zu 8 Items in einem Chart")}</span></div>
    <div class="card">
      <div class="cardhead">
        <div class="chips" id="chips"></div>
        <div class="right" style="position:relative"><input type="search" id="addItem" placeholder="${t("+ Item hinzufügen …")}" autocomplete="off"><div class="ac" id="addRes" hidden></div></div>
      </div>
      <div class="cardhead">
        <div class="seg" id="rng">${["24h", "7d", "30d", "all"].map((x) => `<button data-r="${x}" class="${x === p.range ? "on" : ""}">${x === "all" ? t("Alles") : x.replace("d", " T").replace("h", " h")}</button>`).join("")}</div>
        <div class="seg" id="met">${[["min", t("Referenz")], ["p10", "10 %"], ["median", t("Median")], ["mean", t("Mittel")], ["quantity", t("Menge")]].map(([k, l]) => `<button data-m="${k}" class="${k === p.metric ? "on" : ""}">${l}</button>`).join("")}</div>
        <div class="seg" id="mode"><button data-o="idx" class="${p.mode === "idx" ? "on" : ""}" title="${t("Alle Reihen auf 100 = erster Wert")}">Index (100)</button><button data-o="abs" class="${p.mode === "abs" ? "on" : ""}">${t("Absolut")}</button></div>
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
    $("#ctab").hidden = true; $("#corr").hidden = true;
    $("#chips").innerHTML = `<span class="muted">${t("Noch keine Items gewählt – über das Suchfeld rechts oder „+ Vgl.“ in Tabellen hinzufügen.")}</span>`;
    $("#cbox").innerHTML = `<div class="empty">${t("Kein Item ausgewählt.")}</div>`;
    return;
  }
  const d = await api("compare", { items: ids.join(","), house, range: p.range, metric: p.metric, max_points: 500 });
  $("#chips").innerHTML = d.series.map((s, i) => `<span class="chip"><span class="dot" style="background:var(${SERIES[i]})"></span>
     <a class="q${s.quality ?? 1}" href="#/item/${s.item_id}?house=${house}">${esc(AHI18N.dispName(s))}</a><button data-rm="${s.item_id}" title="${t("Entfernen")}">×</button></span>`).join("") +
    `<button class="btn small" id="clr">${t("Alle entfernen")}</button>`;
  $("#chips").onclick = (e) => {
    const b = e.target.closest("button[data-rm]");
    if (b) setIds(ids.filter((x) => x !== +b.dataset.rm));
    if (e.target.id === "clr") setIds([]);
  };
  const isQty = p.metric === "quantity";
  const idx = p.mode === "idx";
  const ds = d.series.map((s, i) => {
    const first = s.points.find((x) => x.v)?.v;
    return line(s.name, s.points.map((x) => ({ x: x.ts, y: x.v == null ? null : idx ? (first ? (x.v / first) * 100 : null) : x.v, n: x.n ?? 1 })), SERIES[i]);
  });
  const allPts = d.series.flatMap((s) => s.points);
  const span = allPts.length ? Math.max(...allPts.map((x) => x.ts)) - Math.min(...allPts.map((x) => x.ts)) : 86400;
  timeLineChart($("#cc"), ds, { span, yFmt: idx ? (v) => num(v, 0) : isQty ? (v) => compact(v) : moneyText, yTitle: idx ? t("Index (Start = 100)") : "" });
  const m = (s) => s.summary || {};
  $("#ctab").innerHTML = `<h2>${t("Kennzahlen")}</h2>` + table([
    { label: "", fmt: (s) => `<span class="sw" style="background:var(${SERIES[d.series.indexOf(s)]});width:18px"></span>` },
    { label: "Item", cls: "itemcell", fmt: (s) => itemLabel({ ...s, house_id: house }) },
    { label: t("Referenzpreis"), num: true, fmt: (s) => money(m(s).last_min) },
    { label: t("Median"), num: true, fmt: (s) => money(m(s).last_median) },
    { label: t("Ø 7 T"), num: true, fmt: (s) => money(m(s).avg7_min) },
    { label: "Δ 24 h", num: true, fmt: (s) => pct(m(s).chg24_min) },
    { label: t("Δ Zeitraum"), num: true, fmt: (s) => pct(s.describe.change_pct) },
    { label: "Trend", num: true, fmt: (s) => s.trend.pct_per_day == null ? "–" : `${s.trend.pct_per_day > 0 ? "+" : ""}${num(s.trend.pct_per_day, 2)} %/T` },
    { label: t("Volatilität"), num: true, fmt: (s) => s.volatility.daily == null ? "–" : num(s.volatility.daily * 100, 1) + " %/T" },
    { label: t("Menge"), num: true, fmt: (s) => num(m(s).last_qty) },
  ], d.series, { rowHref: (s) => `/item/${s.item_id}?house=${house}` });
  if (d.series.length > 1) {
    const cell = (v) => {
      if (v == null) return '<td class="muted">–</td>';
      const c = v >= 0 ? "var(--div-pos)" : "var(--div-neg)";
      return `<td style="background:color-mix(in srgb, ${c} ${Math.round(Math.abs(v) * 70)}%, var(--div-mid))">${num(v, 2)}</td>`;
    };
    $("#corr").innerHTML = `<div class="cardhead"><h2>${t("Korrelation der Preisänderungen")}</h2><span class="right muted small">${t("Pearson auf Log-Renditen · +1 gleichläufig, −1 gegenläufig")}</span></div>
      <div class="tablewrap"><table class="t matrix"><thead><tr><th></th>${d.series.map((s) => `<th>${esc(AHI18N.dispName(s))}</th>`).join("")}</tr></thead>
      <tbody>${d.correlation.map((row, i) => `<tr class="nohover"><th>${esc(AHI18N.dispName(d.series[i]))}</th>${row.map(cell).join("")}</tr>`).join("")}</tbody></table></div>`;
  } else $("#corr").hidden = true;
}

// ================================================================ Movers
// ============================================================== Liquidität
async function viewLiquidity(r) {
  const house = curHouse(r.params);
  const h = houseById(house);
  const p = { ...r.params };
  const classes = await getClasses(house);
  main.innerHTML = `
    <div class="goblin-heading"><div class="pagehead"><h1>${t("Liquidität")}</h1><span class="sub">${tf("Angebotsbewegungen · {0} · keine Verkaufsnachweise", esc(hname(h)))}</span></div>${goblinScene("head-goblin")}</div>
    <div class="card filters">
      <label class="f"><span>${t("Klasse")}</span><select id="cl"><option value="">${t("Alle")}</option>${classes.map((c) => `<option value="${c.class_id}" ${String(p.class) === String(c.class_id) ? "selected" : ""}>${esc(AHI18N.trClass(c.class_name))}</option>`).join("")}</select></label>
    </div>
    <div class="grid g2"><div class="card flush" id="hot"><div class="loading">…</div></div><div class="card flush" id="cold"><div class="loading">…</div></div></div>`;
  $("#cl").onchange = (e) => setParams({ class: e.target.value });
  const d = await api("liquidity", { ...p, house, limit: 40 });
  const listed = { label: t("gelistet"), num: true, title: t("Anteil der geprüften Stunden-Snapshots mit Angebot (7 Tage)"), fmt: (x) => num((x.liq_presence || 0) * 100, 0) + " %" };
  $("#hot").innerHTML = `<h2>${t("▲ Aktivste Angebotssignale")}</h2>` + table([COL.item, COL.liq,
    { label: t("Signale 7 T"), num: true, title: t("Ausverkäufe + Mengeneinbrüche zwischen geprüften Stunden-Snapshots"), fmt: (x) => num(x.liq_events) },
    { label: t("davon Ausverkäufe"), num: true, fmt: (x) => num(x.liq_vanish) },
    listed, COL.price, COL.cmp],
    d.hot, { rowHref: (x) => `/item/${x.item_id}?house=${x.house_id}`, empty: t("Noch keine Umsatzsignale für diesen Markt.") });
  $("#cold").innerHTML = `<h2>${t("Gelistet, wenige Angebotssignale in 7 Tagen")}</h2>` + table([COL.item, COL.liq,
    listed,
    { label: t("aktuelles Angebot"), num: true, fmt: (x) => money(x.last_min) },
    { label: t("Marktwert"), num: true, fmt: (x) => money(x.market_value, { short: true }) }, COL.cmp],
    d.cold, { rowHref: (x) => `/item/${x.item_id}?house=${x.house_id}`, empty: t("Keine Angebote ohne Umsatzsignale.") });
}

// ================================================================== Daten

function catalogCoverageCard(c) {
  if (!c) return `<div class="card mt"><h2>${t("Itemstamm")}</h2><p class="note">${t("Noch kein geprüfter Clientkatalog importiert.")}</p></div>`;
  return `<div class="card mt"><h2>${t("Itemstamm & Abdeckung")}</h2>
    <p>${t("Forever-Livebuild")} <b>${esc(c.build)}</b> · ${t("importiert")} ${dt(c.imported_at)}</p>
    <div class="grid g3">
      <div><b>${num(c.imported_named)} / ${num(c.readable_named)}</b><br>${t("lesbar benannte Clientitems importiert")}</div>
      <div><b>${num(c.search_items)}</b><br>${t("Items im gesamten Suchindex")}</div>
      <div><b>${num(c.unreadable)} / ${num(c.base_ids)}</b><br>${t("Basis-IDs ohne lesbaren Namensdatensatz")}</div>
    </div>
    <p class="small muted">${tf("DE-Namensfelder: {0} · EN-Namensfelder: {1}. Vorhandene Felder sind keine Garantie einer vollständigen Übersetzung. {2} Items im Gesamtindex ohne bestätigten DE-Namen.", num(c.de_names), num(c.en_names), num(c.missing_de_total))}</p>
    <p class="small">${tf("{0} benannte Clienteinträge tragen einen Test-/Alteintrag-Hinweis. {1} marktbeobachtete Items haben keinen lesbaren Datensatz in dieser Clientquelle; ihre bisherigen Daten bleiben erhalten. Unlesbare IDs werden gezählt, nicht als entfernte Items ausgegeben. Eine spätere Spielversion wird erst nach Prüfung freigegeben.", num(c.test_hints), num(c.observed_without_readable_client))}</p>
    <p class="note">${t("Vollständig bezogen auf die lesbar benannten Datensätze dieses Builds, nicht auf alle jemals erhältlichen oder handelbaren Items. Client-Existenz ist kein Drop- oder Handelsnachweis. Marktpreise kommen weiter ausschließlich aus Marktbeobachtungen.")}</p>
    <details><summary>${t("Quellen & Prüfsummen")}</summary><ul>${(c.sources || []).map((x) => `<li><a href="${esc(x.url)}" target="_blank" rel="noopener">${esc(x.table)} ${esc(x.locale)}</a> · ${tf("{0} Zeilen", num(x.rows))}<br><code style="overflow-wrap:anywhere">SHA-256 ${esc(x.sha256)}</code></li>`).join("")}</ul></details>
    <p class="small muted">${t("Spieldaten, Namen und Bilder © Blizzard Entertainment, Inc. Exportdienst:")} <a href="https://wago.tools/" target="_blank" rel="noopener">wago.tools</a>. ${t("Unabhängiges, nichtkommerzielles Fanprojekt, nicht von Blizzard unterstützt. Keine Übertragung von Rechten an Spieldaten.")} <a href="${esc(c.policy_url)}" target="_blank" rel="noopener">${t("Blizzard-Fanrichtlinien")}</a>.</p>
  </div>`;
}

function marketRunCard(runs) {
  const run = runs?.[0];
  if (!run) return `<div class="card mt"><h2>${t("Markt-Laufprotokoll")}</h2><p class="note">${t("Noch kein Lauf mit neuem Prüfmanifest. Ältere Snapshots haben keinen nachträglichen Vollständigkeitsnachweis.")}</p></div>`;
  const states = {complete:t("vollständig geprüft"),partial:t("teilweise geprüft"),rejected:t("nicht freigegeben"),running:t("läuft")};
  return `<div class="card mt"><h2>${t("Markt-Laufprotokoll")}</h2>
    <p><b>${tf("Lauf {0}: {1}", num(run.id), esc(states[run.status] || run.status))}</b> · ${dt(run.started_at)} · Region ${esc(run.region)}</p>
    <p class="note">${t("Preise sind nach Regelsatz und Fraktion getrennt. Fehlende Antworten gelten nicht als leere Märkte.")}</p>
    <p>${tf("{0} / {1} Endpunkte geprüft. Quellstände mit Angeboten dürfen höchstens 3 Stunden alt sein und innerhalb eines Hauses oder Sammelmarkts höchstens 10 Minuten auseinanderliegen.", num(run.endpoints?.filter(e=>["ok","empty","expected_inactive"].includes(e.status)).length), num(run.endpoints?.length))}</p>
    ${(run.errors || []).map(e=>`<p class="note">${esc(e)}</p>`).join("")}
    <details><summary>${t("Häuser, Endpunkte & Prüfsummen")}</summary>${table([
      {label:t("Haus / Endpunkt"),fmt:e=>`${num(e.house)} · ${esc(e.endpoint)}`},
      {label:t("Ergebnis"),fmt:e=>esc((e.status==="expected_inactive" ? t("erwartet inaktiv") : e.error) || (e.status==="empty" ? t("bestätigt leer") : e.status==="ok" ? t("geprüft") : t("Fehler")))},
      {label:t("Quellstand"),fmt:e=>dt(e.source_ts)},
      {label:t("Auktionen"),num:true,fmt:e=>e.auction_count==null ? "–" : num(e.auction_count)},
      {label:"SHA-256",fmt:e=>`<code title="${esc(e.sha256 || "")}">${esc((e.sha256 || "–").slice(0,12))}</code>`}
    ],run.endpoints || [])}<p class="small muted">${t("Jeder erfolgreiche Abruf speichert Inhaltshash, Last-Modified, ETag (falls geliefert), Quellzeit und Zählungen. Gleiche Quellzeit erzeugt keinen doppelten Snapshot. Fehlerhafte Hauspaare werden nicht übernommen; alle Marktschreibvorgänge eines Laufs sind atomar.")}</p></details>
  </div>`;
}

function marketQualityNote(q) {
  if (!q?.ts) return t("Kein geprüfter Marktstand vorhanden.");
  const age = Date.now()/1000-q.ts;
  return `${q.complete ? t("Vollständiger Snapshot") : t("Teilstand / Vollständigkeit nicht belegt")}${age>10800 ? " · " + t("älter als 3 Stunden") : ""}. ${tf("Letzter Gesamtlauf: {0}. Mengen und Werte beschreiben beobachtete Angebote, keinen Umsatz. Preise gelten nur für den gewählten Regelsatz.", q.latest_run_status || t("ohne Manifest"))}`;
}

function dataFlowCard() {
  return `<div class="card mt"><h2>${t("Woher kommen die Preise?")}</h2>
    <ol class="flow">
      <li><b>Blizzard</b> ${t("liefert die EU-Auktionshäuser (Battle.net API), zugeordnet zu Regelsatz und Fraktion.")}</li>
      <li><b>${t("Zusammenfassen:")}</b> ${t("Jeder Regelsatz hat eigene Preise je Fraktion. Es werden keine Preise zwischen Regelsätzen gemischt.")}</li>
      <li><b>${t("Veröffentlichen:")}</b> ${t("Die Seite wird danach automatisch neu gebaut. Quellzeit und Veröffentlichung werden getrennt ausgewiesen; die Laufzeit ist keine Frischegarantie.")}</li>
    </ol>
  </div>`;
}
async function viewData() {
  const [st, hs, lg] = await Promise.all([api("status"), api("houses"), api("import/log")]);
  const housesTable = () => table([
      { label: t("Bezeichnung"), fmt: (h) => `<span class="fac ${esc(h.faction)}"></span>${esc(hname(h))}` }, { label: t("Blizzard-Name"), fmt: (h) => `<span class="muted">${esc(h.kind === "haus" ? h.name : "–")}</span>` },
      { label: t("Region"), fmt: (h) => esc(h.region) }, { label: t("Fraktion"), fmt: (h) => esc(h.faction ? AHI18N.faction(h.faction) : "–") },
      { label: t("Auflösung"), fmt: (h) => (h.granularity === "day" ? t("Tag") : t("Stunde")) },
      { label: t("Snapshots"), num: true, fmt: (h) => num(h.snapshots) }, { label: "Items", num: true, fmt: (h) => num(h.items) },
      { label: t("Von"), fmt: (h) => dt(h.first_ts) }, { label: t("Bis"), fmt: (h) => dt(h.last_ts) }],
      hs.houses, { rowHref: (h) => `/?house=${h.id}` });
  if (st.static) {
    main.innerHTML = `
    <div class="pagehead"><h1>${t("Datenquellen")}</h1><span class="sub">${tf("Statische Version · Datenstand {0} ({1})", dt(st.generated_at), ago(st.generated_at))}</span></div>
    <div class="card"><h2>${t("Über diese Seite")}</h2>
      <p>${tf("Preisanalyse für das Auktionshaus von WoW: Forever (EU). Die Daten werden stündlich automatisch aktualisiert; Suche, Filter und Analysen laufen direkt im Browser. Historie: {0} Tage.", num(st.history_days))}</p>
    </div>
    ${dataFlowCard()}
    ${marketRunCard(st.market_runs)}
    ${catalogCoverageCard(st.catalog_coverage)}
    <div class="card flush mt"><h2>${t("Auktionshäuser")}</h2>${housesTable()}</div>
    <div class="card flush mt"><h2>${t("Import-Protokoll")}</h2>${table([
      { label: t("Zeit"), fmt: (x) => dt(x.ts) }, { label: t("Quelle"), fmt: (x) => esc(x.source) },
      { label: t("Meldung"), fmt: (x) => `<span class="small">${esc(x.message)}</span>` }], lg.log)}</div>`;
    return;
  }
  main.innerHTML = `
    <div class="pagehead"><h1>Datenquellen</h1><span class="sub">${num(st.counts.houses)} Häuser · ${num(st.counts.items)} Items · ${num(st.counts.snapshots)} Snapshots${st.db_size ? ` · DB ${num(st.db_size / 1048576, 1)} MB` : ""}</span></div>
    <div class="grid g2">
      <div class="card"><h2>Auctionator (Spielclient)</h2>
        <p class="muted">Liest die Preisdatenbank aus <code>WTF\\Account\\…\\SavedVariables\\Auctionator.lua</code> (Tageswerte: niedrigster/höchster Referenzpreis, Menge). Vorher im Spiel einen Scan im Auktionshaus durchführen und <b>/reload</b> oder ausloggen, damit WoW die Datei schreibt.</p>
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
    ${marketRunCard(st.market_runs)}
    ${catalogCoverageCard(st.catalog_coverage)}
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
    if (!items.length) { box.innerHTML = `<div class="acempty">${tf("Keine Treffer für „{0}“", esc(lastQ))}</div>`; return; }
    box.innerHTML = items.map((it, i) => {
      const cat = [AHI18N.trClass(it.class_name), AHI18N.trSubclass(it.subclass_name)].filter(Boolean).map(esc).join(" › ");
      const en = it.name_en && it.name_en !== it.name ? `<span class="acen">${hl(it.name_en, lastQ)}</span> · ` : "";
      return `<a href="#/item/${it.item_id}${it.house_id ? "?house=" + it.house_id : ""}" data-i="${i}" class="acrow ${i === sel ? "sel" : ""}">
        ${icon(it, "md")}
        <span class="acmain"><span class="acname q${it.quality ?? 1}">${hl(it.name, lastQ)}</span>
          <span class="acsub">${en}${(it.aliases || []).length ? esc(it.aliases.join(", ")) + " (" + t("Suchalias") + ") · " : ""}${it.translation_missing && AHI18N.get() === "de" ? "DE-Name fehlt · " : ""}${cat || t("Kategorie unbekannt")} · ID ${it.item_id}</span></span>
        <span class="acprice">${it.no_ah ? `<span class="acsub">${esc(listingNote(it))}</span>` : `${money(it.last_min)}<span class="acsub">${it.is_current ? t("letzter Snapshot") : t("historisch")}</span>`}</span></a>`;
    }).join("") + (opts.showAll !== false && total > items.length
      ? `<a href="#" data-all class="acall ${sel === items.length ? "sel" : ""}">${tf("Alle {0} Treffer anzeigen ›", num(total))}</a>` : "");
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
  legacyAliases = d.legacy_aliases || {};
  state.house = resolveHouseId(state.house);
  state.houses = d.houses.filter((h) => h.snapshots > 0);
  if (!state.house || !houseById(state.house)) state.house = d.default ? String(d.default) : "";
  renderMarket();
}
function renderMarket() {
  const h = houseById(state.house);
  const sel = $("#rulesetSelect");
  if (sel) {
    sel.innerHTML = ["normal","pvp","rp","rppvp"].map(key=>{
      const r = state.houses.find(x=>x.ruleset_key===key);
      return r ? `<option value="${key}" ${key===rulesetKey()?"selected":""}>${esc(ruleName(r))}</option>` : "";
    }).join("");
    sel.onchange = () => {
      const target = state.houses.find(x=>x.ruleset_key===sel.value && x.group===(h?.group || "allianz"));
      if (target) setHouse(target.id);
    };
  }
  $$("#market button").forEach((b) => {
    b.hidden = !groupId(b.dataset.k);
    b.classList.toggle("on", !!h && h.group === b.dataset.k);
  });
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
  el.textContent = `${t("Stand:")} ${ago(h.last_ts)}`;
  el.title = `${t("Letzter Datenpunkt:")} ${dt(h.last_ts)}${h.granularity === "day" ? " " + t("(Tageswerte aus Auctionator)") : ""}${STATIC ? " · " + t("statischer Export") : ""}`;
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

function applyStaticStrings() {
  document.documentElement.lang = AHI18N.get();
  document.title = t("AH Analytics – WoW Forever Auktionshaus-Preise");
  for (const el of $$("[data-i18n]")) {
    const v = t(el.dataset.i18n);
    if (el.dataset.i18nAttr) el.setAttribute(el.dataset.i18nAttr, v);
    else el.textContent = v;
    if (el.dataset.i18nAria) el.setAttribute("aria-label", t(el.dataset.i18nAria));
  }
  for (const el of $$("[data-i18n-ph]")) el.placeholder = t(el.dataset.i18nPh);
}

function initLang() {
  const box = $("#langSwitch");
  if (!box) return;
  const drawLang = () => {
    box.querySelectorAll("button").forEach((b) => b.classList.toggle("on", b.dataset.lang === AHI18N.get()));
  };
  box.addEventListener("click", (e) => {
    const b = e.target.closest("button[data-lang]");
    if (b) AHI18N.set(b.dataset.lang);
  });
  AHI18N.onChange(() => {
    applyStaticStrings();
    initTheme();
    renderMarket();
    drawLang();
    route();
  });
  drawLang();
}

(async function init() {
  // Echte Item-URLs (/item/<id>/ = EN, /de/item/<id>/ = DE) vor dem ersten relativen Fetch
  // auf die Hash-Route abbilden; die statisch gerenderte Seite bleibt bis zur Route sichtbar.
  const bootItem = location.pathname.match(/\/(de\/)?item\/(\d+)\/?$/);
  if (bootItem && !location.hash) {
    const root = location.pathname.replace(/\/(de\/)?item\/\d+\/?$/, "/");
    history.replaceState(null, "", root + "#/item/" + bootItem[2]);
  }
  initTheme();
  initLang();
  applyStaticStrings();
  updateCompareBadge();
  STATIC = await StaticAPI.detect();
  try { await loadHouses(); } catch (e) { main.innerHTML = `<div class="error">${t("Server nicht erreichbar:")} ${esc(e.message)}</div>`; return; }
  $("#market").addEventListener("click", (e) => { const b = e.target.closest("button[data-k]"); if (b) setHouse(groupId(b.dataset.k)); });
  attachAutocomplete($("#quickInput"), $("#quickResults"), null, (it) => (location.hash = `/item/${it.item_id}${it.house_id ? "?house=" + it.house_id : ""}`));
  $("#quickSearch").onsubmit = (e) => { e.preventDefault(); const q = $("#quickInput").value.trim(); if (q) { $("#quickResults").hidden = true; go("/search", { q, house: state.house }); } };
  window.addEventListener("hashchange", route);
  route();
  if (STATIC) watchVersion();
})();

/** Offene Tabs: nach einem Update der Website einen Hinweis zum Neuladen zeigen. */
function watchVersion() {
  const mine = document.querySelector('meta[name="app-version"]')?.content;
  if (!mine) return;
  let shown = false, last = 0;
  const check = async () => {
    if (shown || Date.now() - last < 5 * 60 * 1000) return;
    last = Date.now();
    try {
      const m = await fetch(`data/meta.json?t=${Date.now()}`, { cache: "no-store" }).then((r) => r.json());
      if (m.app_version && m.app_version !== mine) {
        shown = true;
        document.body.insertAdjacentHTML("beforeend", `<div class="newver" role="status">${t("Neue Version der Website verfügbar.")}
          <button type="button" class="btn primary small" onclick="location.reload()">${t("Neu laden")}</button></div>`);
      }
    } catch { /* offline – später erneut */ }
  };
  setInterval(check, 5 * 60 * 1000);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) check(); });
    }

