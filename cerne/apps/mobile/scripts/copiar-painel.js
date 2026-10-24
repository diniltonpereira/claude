// Copia ../../painel para ./www (o app empacota a mesma interface do site, sem o service worker).
const fs = require("node:fs");
const path = require("node:path");

const src = path.resolve(__dirname, "../../../painel");
const dst = path.resolve(__dirname, "../www");
fs.rmSync(dst, { recursive: true, force: true });
fs.cpSync(src, dst, { recursive: true, filter: (p) => !p.endsWith("sw.js") });
console.log(`painel copiado: ${src} -> ${dst}`);
