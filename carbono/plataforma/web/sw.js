/* Service worker do CarbonMG: abre o painel sem conexão e guarda as consultas usadas em campo.
 *
 * - Telas do app (HTML, CSS, JS, ícones): cache primeiro, atualizado em segundo plano.
 * - Consultas de leitura usadas em campo (inventários, contratos, unidades, fatores, sessão): rede
 *   primeiro; sem conexão, a última resposta guardada.
 * - Envios (POST/PATCH/DELETE) nunca são guardados aqui: sem conexão, o app usa a fila do aparelho.
 * - Anexos, dados pessoais exportados e trilha de auditoria não são guardados no aparelho.
 * - Ao sair da conta, o app apaga o cache de consultas (LIMPAR_DADOS).
 */
"use strict";
const VERSAO = "carbonmg-v10";
const CACHE_APP = `${VERSAO}-app`;
const CACHE_API = "carbonmg-api";
const APP = ["./", "index.html", "app.css", "app.js", "offline.js", "mapa.js", "manifest.webmanifest",
  "icone-192.png", "icone-512.png", "icone-maskable-512.png", "vendor/leaflet/leaflet.js", "vendor/leaflet/leaflet.css"];
const LEITURA_OFFLINE = [
  /^\/auth\/config$/, /^\/eu$/, /^\/fatores$/, /^\/sbce\/calendario$/,
  /^\/inventarios(\/\d+)?$/, /^\/contratos(\/\d+)?$/, /^\/organizacoes\/\d+\/instalacoes$/,
  /^\/organizacoes\/\d+\/painel$/, /^\/projetos(\/\d+|\/regras)?$/,
];

self.addEventListener("install", (ev) => {
  ev.waitUntil(caches.open(CACHE_APP).then((c) => c.addAll(APP)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (ev) => {
  ev.waitUntil(caches.keys()
    .then((ks) => Promise.all(ks.filter((k) => k.startsWith("carbonmg-v") && k !== CACHE_APP).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("message", (ev) => {
  if (ev.data === "LIMPAR_DADOS") ev.waitUntil(caches.delete(CACHE_API));
});

function semConexao() {
  return new Response(JSON.stringify({ erro: "Sem conexão. Esta informação ainda não foi aberta neste aparelho." }),
    { status: 503, headers: { "Content-Type": "application/json", "X-CarbonMG-Offline": "1" } });
}

self.addEventListener("fetch", (ev) => {
  const req = ev.request;
  const url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== self.location.origin) return;

  if (url.pathname.startsWith("/painel/")) {
    // App: cache primeiro; busca nova versão em segundo plano.
    ev.respondWith(caches.open(CACHE_APP).then(async (c) => {
      const guardado = await c.match(req, { ignoreSearch: true });
      const rede = fetch(req).then((r) => { if (r.ok) c.put(req, r.clone()); return r; }).catch(() => null);
      return guardado || (await rede) || (await c.match("index.html")) || semConexao();
    }));
    return;
  }

  if (LEITURA_OFFLINE.some((re) => re.test(url.pathname))) {
    // Consultas: rede primeiro; sem conexão, a última resposta guardada.
    ev.respondWith(fetch(req).then((r) => {
      if (r.ok) { const copia = r.clone(); caches.open(CACHE_API).then((c) => c.put(req, copia)); }
      return r;
    }).catch(async () => (await caches.match(req, { cacheName: CACHE_API })) || semConexao()));
    return;
  }

  // Demais GET: só rede; sem conexão, resposta clara em vez de erro de rede.
  if (url.pathname.startsWith("/publico/") || url.pathname.startsWith("/anexos/")) return;
  ev.respondWith(fetch(req).catch(() => semConexao()));
});
