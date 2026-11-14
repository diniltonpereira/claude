"""Polígono da área do projeto: leitura (GeoJSON, KML/KMZ, shapefile do CAR), validação, área e sobreposição.

Coordenadas em graus (longitude, latitude), datum WGS 84 ou SIRGAS 2000, que coincidem na escala de
um imóvel (diferença de centímetros). Arquivos em projeção UTM são recusados com orientação para
exportar em coordenadas geográficas (o SICAR já entrega assim).

A área é calculada sobre a esfera autálica (de mesma área que o elipsoide WGS 84), com erro típico
abaixo de 0,5%. Serve para conferir a área declarada e medir sobreposições; a área oficial continua
sendo a do CAR, da matrícula ou do levantamento georreferenciado.
"""
from __future__ import annotations

import io
import json
import math
import re
import zipfile
from xml.etree import ElementTree

from shapely import make_valid
from shapely.geometry import MultiPolygon, Point, Polygon, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union
from shapely.validation import explain_validity

from .banco import ErroNegocio

RAIO_AUTALICO = 6_371_007.2          # m (esfera de mesma área do WGS 84)
MAX_VERTICES = 50_000
MAX_ARQUIVO = 10 * 1024 * 1024
MAX_AREA_HA = 5_000_000
BRASIL = (-74.0, -34.0, -28.0, 6.0)    # lon mín, lat mín, lon máx, lat máx
CASAS = 7                              # ~1 cm


class GeometriaInvalida(ErroNegocio):
    pass


# ---------------------------------------------------------------- área
def _area_anel_m2(coords) -> float:
    # Chamberlain & Duquette (2007), "Some algorithms for polygons on a sphere"
    total = 0.0
    pts = list(coords)
    for (l1, p1), (l2, p2) in zip(pts, pts[1:] + pts[:1]):
        total += math.radians(l2 - l1) * (2 + math.sin(math.radians(p1)) + math.sin(math.radians(p2)))
    return abs(total) * RAIO_AUTALICO ** 2 / 2


def area_ha(g: BaseGeometry) -> float:
    polis = g.geoms if hasattr(g, "geoms") else [g]
    m2 = 0.0
    for p in polis:
        if isinstance(p, Polygon):
            m2 += _area_anel_m2([c[:2] for c in p.exterior.coords[:-1]])
            m2 -= sum(_area_anel_m2([c[:2] for c in i.coords[:-1]]) for i in p.interiors)
    return m2 / 10_000


# ---------------------------------------------------------------- leitura
def _de_geojson(dados: dict) -> list[dict]:
    t = dados.get("type")
    if t == "FeatureCollection":
        return [g for f in dados.get("features") or [] for g in _de_geojson(f)]
    if t == "Feature":
        return _de_geojson(dados.get("geometry") or {})
    if t == "GeometryCollection":
        return [g for x in dados.get("geometries") or [] for g in _de_geojson(x)]
    if t in ("Polygon", "MultiPolygon"):
        return [dados]
    if t in ("Point", "MultiPoint", "LineString", "MultiLineString"):
        return []
    raise GeometriaInvalida("GeoJSON sem polígono reconhecível.")


_COORD = re.compile(r"\s+")


def _kml_anel(el) -> list[tuple[float, float]]:
    txt = next((x.text for x in el.iter() if x.tag.endswith("coordinates")), "") or ""
    pts = []
    for tripla in _COORD.split(txt.strip()):
        if tripla:
            partes = tripla.split(",")
            pts.append((float(partes[0]), float(partes[1])))
    return pts


def _de_kml(texto: bytes) -> list[dict]:
    if re.search(rb"<!(DOCTYPE|ENTITY)", texto[:4096], re.I):
        raise GeometriaInvalida("KML com declarações DOCTYPE/ENTITY não é aceito.")
    try:
        raiz = ElementTree.fromstring(texto)
    except ElementTree.ParseError as exc:
        raise GeometriaInvalida("KML ilegível.") from exc
    geoms = []
    for pol in (e for e in raiz.iter() if e.tag.endswith("Polygon")):
        fora = next((e for e in pol if e.tag.endswith("outerBoundaryIs")), None)
        if fora is None:
            continue
        aneis = [_kml_anel(fora)] + [_kml_anel(e) for e in pol if e.tag.endswith("innerBoundaryIs")]
        geoms.append({"type": "Polygon", "coordinates": aneis})
    return geoms


def _zip(conteudo: bytes) -> zipfile.ZipFile:
    try:
        z = zipfile.ZipFile(io.BytesIO(conteudo))
    except zipfile.BadZipFile as exc:
        raise GeometriaInvalida("Arquivo compactado inválido.") from exc
    infos = z.infolist()
    if len(infos) > 200 or sum(i.file_size for i in infos) > 200 * 1024 * 1024 or any(
            i.compress_size and i.file_size / i.compress_size > 500 for i in infos):
        raise GeometriaInvalida("Arquivo compactado com estrutura anormal.")
    return z


def _de_shapefile(z: zipfile.ZipFile) -> tuple[list[dict], list[str]]:
    import shapefile
    nomes = {n.lower(): n for n in z.namelist() if not n.startswith("__MACOSX")}
    shps = [n for n in nomes if n.endswith(".shp")]
    if len(shps) != 1:
        raise GeometriaInvalida("Envie um shapefile por vez (o .zip deve ter um único .shp, com .shx, .dbf e .prj).")
    base = shps[0][:-4]
    if base + ".shx" not in nomes:
        raise GeometriaInvalida("Falta o arquivo .shx do shapefile.")
    avisos = []
    prj = z.read(nomes[base + ".prj"]).decode("latin-1") if base + ".prj" in nomes else ""
    if "PROJCS" in prj.upper():
        raise GeometriaInvalida("O shapefile está em coordenadas projetadas (por exemplo, UTM). Exporte em "
                                "coordenadas geográficas SIRGAS 2000 (EPSG:4674) ou WGS 84, como o SICAR fornece.")
    if not prj:
        avisos.append("Shapefile sem .prj: as coordenadas foram lidas como geográficas (SIRGAS 2000/WGS 84).")
    leitor = shapefile.Reader(shp=io.BytesIO(z.read(nomes[shps[0]])), shx=io.BytesIO(z.read(nomes[base + ".shx"])),
                              dbf=io.BytesIO(z.read(nomes[base + ".dbf"])) if base + ".dbf" in nomes else None)
    if leitor.shapeType not in (shapefile.POLYGON, shapefile.POLYGONZ, shapefile.POLYGONM):
        raise GeometriaInvalida("O shapefile não é de polígonos (é de pontos ou linhas).")
    return [s.__geo_interface__ for s in leitor.shapes() if s.points], avisos


def ler(nome: str, conteudo: bytes) -> tuple[dict, list[str]]:
    """Lê o arquivo e devolve (geometria GeoJSON, avisos de leitura)."""
    if len(conteudo) > MAX_ARQUIVO:
        raise GeometriaInvalida("Arquivo acima de 10 MB.")
    ext = nome.lower().rsplit(".", 1)[-1] if "." in nome else ""
    avisos: list[str] = []
    if ext in ("geojson", "json"):
        try:
            geoms = _de_geojson(json.loads(conteudo))
        except (ValueError, AttributeError) as exc:
            raise GeometriaInvalida("GeoJSON ilegível.") from exc
    elif ext == "kml":
        geoms = _de_kml(conteudo)
    elif ext == "kmz":
        z = _zip(conteudo)
        kml = next((n for n in z.namelist() if n.lower().endswith(".kml")), None)
        if not kml:
            raise GeometriaInvalida("KMZ sem arquivo .kml dentro.")
        geoms = _de_kml(z.read(kml))
    elif ext == "zip":
        geoms, avisos = _de_shapefile(_zip(conteudo))
    else:
        raise GeometriaInvalida("Formato não aceito. Envie GeoJSON, KML, KMZ ou shapefile compactado (.zip).")
    if not geoms:
        raise GeometriaInvalida("Nenhum polígono encontrado no arquivo.")
    return {"type": "GeometryCollection", "geometries": geoms}, avisos


# ---------------------------------------------------------------- validação
def _arredondar(g: BaseGeometry) -> dict:
    def anel(cs):
        return [[round(x, CASAS), round(y, CASAS)] for x, y, *_ in cs]
    polis = list(g.geoms) if isinstance(g, MultiPolygon) else [g]
    return {"type": "MultiPolygon", "coordinates": [
        [anel(p.exterior.coords)] + [anel(i.coords) for i in p.interiors] for p in polis]}


def _poligonos(g: BaseGeometry) -> list[Polygon]:
    """Só as partes de área (make_valid pode devolver linhas e pontos soltos junto)."""
    if isinstance(g, Polygon):
        return [g]
    return [p for x in getattr(g, "geoms", []) for p in _poligonos(x)]


def normalizar(geojson: dict) -> tuple[dict, list[str]]:
    """Valida e devolve (MultiPolygon GeoJSON, avisos). Recusa o que não for área no território brasileiro."""
    avisos: list[str] = []
    geoms = _de_geojson(geojson)
    try:
        partes = [shape(g) for g in geoms]
    except Exception as exc:
        raise GeometriaInvalida("Coordenadas do polígono inválidas.") from exc
    if not partes:
        raise GeometriaInvalida("Nenhum polígono informado.")
    vertices = sum(len(p.exterior.coords) + sum(len(i.coords) for i in p.interiors)
                   for g in partes for p in _poligonos(g))
    if vertices > MAX_VERTICES:
        raise GeometriaInvalida(f"Polígono com {vertices} vértices; o limite é {MAX_VERTICES}. Simplifique o desenho.")
    for g in partes:
        x0, y0, x1, y1 = g.bounds
        if max(abs(x0), abs(x1)) > 180 or max(abs(y0), abs(y1)) > 90:
            raise GeometriaInvalida("As coordenadas não são latitude e longitude em graus (parecem UTM). "
                                    "Exporte em coordenadas geográficas SIRGAS 2000 (EPSG:4674) ou WGS 84.")
        if x0 < BRASIL[0] or y0 < BRASIL[1] or x1 > BRASIL[2] or y1 > BRASIL[3]:
            raise GeometriaInvalida("O polígono está fora do território brasileiro. Confira se latitude e "
                                    "longitude não foram trocadas.")
    polis = []
    for g in partes:
        if not g.is_valid:
            motivo = explain_validity(g)
            g = make_valid(g)
            avisos.append("O contorno se cruzava ou tinha pontos repetidos e foi corrigido automaticamente "
                          f"({motivo}). Confira o desenho no mapa.")
        polis.extend(_poligonos(g))
    polis = [p for p in polis if not p.is_empty and p.area > 0]
    if not polis:
        raise GeometriaInvalida("O polígono não tem área (pontos alinhados ou repetidos).")
    unido = MultiPolygon(polis) if len(polis) > 1 else polis[0]
    if not unido.is_valid:  # partes que se sobrepõem entre si: une
        unido = unary_union(polis)
        avisos.append("Partes do polígono se sobrepunham e foram unidas.")
    ha = area_ha(unido)
    if ha < 0.0001:
        raise GeometriaInvalida("Área menor que 1 m²: confira o desenho.")
    if ha > MAX_AREA_HA:
        raise GeometriaInvalida("Área acima de 5 milhões de hectares: confira o arquivo.")
    return _arredondar(unido), avisos


def resumo(geojson: dict) -> dict:
    g = shape(geojson)
    c = g.representative_point()
    x0, y0, x1, y1 = g.bounds
    return {"area_ha": round(area_ha(g), 4), "partes": len(_poligonos(g)),
            "ponto_interno": [round(c.x, 6), round(c.y, 6)], "limites": [x0, y0, x1, y1]}


def intersecao_ha(a: dict, b: dict) -> float:
    ga, gb = shape(a), shape(b)
    if not ga.intersects(gb):
        return 0.0
    return area_ha(ga.intersection(gb))


def contem_ponto(geojson: dict, lon: float, lat: float, tolerancia_m: float = 30) -> bool:
    g = shape(geojson)
    return g.buffer(tolerancia_m / 111_320).contains(Point(lon, lat))


def feature_collection(geojson: dict, propriedades: dict) -> dict:
    return {"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": geojson,
                                                       "properties": propriedades}]}
