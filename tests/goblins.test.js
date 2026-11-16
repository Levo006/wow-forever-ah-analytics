/* Static artwork must never masquerade as a floating control. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const root = __dirname + '/../static/';
const app = fs.readFileSync(root + 'app.js','utf8');
const css = fs.readFileSync(root + 'design-exchange.css','utf8');
assert(!app.includes('goblinPeek'));
assert(!app.includes('goblin-off'));
assert(!css.includes('goblin-peek'));
assert(app.includes('<figure class="goblin-scene'));
assert(css.includes('pointer-events:none'));
globalThis.window = globalThis;
eval(fs.readFileSync(root + 'i18n.js','utf8'));
AHI18N.set('en');
for (const quote of [...app.matchAll(/goblinScene\("[a-z]+", "([^"]+)"/g)].map(m=>m[1])) {
 assert(AHI18N._has(quote), 'Missing translation: ' + quote);
}
for(const name of ['merchant','coin','courier','discount','sword','potions','treasure','alchemist']) {
 assert(fs.statSync(root + 'assets/goblins/' + name + '.webp').size > 1000);
 assert(app.includes('"'+name+'"'));
}
console.log('Static goblin placements and translations OK');
