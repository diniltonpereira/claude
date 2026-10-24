// Copia ../../painel para ./painel (o app empacota a mesma interface do site).
const fs = require("node:fs");
const path = require("node:path");

const src = path.resolve(__dirname, "../../../painel");
const dst = path.resolve(__dirname, "../painel");
fs.rmSync(dst, { recursive: true, force: true });
fs.cpSync(src, dst, { recursive: true, filter: (p) => !p.endsWith("sw.js") });
console.log(`painel copiado: ${src} -> ${dst}`);
