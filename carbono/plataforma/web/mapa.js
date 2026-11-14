/* Mapa da área do projeto (Leaflet, servido pela própria plataforma).
 * - Mostra o polígono atual, o ponto de localização e, durante a edição, o polígono em análise.
 * - Desenho: cada clique acrescenta um vértice; "Concluir" fecha o contorno.
 * - Quem não usa mouse importa o arquivo (shapefile do CAR, KML, KMZ ou GeoJSON): mesmo resultado.
 */
"use strict";

window.CarbonMapa = (() => {
  let carregando = null;

  /** Carrega o Leaflet uma vez, só quando algum mapa é aberto. */
  function carregarLeaflet() {
    if (window.L) return Promise.resolve();
    if (carregando) return carregando;
    carregando = new Promise((ok, erro) => {
      const css = document.createElement("link");
      css.rel = "stylesheet"; css.href = "vendor/leaflet/leaflet.css";
      document.head.appendChild(css);
      const js = document.createElement("script");
      js.src = "vendor/leaflet/leaflet.js";
      js.onload = ok; js.onerror = () => { carregando = null; erro(new Error("Não foi possível carregar o mapa.")); };
      document.head.appendChild(js);
    });
    return carregando;
  }

  const cor = (nome) => getComputedStyle(document.documentElement).getPropertyValue(nome).trim();

  async function abrir(el, { fundo, atribuicao, ponto, area }) {
    await carregarLeaflet();
    const L = window.L;
    // A vista inicial precisa existir antes de qualquer camada vetorial ser acrescentada.
    const mapa = L.map(el, { scrollWheelZoom: false, attributionControl: true, center: [ponto[1], ponto[0]], zoom: 14 });
    mapa.attributionControl.setPrefix('<a href="https://leafletjs.com" target="_blank" rel="noopener">Leaflet</a>');
    if (fundo) L.tileLayer(fundo, { maxZoom: 19, attribution: atribuicao || "" }).addTo(mapa);
    const camadaAtual = L.geoJSON(null, { style: { color: cor("--mata"), weight: 2, fillOpacity: 0.2 } }).addTo(mapa);
    const camadaPrevia = L.geoJSON(null, { style: { color: cor("--ambar"), weight: 2, dashArray: "6 4", fillOpacity: 0.15 } }).addTo(mapa);
    const marcador = L.circleMarker([ponto[1], ponto[0]], { radius: 6, color: cor("--tinta"), weight: 2, fillOpacity: 1, fillColor: cor("--folha") })
      .addTo(mapa).bindTooltip("Ponto de localização do projeto");
    const desenho = { pontos: [], linha: L.polyline([], { color: cor("--foco"), weight: 2 }).addTo(mapa), vertices: L.layerGroup().addTo(mapa) };
    let desenhando = false, aoMudar = () => {};

    function enquadrar() {
      const grupos = [camadaPrevia, camadaAtual].filter((c) => c.getLayers().length);
      if (grupos.length) mapa.fitBounds(grupos[0].getBounds(), { padding: [24, 24], maxZoom: 17 });
      else mapa.setView(marcador.getLatLng(), 14);
    }
    function mostrarAtual(geojson) { camadaAtual.clearLayers(); if (geojson) camadaAtual.addData(geojson); }
    function mostrarPrevia(geojson) { camadaPrevia.clearLayers(); if (geojson) camadaPrevia.addData(geojson); enquadrar(); }
    function redesenhar() {
      desenho.linha.setLatLngs(desenho.pontos.length > 2 ? [...desenho.pontos, desenho.pontos[0]] : desenho.pontos);
      desenho.vertices.clearLayers();
      desenho.pontos.forEach((p) => L.circleMarker(p, { radius: 4, color: cor("--foco"), fillOpacity: 1 }).addTo(desenho.vertices));
      aoMudar(desenho.pontos.length);
    }
    mapa.on("click", (ev) => { if (desenhando) { desenho.pontos.push(ev.latlng); redesenhar(); } });

    mostrarAtual(area);
    enquadrar();
    setTimeout(() => mapa.invalidateSize(), 0);

    return {
      mostrarAtual, mostrarPrevia, enquadrar,
      iniciarDesenho(cb) { desenhando = true; aoMudar = cb || (() => {}); desenho.pontos = []; redesenhar(); el.classList.add("desenhando"); },
      desfazer() { desenho.pontos.pop(); redesenhar(); },
      encerrarDesenho() { desenhando = false; desenho.pontos = []; redesenhar(); el.classList.remove("desenhando"); },
      /** GeoJSON do contorno desenhado (fechado), ou null se tiver menos de 3 vértices. */
      desenhoGeojson() {
        if (desenho.pontos.length < 3) return null;
        const anel = desenho.pontos.map((p) => [Number(p.lng.toFixed(7)), Number(p.lat.toFixed(7))]);
        return { type: "Polygon", coordinates: [[...anel, anel[0]]] };
      },
      destruir() { mapa.remove(); },
    };
  }

  return { abrir };
})();
