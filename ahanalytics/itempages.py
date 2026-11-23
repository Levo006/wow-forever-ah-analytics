"""Statische Item-Seiten mit echten, suchmaschinenlesbaren URLs (EN-first).

Google sieht die Hash-Routen der App (#/item/<id>) nicht als eigene Seiten.
Dieser Export schreibt deshalb je Item zwei echte HTML-Seiten in den
Seiten-Ordner, die GitHub Pages direkt ausliefert:

    item/<id>/index.html     englisch (Haupt-URL, hreflang x-default)
    de/item/<id>/index.html  deutsch

Jede Seite ist die normale App-Shell mit eigenem <title>, Meta-Description,
canonical + hreflang und sichtbarem statischem Inhalt (Name, Icon, Preise,
Kennzahlen). Beim Laden bildet app.js die Pfad-URL auf die Hash-Route ab und
die bekannte interaktive Item-Ansicht uebernimmt; ohne JavaScript bleibt der
statische Inhalt lesbar.

Zusaetzlich werden die Sitemaps geschrieben: sitemap.xml als Index,
sitemap-pages.xml (Startseite) und sitemap-items-<lang>-<n>.xml mit je
hoechstens 10.000 Item-URLs (EN zuerst).
"""
from __future__ import annotations

import html
import json
import os
import re
import time

from .rulesets import RULESETS, FACTIONS

BASE = "https://forever-analytics.eu"
SITEMAP_CHUNK = 10_000

QUAL_DE = ["Schlecht", "Gewöhnlich", "Ungewöhnlich", "Selten", "Episch", "Legendär", "Artefakt", "Erbstück"]
QUAL_EN = ["Poor", "Common", "Uncommon", "Rare", "Epic", "Legendary", "Artifact", "Heirloom"]
BIND_DE = {1: "Wird beim Aufheben gebunden", 2: "Wird beim Anlegen gebunden", 3: "Wird bei Benutzung gebunden", 4: "Questgegenstand"}
BIND_EN = {1: "Binds when picked up", 2: "Binds when equipped", 3: "Binds when used", 4: "Quest item"}

# Sammelhäuser je Fraktion (ext_id) für die Preisübersicht: ext_id -> (de, en)
AGG_HOUSES = tuple((f"eu-{num}", f"{key}:{fac}", fac)
                   for key, (_, _, ids) in RULESETS.items() for fac, num in zip(FACTIONS, ids))

_BOOT_HINT = (
    "<script>try{var m=location.pathname.match(/\\/(de\\/)?item\\/(\\d+)\\/?$/);"
    "if(m)sessionStorage.setItem(\"ah.bootlang\",m[1]?\"de\":\"en\");}catch(e){}</script>"
)

_CSS = """<style>
.ipp{max-width:720px;margin:20px auto;padding:0 14px}
.ipp h1{display:flex;align-items:center;gap:10px;font-size:1.5rem;margin:14px 0 4px}
.ipp-ico{width:36px;height:36px;border-radius:6px;flex:none}
.ipp-sub{opacity:.75;margin:0 0 14px}
.ipp table{border-collapse:collapse;margin:6px 0 4px}
.ipp th,.ipp td{padding:5px 16px 5px 0;text-align:left;border-bottom:1px solid rgba(128,128,128,.25)}
.ipp td.n,.ipp th.n{text-align:right;font-variant-numeric:tabular-nums}
.ipp-stand{font-size:.85rem;opacity:.65;margin:4px 0 16px}
.ipp ul{margin:0 0 16px;padding-left:20px;line-height:1.5}
.ipp-links{display:flex;gap:16px;flex-wrap:wrap;margin-top:8px}
.ipp-top{display:flex;justify-content:space-between;font-size:.9rem;opacity:.8}
</style>"""


def esc(s) -> str:
    return html.escape(str(s), quote=True)


def money_text(c) -> str | None:
    """Kupferbetrag als Kurztext wie moneyText() in app.js: '144s 12c', '3g 45s'."""
    if c is None:
        return None
    c = int(round(c))
    g, r = divmod(c, 10000)
    s, k = divmod(r, 100)
    if g >= 100:
        return f"{g:,}g".replace(",", ".")
    if g:
        return f"{g}g {s}s"
    if s:
        return f"{s}s {k}c"
    return f"{k}c"


def icon_url(icon) -> str | None:
    if not icon:
        return None
    icon = str(icon)
    if icon.startswith(("http://", "https://")):
        return icon
    return "https://wow.zamimg.com/images/wow/icons/medium/" + icon + ".jpg"


def _en_maps(static_src: str) -> dict:
    """CLASS_EN/SUB_EN/SLOT_EN aus static/i18n.js (einzige gepflegte Quelle) lesen."""
    path = os.path.join(static_src, "i18n.js")
    maps = {}
    try:
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        for name in ("CLASS_EN", "SUB_EN", "SLOT_EN"):
            m = re.search(r"const %s = (\{.*?\});" % name, src, re.S)
            maps[name] = json.loads(m.group(1)) if m else {}
    except Exception:  # noqa: BLE001 - fehlende Übersetzung ist kein Export-Stopper
        maps = {"CLASS_EN": {}, "SUB_EN": {}, "SLOT_EN": {}}
    return maps


def _template(shell: str) -> str:
    """Die (bereits versionierte) index.html-Shell einmalig in eine Token-Vorlage verwandeln."""
    s = shell
    s = re.sub(r'<html lang="[^"]*"', '<html lang="%%LANG%%"', s, count=1)
    s = re.sub(r"<title>.*?</title>", "<title>%%TITLE%%</title>", s, count=1, flags=re.S)
    s = re.sub(r'<meta name="description" content="[^"]*"', '<meta name="description" content="%%DESC%%"', s, count=1)
    s = re.sub(r'<link rel="canonical" href="[^"]*">', '<link rel="canonical" href="%%CANONICAL%%">\n  %%HREFLANG%%', s, count=1)
    for prop in ("og:title", "og:description", "og:url", "og:image"):
        s = re.sub(r'<meta property="%s" content="[^"]*">' % re.escape(prop),
                   '<meta property="%s" content="%%%%%s%%%%">' % (prop, prop.replace(":", "_").upper()), s, count=1)
    s = re.sub(r'\s*<meta property="og:image:(width|height)" content="[^"]*">', "", s)
    s = re.sub(r'<meta property="og:locale" content="[^"]*">', '<meta property="og:locale" content="%%OGLOCALE%%">', s, count=1)
    s = re.sub(r'<meta property="og:locale:alternate" content="[^"]*">', '<meta property="og:locale:alternate" content="%%OGLOCALEALT%%">', s, count=1)
    # Relative Referenzen (keine http:, #, /, data:, keine %%TOKENS%%) mit Tiefenpräfix versehen
    s = re.sub(r'(src|href)="(?!https?:|#|/|data:|%)([^"]+)"', r'\1="%%REL%%\2"', s)
    s = s.replace("</head>", _BOOT_HINT + "\n</head>", 1)
    m = re.search(r'(<main id="main"[^>]*>).*?</main>', s, flags=re.S)
    if not m:
        raise RuntimeError("index.html: <main id=\"main\"> nicht gefunden – Item-Seiten können nicht gerendert werden")
    s = s[:m.start(1)] + m.group(1) + "%%MAIN%%</main>" + s[m.end():]
    return s


def _render(tpl: str, tokens: dict) -> str:
    out = tpl
    for k, v in tokens.items():
        out = out.replace("%%" + k + "%%", v)
    return out


def render_page(tpl: str, it: dict, prices: dict, lang: str, maps: dict) -> tuple[str, str, str]:
    """Eine Item-Seite rendern. Rückgabe: (HTML, Dateipfad-Anteil, item_id)."""
    en = lang == "en"
    iid = it["id"]
    sub, rel = ("", "../../") if en else ("de/", "../../../")
    title, desc = _desc_and_title(it, prices, lang)
    canonical = f"{BASE}/{sub}item/{iid}/"
    other = f"{BASE}/{'de/' if en else ''}item/{iid}/"
    hreflang = (
        f'<link rel="alternate" hreflang="{lang}" href="{canonical}">\n'
        f'  <link rel="alternate" hreflang="{"de" if en else "en"}" href="{other}">\n'
        f'  <link rel="alternate" hreflang="x-default" href="{BASE}/item/{iid}/">')
    tokens = {
        "LANG": lang, "TITLE": esc(title), "DESC": esc(desc), "CANONICAL": canonical,
        "HREFLANG": hreflang, "REL": rel,
        "OG_TITLE": esc(title), "OG_DESCRIPTION": esc(desc), "OG_URL": canonical,
        "OG_IMAGE": esc(icon_url(it.get("icon")) or f"{BASE}/assets/og-image.jpg"),
        "OGLOCALE": "en_US" if en else "de_DE",
        "OGLOCALEALT": "de_DE" if en else "en_US",
        "MAIN": _body(it, prices, lang, maps),
    }
    return _render(tpl, tokens), os.path.join(sub, "item", str(iid), "index.html"), iid


def _details_lines(details_json, limit=12) -> list[str]:
    try:
        d = json.loads(details_json or "{}")
    except Exception:  # noqa: BLE001
        return []
    out = []
    for line in (d.get("lines") or [])[:limit]:
        t = str(line.get("t") or "").strip()
        r = str(line.get("r") or "").strip()
        if t:
            out.append(esc(t) + (f' <span style="opacity:.65">· {esc(r)}</span>' if r else ""))
    return out


def _price_rows(prices: dict, lang: str) -> tuple[str, int | None]:
    """Tabellenzeilen je Fraktion + jüngster Stand. prices: faction_de -> summary dict."""
    rows, latest = [], None
    for key, (de, en, _) in RULESETS.items():
        for fac in FACTIONS:
            p = prices.get(f"{key}:{fac}")
            if not p: continue
            latest = max(latest or 0, p.get("last_ts") or 0) or None
            faction = {"Allianz":"Alliance", "Neutral":"Blackwater Raiders"}.get(fac,fac) if lang=="en" else ("Schwarzmeerräuber" if fac=="Neutral" else fac)
            label = (en if lang=="en" else de) + " · " + faction
            mn, md, qty = money_text(p.get("last_min")), money_text(p.get("last_median")), p.get("last_qty")
            rows.append(f'<tr><td>{esc(label)}</td><td class="n">{esc(mn or "–")}</td><td class="n">{esc(md or "–")}</td><td class="n">{qty if qty is not None else "–"}</td></tr>')
    return "".join(rows), latest


def _desc_and_title(it: dict, prices: dict, lang: str) -> tuple[str, str]:
    name = (it.get("name_en") or it.get("name")) if lang == "en" else (it.get("name") or it.get("name_en"))
    name = name or f"Item #{it['id']}"
    cls = it.get("class_name") or ""
    if lang == "en":
        title = f"{name} – Prices by Ruleset | Forever AH-Analytics"
        if prices:
            parts = [f"{fac_en} {money_text(prices['normal:' + fac_de].get('last_min'))}"
                     for fac_de, fac_en in (("Allianz", "Alliance"), ("Horde", "Horde"), ("Neutral", "Neutral"))
                     if prices.get("normal:" + fac_de) and prices['normal:' + fac_de].get("last_min") is not None]
            desc = (f"{name} auction house prices in WoW: Forever – Normal ruleset: " + ", ".join(parts) +
                    ". Separate PvP, Roleplay and Roleplay-PvP prices on this page. Updated hourly.")
        else:
            desc = (f"{name} ({cls}) in WoW: Forever – item details, auction house prices "
                    f"and price history as soon as it is listed.")
    else:
        title = f"{name} – Preise je Regelsatz | Forever AH-Analytics"
        if prices:
            parts = [f"{fac_de} {money_text(prices['normal:' + fac_de].get('last_min'))}"
                     for fac_de in ("Allianz", "Horde", "Neutral")
                     if prices.get("normal:" + fac_de) and prices['normal:' + fac_de].get("last_min") is not None]
            desc = (f"{name}: Auktionshaus-Preise in WoW: Forever – Regelsatz Normal: " + ", ".join(parts) +
                    ". PvP, Rollenspiel und Rollenspiel-PvP separat auf dieser Seite. Stündlich aktualisiert.")
        else:
            desc = (f"{name} ({cls}) in WoW: Forever – Item-Details, Auktionshaus-Preise "
                    f"und Verlauf, sobald gelistet.")
    return title, desc


def _body(it: dict, prices: dict, lang: str, maps: dict) -> str:
    en = lang == "en"
    iid = it["id"]
    name = (it.get("name_en") or it.get("name")) if en else (it.get("name") or it.get("name_en"))
    name = name or f"Item #{iid}"
    other_name = (it.get("name") if en else it.get("name_en")) or ""
    q = it.get("quality") if it.get("quality") is not None else 1
    qual = (QUAL_EN if en else QUAL_DE)[q] if 0 <= q <= 7 else ""
    cls = it.get("class_name") or ""
    sub = it.get("subclass_name") or ""
    slot = it.get("slot") or ""
    if en:
        cls = maps["CLASS_EN"].get(cls, cls)
        sub = maps["SUB_EN"].get(sub, sub)
        slot = maps["SLOT_EN"].get(slot, slot)
    binding = (BIND_EN if en else BIND_DE).get(it.get("binding") or 0, "")

    facts = [x for x in (cls, sub, qual,
                         (f"{'Item level' if en else 'Gegenstandsstufe'} {it['item_level']}" if it.get("item_level") else ""),
                         (f"{'Requires level' if en else 'Benötigt Stufe'} {it['required_level']}" if it.get("required_level") else ""),
                         slot, binding) if x]
    subline = esc(" · ".join(str(f) for f in facts))
    if en and other_name and other_name != name:
        subline += f' <span style="opacity:.6">· Deutsch: {esc(other_name)}</span>'
    elif not en and other_name and other_name != name:
        subline += f' <span style="opacity:.6">· English: {esc(other_name)}</span>'

    icon = icon_url(it.get("icon"))
    img = f'<img class="ipp-ico" src="{esc(icon)}" alt="" width="36" height="36">' if icon else ""

    rows, latest = _price_rows(prices, lang)
    if rows:
        head = (f"<tr><th>{'Ruleset · Faction' if en else 'Regelsatz · Fraktion'}</th><th class=\"n\">{'Low' if en else 'Min'}</th>"
                f"<th class=\"n\">{'Median' if en else 'Median'}</th><th class=\"n\">{'Supply' if en else 'Menge'}</th></tr>")
        price_html = f'<p>{"Separate markets for each ruleset. Trading is only possible within your own ruleset." if en else "Getrennte Märkte je Regelsatz. Handel nur innerhalb desselben Regelsatzes."}</p><table>{head}{rows}</table>'
        stand = time.strftime("%d.%m.%Y, %H:%M", time.localtime(latest)) if latest else ""
        price_html += (f'<p class="ipp-stand">{"Prices as of" if en else "Preise vom"} {stand} · '
                       f'{"updated hourly from the Battle.net API" if en else "stündlich aus der Battle.net API"}</p>')
    else:
        price_html = (f'<p>{"Not seen in the auction house yet." if en else "Noch nicht im Auktionshaus gesehen."}</p>')

    lines = _details_lines(it.get("details_en") if en else it.get("details"))
    details_html = "<ul>" + "".join(f"<li>{l}</li>" for l in lines) + "</ul>" if lines else ""

    rel = "../../../" if not en else "../../"
    interactive = f'<a href="{rel}#/item/{iid}">{"Open interactive charts & analysis" if en else "Interaktive Preis-Charts & Analysen"} →</a>'
    wh = f'<a href="https://www.wowhead.com/forever/{"item" if en else "de/item"}={iid}" rel="noopener">Wowhead</a>'
    home = f'<a href="{rel}">Forever AH-Analytics</a>'
    # Sprachwechsel: EN-Seite (../../) -> de/item/<id>/ ; DE-Seite (../../../) -> item/<id>/
    switch = (f'<a href="{rel}de/item/{iid}/">Deutsch</a>' if en
              else f'<a href="{rel}item/{iid}/">English</a>')

    return (f'{_CSS}<div class="ipp"><div class="ipp-top">{home}{switch}</div>'
            f'<h1>{img}<span class="q{q}">{esc(name)}</span></h1>'
            f'<p class="ipp-sub">{subline}</p>'
            f"{price_html}{details_html}"
            f'<p class="ipp-links">{interactive}{wh}</p></div>')


def export_item_pages(con, out_dir: str, static_src: str, log=print) -> dict:
    """Alle Item-Seiten + Sitemaps schreiben. Rückgabe: Statistik."""
    t0 = time.time()
    with open(os.path.join(out_dir, "index.html"), encoding="utf-8") as fh:
        tpl = _template(fh.read())
    maps = _en_maps(static_src)

    agg_ids = {}
    for ext, fac_de, _fac_en in AGG_HOUSES:
        row = con.execute("SELECT id FROM houses WHERE source='battlenet' AND ext_id=?", (ext,)).fetchone()
        if row:
            agg_ids[row[0]] = fac_de
    prices_by_item: dict[int, dict] = {}
    if agg_ids:
        q = (f"SELECT item_id, house_id, last_min, last_median, last_qty, last_ts FROM item_summary "
             f"WHERE house_id IN ({','.join(str(i) for i in agg_ids)}) AND last_ts=(SELECT MAX(ts) FROM snapshots WHERE snapshots.house_id=item_summary.house_id)")
        for r in con.execute(q):
            prices_by_item.setdefault(r[0], {})[agg_ids[r[1]]] = {
                "last_min": r[2], "last_median": r[3], "last_qty": r[4], "last_ts": r[5]}

    items = [dict(r) for r in con.execute(
        """SELECT i.id, i.name, i.name_en, i.quality, i.class_name, i.subclass_name, i.icon,
                  i.item_level, i.required_level, i.slot, i.details, i.details_en, i.binding
           FROM items i ORDER BY i.id""")]

    item_ids = []
    for n, it in enumerate(items):
        iid = it["id"]
        item_ids.append(iid)
        prices = prices_by_item.get(iid, {})
        for lang in ("en", "de"):
            page, rel_path, _ = render_page(tpl, it, prices, lang, maps)
            path = os.path.join(out_dir, rel_path)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(page)
        if n and n % 5000 == 0:
            log(f"  {n} Item-Seiten geschrieben …")

    sm = write_sitemaps(out_dir, item_ids)
    stats = {"item_pages": 2 * len(items), "items": len(items),
             "with_prices": sum(1 for it in items if prices_by_item.get(it["id"])),
             "sitemap_files": sm, "seconds": round(time.time() - t0, 1)}
    log(f"Item-Seiten: {stats['items']} Items × 2 Sprachen = {stats['item_pages']} Seiten "
        f"({stats['with_prices']} mit Preisen), {sm} Sitemap-Dateien in {stats['seconds']} s")
    return stats


def _urlset(locs: list[str]) -> str:
    body = "".join(f"  <url><loc>{esc(u)}</loc></url>\n" for u in locs)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + body + "</urlset>\n")


def write_sitemaps(out_dir: str, item_ids: list[int]) -> int:
    """sitemap.xml (Index) + URL-Dateien schreiben. EN zuerst. Rückgabe: Anzahl Dateien."""
    files = ["sitemap-pages.xml"]
    with open(os.path.join(out_dir, "sitemap-pages.xml"), "w", encoding="utf-8") as fh:
        fh.write(_urlset([BASE + "/"]))
    for prefix in ("", "de/"):  # EN-Haupt-URLs zuerst
        lang = "en" if not prefix else "de"
        chunk, idx = [], 1
        for iid in item_ids:
            chunk.append(f"{BASE}/{prefix}item/{iid}/")
            if len(chunk) >= SITEMAP_CHUNK:
                name = f"sitemap-items-{lang}-{idx}.xml"
                with open(os.path.join(out_dir, name), "w", encoding="utf-8") as fh:
                    fh.write(_urlset(chunk))
                files.append(name)
                chunk, idx = [], idx + 1
        if chunk:
            name = f"sitemap-items-{lang}-{idx}.xml"
            with open(os.path.join(out_dir, name), "w", encoding="utf-8") as fh:
                fh.write(_urlset(chunk))
            files.append(name)
    idx_body = "".join(f"  <sitemap><loc>{BASE}/{f}</loc></sitemap>\n" for f in files)
    with open(os.path.join(out_dir, "sitemap.xml"), "w", encoding="utf-8") as fh:
        fh.write('<?xml version="1.0" encoding="UTF-8"?>\n'
                 '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
                 + idx_body + "</sitemapindex>\n")
    return len(files) + 1
