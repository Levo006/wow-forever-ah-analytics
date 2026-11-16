/* Static artwork must never masquerade as a floating control. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const root = __dirname + '/../static/';
const app = fs.readFileSync(root + 'app.js','utf8');
const css = fs.readFileSync(root + 'design-exchange.css','utf8');
assert(!app.includes('goblinPeek'));
assert(!app.includes('goblin-off'));
assert(!css.includes('goblin-peek'));
assert(app.includes('<figure class="goblin-ambient'));
assert(css.includes('pointer-events:none'));
// Genau eine Figur pro Aufruf; jede Ansicht ruft goblinScene höchstens einmal pro Platz.
const moodBlock = app.match(/const GOBLIN_MOODS = \[([\s\S]*?)\];/);
assert(moodBlock, 'GOBLIN_MOODS fehlt');
const entries = [...moodBlock[1].matchAll(/\["([a-z]+)", \[([^\]]+)\]\]/g)];
assert.equal(entries.length, 8, 'alle acht Goblin-Bilder im Zufallspool');
globalThis.window = globalThis;
eval(fs.readFileSync(root + 'i18n.js','utf8'));
AHI18N.set('en');
const seen = new Set();
for (const [, img, quotes] of entries) {
 assert(fs.statSync(root + 'assets/goblins/' + img + '.webp').size > 1000, img);
 seen.add(img);
 for (const q of quotes.matchAll(/"([^"]+)"/g)) assert(AHI18N._has(q[1]), 'Missing translation: ' + q[1]);
}
for (const name of ['merchant','coin','courier','discount','sword','potions','treasure','alchemist']) assert(seen.has(name), name);
// Kein Kontext darf zwei Figuren gleichzeitig rendern: Aufrufstellen je Ansicht prüfen.
const calls = [...app.matchAll(/goblinScene\("(?:hero|head|panel)-goblin"\)/g)].length;
assert(calls >= 5, 'Goblin-Platzierungen in den Ansichten fehlen');
console.log('Static goblin placements and translations OK');
