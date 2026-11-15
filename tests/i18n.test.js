/* Prüft, dass jede im Frontend markierte Zeichenkette eine EN-Übersetzung in i18n.js hat. */
const fs = require("fs");
const path = require("path");

globalThis.window = globalThis;
const root = path.join(__dirname, "..", "static");
eval(fs.readFileSync(path.join(root, "i18n.js"), "utf8"));
if (!globalThis.AHI18N) { console.error("AHI18N fehlt"); process.exit(1); }
AHI18N.set("en");

const keys = new Set();
const scan = (file, patterns) => {
  const src = fs.readFileSync(file, "utf8");
  for (const [re, unesc] of patterns) {
    for (const m of src.matchAll(re)) {
      let k = m[1];
      if (unesc) k = k.replace(/\\(.)/g, "$1");
      k = k.replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"');
      keys.add(k);
    }
  }
};
scan(path.join(root, "app.js"), [[/(?<![A-Za-z$_])t\("((?:[^"\\]|\\.)*)"\s*\)/g, true], [/(?<![A-Za-z$_])tf\("((?:[^"\\]|\\.)*)"/g, true]]);
scan(path.join(root, "static-api.js"), [[/(?<![A-Za-z$_])T\("((?:[^"\\]|\\.)*)"\s*\)/g, true]]);
scan(path.join(root, "item-tooltip.js"), [[/(?<![A-Za-z$_])T\('((?:[^'\\]|\\.)*)'\s*\)/g, true]]);
scan(path.join(root, "index.html"), [[/data-i18n(?:-ph|-aria)?="([^"]+)"/g, false]]);

let missing = [];
for (const k of keys) {
  if (!AHI18N._has(k)) missing.push(k);
}
if (missing.length) {
  console.error("Fehlende EN-Übersetzungen (" + missing.length + "):");
  missing.forEach((k) => console.error("  - " + JSON.stringify(k)));
  process.exit(1);
}
console.log(`i18n: ${keys.size} Schlüssel, alle übersetzt`);
