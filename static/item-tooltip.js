/* Inline item text, Forever only. External markup is allowlist-sanitized before display. */
(() => {
  'use strict';
  const T = (s) => (window.AHI18N ? AHI18N.t(s) : s);
  const cache = new Map();
  const loc = () => (window.AHI18N && AHI18N.get() === 'en' ? 0 : 3);
  function sanitize(html) {
    const doc = new DOMParser().parseFromString(String(html), 'text/html');
    const allowed = new Set(['TABLE','TBODY','THEAD','TR','TD','TH','DIV','SPAN','B','STRONG','I','EM','BR','SMALL','A']);
    const bad = new Set(['SCRIPT','STYLE','IFRAME','OBJECT','EMBED','FORM','INPUT','BUTTON','LINK','META','IMG','SVG','MATH']);
    const result = document.createDocumentFragment();
    const copy = (node, parent) => {
      if (node.nodeType === Node.TEXT_NODE) { parent.append(document.createTextNode(node.textContent)); return; }
      if (node.nodeType !== Node.ELEMENT_NODE || bad.has(node.tagName)) return;
      if (!allowed.has(node.tagName)) { for (const child of node.childNodes) copy(child, parent); return; }
      const el = document.createElement(node.tagName.toLowerCase());
      const classes = [...node.classList].filter(c => /^(?:q[0-9]?|moneygold|moneysilver|moneycopper|whtt-sellprice|whtt-extra|whtt-spells)$/.test(c));
      if (classes.length) el.className = classes.join(' ');
      if (node.tagName === 'A') { try { const u = new URL(node.getAttribute('href'), 'https://www.wowhead.com'); if (u.protocol === 'https:' && u.hostname === 'www.wowhead.com' && u.pathname.startsWith('/forever/')) {el.href=u.href;el.target='_blank';el.rel='noopener';} } catch {} }
      for (const child of node.childNodes) copy(child, el);
      parent.append(el);
    };
    for (const child of doc.body.childNodes) copy(child, result);
    return result;
  }
  function load(id) {
    const key = `${id}:${loc()}`;
    if (!cache.has(key)) cache.set(key, fetch(`https://nether.wowhead.com/tooltip/item/${id}?dataEnv=16&locale=${loc()}`, {signal:AbortSignal.timeout(9000)})
      .then(r => {if (!r.ok) throw new Error(T('Nicht verfügbar')); return r.json();})
      .then(d => {if (typeof d.tooltip !== 'string' || !d.tooltip.trim() || d.error) throw new Error(T('Kein Originaltext')); return d;})
      .catch(e => {cache.delete(key);throw e;}));
    return cache.get(key);
  }
  async function fill(target, id) {
    target.setAttribute('aria-busy','true');target.dataset.tooltipRequest=String(id);
    try {
      const d = await load(id);if(target.dataset.tooltipRequest!==String(id))return false;
      const box = document.createElement('div');box.className='wtip original-tooltip';box.append(sanitize(d.tooltip));
      target.replaceChildren(box);
      const note = document.createElement('p');note.className='tooltip-provenance';note.textContent=T('Original-Itemtext · Wowhead Forever · Sprache der Anzeige');target.append(note);
      return true;
    } catch {
      if(target.dataset.tooltipRequest!==String(id))return false;
      const note=document.createElement('p');note.className='tooltip-provenance';note.textContent=T('Vollständiger Forever-Originaltext gerade nicht verfügbar. Vorhandene Daten stehen auf der Item-Seite; fehlende Stats werden nicht ergänzt.');target.append(note);
      return false;
    } finally {if(target.dataset.tooltipRequest===String(id))target.removeAttribute('aria-busy');}
  }
  // Item pages also show the original directly, rather than hiding it near the footer.
  const observer=new MutationObserver(()=>{
    const host=document.querySelector('[data-original-tooltip]:not([data-loading])');if(!host)return;
    host.dataset.loading='1';fill(host,Number(host.dataset.originalTooltip));
  });
  observer.observe(document.getElementById('main'),{childList:true,subtree:true});
  window.AHItemTooltip = Object.freeze({sanitize});
})();
