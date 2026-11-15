/* Tap-to-inspect, Forever only. External markup is allowlist-sanitized before display. */
(() => {
  'use strict';
  const cache = new Map();
  const dialog = document.createElement('dialog');
  dialog.className = 'item-inspector';
  dialog.setAttribute('aria-label', 'Original-Itemtext');
  dialog.innerHTML = '<div class="inspector-top"><span>ITEM ANSEHEN</span><button type="button" class="inspector-close" aria-label="Schließen">×</button></div><div class="inspector-content"></div><div class="inspector-bottom"><a class="btn primary inspector-prices">Preise &amp; Verlauf ›</a><a class="inspector-source" target="_blank" rel="noopener">Wowhead · Forever ↗</a></div>';
  document.body.append(dialog);
  const content = dialog.querySelector('.inspector-content');
  const close = () => dialog.close();
  dialog.querySelector('button').addEventListener('click', close);
  dialog.addEventListener('click', e => { if (e.target === dialog) { const r = dialog.getBoundingClientRect(); if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) close(); } });
  dialog.querySelector('.inspector-prices').addEventListener('click', close);

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
    if (!cache.has(id)) cache.set(id, fetch(`https://nether.wowhead.com/tooltip/item/${id}?dataEnv=16&locale=3`, {signal:AbortSignal.timeout(9000)})
      .then(r => {if (!r.ok) throw new Error('Nicht verfügbar'); return r.json();})
      .then(d => {if (typeof d.tooltip !== 'string' || !d.tooltip.trim() || d.error) throw new Error('Kein Originaltext'); return d;})
      .catch(e => {cache.delete(id);throw e;}));
    return cache.get(id);
  }
  async function fill(target, id) {
    target.setAttribute('aria-busy','true');target.dataset.tooltipRequest=String(id);
    try {
      const d = await load(id);if(target.dataset.tooltipRequest!==String(id))return false;
      const box = document.createElement('div');box.className='wtip original-tooltip';box.append(sanitize(d.tooltip));
      target.replaceChildren(box);
      const note = document.createElement('p');note.className='tooltip-provenance';note.textContent='Original-Itemtext · Wowhead Forever · Deutsch';target.append(note);
      return true;
    } catch {
      if(target.dataset.tooltipRequest!==String(id))return false;
      target.querySelector('.inspector-wait')?.remove();
      const note=document.createElement('p');note.className='tooltip-provenance';note.textContent='Vollständiger Forever-Originaltext gerade nicht verfügbar. Vorhandene Daten stehen auf der Item-Seite; fehlende Stats werden nicht ergänzt.';target.append(note);
      return false;
    } finally {if(target.dataset.tooltipRequest===String(id))target.removeAttribute('aria-busy');}
  }
  document.addEventListener('click', e => {
    const link=e.target.closest('a.item[href*="/item/"]');
    if (!link || e.button || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return;
    const match=link.getAttribute('href').match(/\/item\/(\d+)/);if(!match)return;
    e.preventDefault();e.stopPropagation();
    const id=Number(match[1]);
    content.replaceChildren();
    const wait=document.createElement('p');wait.className='inspector-wait';wait.textContent='Lade originalen Itemtext …';content.append(wait);
    dialog.querySelector('.inspector-prices').href=link.getAttribute('href');
    dialog.querySelector('.inspector-source').href=`https://www.wowhead.com/forever/de/item=${id}`;
    dialog.showModal();fill(content,id);
  },true);
  document.addEventListener('click', e=>{const button=e.target.closest('[data-inspect-item]');if(!button)return;const id=Number(button.dataset.inspectItem);content.innerHTML='<p class="inspector-wait">Lade originalen Itemtext …</p>';dialog.querySelector('.inspector-prices').href=location.hash;dialog.querySelector('.inspector-source').href=`https://www.wowhead.com/forever/de/item=${id}`;dialog.showModal();fill(content,id);});
  // Item pages also show the original directly, rather than hiding it near the footer.
  const observer=new MutationObserver(()=>{
    const host=document.querySelector('[data-original-tooltip]:not([data-loading])');if(!host)return;
    host.dataset.loading='1';fill(host,Number(host.dataset.originalTooltip));
  });
  observer.observe(document.getElementById('main'),{childList:true,subtree:true});
  window.AHItemTooltip = Object.freeze({sanitize});
})();
