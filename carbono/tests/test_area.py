"""Polígono da área do projeto: leitura de arquivos, validação, área, versões e sobreposição."""
import io
import json
import zipfile

import pytest
import shapefile
from fastapi.testclient import TestClient

from plataforma import geo, projetos as pj
from plataforma.api import criar_app
from plataforma.banco import ErroNegocio, conectar

from .test_projetos import MOC, _anexos, criar, quadrado, s  # noqa: F401 — fixtures reaproveitadas

LON, LAT = MOC["longitude"], MOC["latitude"]
SIRGAS = ('GEOGCS["GCS_SIRGAS_2000",DATUM["D_SIRGAS_2000",SPHEROID["GRS_1980",6378137.0,298.257222101]],'
          'PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]]')
UTM = 'PROJCS["SIRGAS_2000_UTM_Zone_23S",' + SIRGAS + ',PROJECTION["Transverse_Mercator"]]'


def shp_zip(poligonos, prj=SIRGAS, tipo=shapefile.POLYGON) -> bytes:
    shp, shx, dbf = io.BytesIO(), io.BytesIO(), io.BytesIO()
    w = shapefile.Writer(shp=shp, shx=shx, dbf=dbf, shapeType=tipo)
    w.field("COD_IMOVEL", "C")
    for anel in poligonos:
        w.poly([anel]) if tipo == shapefile.POLYGON else w.point(*anel[0])
        w.record("MG-3143302-ABC")
    w.close()
    z = io.BytesIO()
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("AREA_IMOVEL.shp", shp.getvalue())
        f.writestr("AREA_IMOVEL.shx", shx.getvalue())
        f.writestr("AREA_IMOVEL.dbf", dbf.getvalue())
        if prj:
            f.writestr("AREA_IMOVEL.prj", prj)
    return z.getvalue()


def anel(lon, lat, lado=0.01, horario=False):
    a = quadrado(lon, lat, lado)["coordinates"][0]
    return a[::-1] if horario else a


# ---------------------------------------------------------------- área e validação
def test_area_de_um_quadrado_e_desconta_o_furo():
    # 0,01 grau a 16,7°S: 1.113 m (latitude) × 1.066 m (longitude) ≈ 118,6 ha
    g, avisos = geo.normalizar(quadrado(LON, LAT))
    assert avisos == [] and geo.resumo(g)["area_ha"] == pytest.approx(118.6, rel=0.005)
    com_furo = {"type": "Polygon", "coordinates": [anel(LON, LAT), anel(LON, LAT, 0.005, horario=True)]}
    g2, _ = geo.normalizar(com_furo)
    assert geo.resumo(g2)["area_ha"] == pytest.approx(118.6 * 0.75, rel=0.005)


@pytest.mark.parametrize("entrada, mensagem", [
    ({"type": "Polygon", "coordinates": [[[600000, 8150000], [601000, 8150000], [601000, 8151000], [600000, 8150000]]]},
     "UTM"),
    (quadrado(LAT, LON), "trocadas"),                       # latitude e longitude invertidas
    (quadrado(2.35, 48.85), "fora do território"),          # Paris
    ({"type": "Point", "coordinates": [LON, LAT]}, "Nenhum polígono"),
    ({"type": "Polygon", "coordinates": [[[LON, LAT], [LON + 0.01, LAT], [LON + 0.02, LAT], [LON, LAT]]]}, "não tem área"),
])
def test_recusa_coordenadas_sem_sentido(entrada, mensagem):
    with pytest.raises(geo.GeometriaInvalida, match=mensagem):
        geo.normalizar(entrada)


def test_contorno_que_se_cruza_e_corrigido_com_aviso():
    d = 0.005
    gravata = {"type": "Polygon", "coordinates": [[[LON - d, LAT - d], [LON + d, LAT + d], [LON + d, LAT - d],
                                                   [LON - d, LAT + d], [LON - d, LAT - d]]]}
    g, avisos = geo.normalizar(gravata)
    assert g["type"] == "MultiPolygon" and len(g["coordinates"]) == 2 and "corrigido" in avisos[0]


# ---------------------------------------------------------------- leitura de arquivos
def test_le_shapefile_do_car_e_recusa_utm_e_pontos():
    g, avisos = geo.ler("CAR_MG.zip", shp_zip([anel(LON, LAT)]))
    normal, _ = geo.normalizar(g)
    assert avisos == [] and geo.resumo(normal)["area_ha"] == pytest.approx(118.6, rel=0.005)
    with pytest.raises(geo.GeometriaInvalida, match="SIRGAS 2000"):
        geo.ler("car.zip", shp_zip([anel(LON, LAT)], prj=UTM))
    with pytest.raises(geo.GeometriaInvalida, match="não é de polígonos"):
        geo.ler("car.zip", shp_zip([[(LON, LAT)]], tipo=shapefile.POINT))
    _, avisos = geo.ler("car.zip", shp_zip([anel(LON, LAT)], prj=None))
    assert "sem .prj" in avisos[0]


def test_le_kml_kmz_e_geojson_e_recusa_kml_com_entidades():
    coords = " ".join(f"{x},{y},0" for x, y in anel(LON, LAT))
    furo = " ".join(f"{x},{y},0" for x, y in anel(LON, LAT, 0.005, horario=True))
    kml = f"""<?xml version="1.0"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document><Placemark><Polygon>
      <outerBoundaryIs><LinearRing><coordinates>{coords}</coordinates></LinearRing></outerBoundaryIs>
      <innerBoundaryIs><LinearRing><coordinates>{furo}</coordinates></LinearRing></innerBoundaryIs>
      </Polygon></Placemark></Document></kml>""".encode()
    g, _ = geo.ler("area.kml", kml)
    assert geo.resumo(geo.normalizar(g)[0])["area_ha"] == pytest.approx(118.6 * 0.75, rel=0.005)
    z = io.BytesIO()
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("doc.kml", kml)
    assert geo.ler("area.kmz", z.getvalue())[0]["geometries"]
    fc = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {}, "geometry": quadrado(LON, LAT)}]}
    assert geo.ler("area.geojson", json.dumps(fc).encode())[0]["geometries"]
    bomba = b'<?xml version="1.0"?><!DOCTYPE kml [<!ENTITY a "aaaa">]><kml>&a;</kml>'
    with pytest.raises(geo.GeometriaInvalida, match="DOCTYPE"):
        geo.ler("x.kml", bomba)
    with pytest.raises(geo.GeometriaInvalida, match="Formato"):
        geo.ler("x.dwg", b"...")


# ---------------------------------------------------------------- versões, travas e sobreposição
def test_versoes_imutaveis_e_trava_depois_da_validacao(s):
    c = s["c"]
    pid = criar(s, "gestor", tipo="restauracao_psa", area_ha=118)
    r1 = pj.definir_area(c, s["gestor"], pid, quadrado(LON, LAT), origem="desenho no mapa")
    assert r1["versao"] == 1 and pj.definir_area(c, s["gestor"], pid, quadrado(LON, LAT), origem="x")["inalterado"]
    r2 = pj.definir_area(c, s["gestor"], pid, quadrado(LON, LAT, 0.012), origem="CAR.zip")
    assert r2["versao"] == 2
    area = pj.ver(c, s["gestor"], pid)["area"]
    assert [h["versao"] for h in area["historico"]] == [2, 1] and area["origem"] == "CAR.zip"
    assert any("declarada (118,00 ha). Confira o arquivo" in a for a in area["alertas"])   # 171 ha × 118 declarados
    with pytest.raises(Exception, match="imutável"):
        c.execute("UPDATE projeto_area SET area_ha=1 WHERE projeto_id=?", (pid,))
    with pytest.raises(Exception, match="imutável"):
        c.execute("DELETE FROM projeto_area WHERE projeto_id=?", (pid,))
    trilha = c.execute("SELECT antes, depois FROM auditoria WHERE acao='definir_area' ORDER BY id").fetchall()
    assert len(trilha) == 2 and '"versao": 1' in trilha[1]["antes"]
    with pytest.raises(Exception):  # outro órgão não altera
        pj.definir_area(c, s["fazendeiro"], pid, quadrado(LON, LAT), origem="x")
    c.execute("UPDATE projeto SET etapa='verificacao' WHERE id=?", (pid,))
    with pytest.raises(ErroNegocio, match="já foi validado"):
        pj.definir_area(c, s["gestor"], pid, quadrado(LON, LAT), origem="x")


def test_ponto_fora_do_poligono_gera_alerta(s):
    pid = criar(s, "gestor", tipo="restauracao_psa")
    pj.definir_area(s["c"], s["gestor"], pid, quadrado(LON + 0.1, LAT), origem="x")
    assert any("fora do polígono" in a for a in pj.ver(s["c"], s["gestor"], pid)["area"]["alertas"])


def test_sobreposicao_do_mesmo_tipo_impede_validacao_e_divisa_nao(s):
    c = s["c"]
    a = criar(s, "gestor", tipo="restauracao_psa", natureza_area="publica_propria")
    b = criar(s, "fazendeiro", tipo="restauracao_psa", natureza_area="privada", nome="Fazenda restaurada",
              car="MG-3143302-ABC", matricula="12.345")
    pj.definir_area(c, s["gestor"], a, quadrado(LON, LAT), origem="x")
    pj.definir_area(c, s["fazendeiro"], b, quadrado(LON + 0.005, LAT), origem="y")      # metade sobreposta
    (sob,) = pj.ver(c, s["gestor"], a)["area"]["sobreposicoes"]
    assert sob["projeto_id"] == b and sob["mesmo_tipo"] and sob["relevante"]
    assert sob["sobreposicao_ha"] == pytest.approx(59.3, rel=0.01)
    assert sob["nome"] == "Projeto de outra organização"                               # não revela o terceiro
    assert pj.ver(c, s["reg"], a)["area"]["sobreposicoes"][0]["nome"] == "Fazenda restaurada"
    c.execute("UPDATE projeto SET etapa='concepcao' WHERE id=?", (a,))
    assert any("dupla contagem" in p for p in pj.pendencias(c, a))
    # vizinho que só encosta na divisa (30 cm de erro de digitalização): aparece, mas não trava
    pj.definir_area(c, s["fazendeiro"], b, quadrado(LON + 0.01 - 0.000003, LAT), origem="y2")
    (sob,) = pj.ver(c, s["gestor"], a)["area"]["sobreposicoes"]
    assert sob["sobreposicao_ha"] < 0.05 and not sob["relevante"]
    assert not any("dupla contagem" in p for p in pj.pendencias(c, a))


def test_sobreposicao_de_outro_tipo_so_informa(s):
    c = s["c"]
    a = criar(s, "gestor", tipo="restauracao_psa")
    b = criar(s, "gestor", tipo="solar", nome="Usina solar")
    pj.definir_area(c, s["gestor"], a, quadrado(LON, LAT), origem="x")
    pj.definir_area(c, s["gestor"], b, quadrado(LON, LAT, 0.002), origem="y")
    (sob,) = pj.ver(c, s["gestor"], a)["area"]["sobreposicoes"]
    assert not sob["mesmo_tipo"] and sob["nome"] == "Usina solar"
    c.execute("UPDATE projeto SET etapa='concepcao' WHERE id=?", (a,))
    assert not any("dupla contagem" in p for p in pj.pendencias(c, a))


# ---------------------------------------------------------------- API
def test_api_importa_com_previa_salva_e_baixa(tmp_path, monkeypatch):
    monkeypatch.setenv("CARBONO_ANEXOS", str(tmp_path / "anexos"))
    app = criar_app(str(tmp_path / "api.db"))
    cli, c = TestClient(app), conectar(app.state.caminho)
    org = c.execute("INSERT INTO organizacao (nome,cnpj,tipo) VALUES ('Pref','11111111000191','orgao_publico')").lastrowid
    g = c.execute("INSERT INTO usuario (nome,papel,organizacao_id) VALUES ('G','elaborador',?)", (org,)).lastrowid
    H = {"X-Usuario-Id": str(g)}
    pid = cli.post("/projetos", headers=H, json={
        "nome": "Recomposição de APP", "tipo": "restauracao_psa", "natureza_area": "publica_propria",
        "linha_base": "Pastagem degradada.", "estimativa_tco2e_ano": 900, **MOC}).json()["id"]
    arq = {"arquivo": ("CAR.zip", shp_zip([anel(LON, LAT)]), "application/zip")}
    previa = cli.post(f"/projetos/{pid}/area/arquivo", headers=H, files=arq).json()
    assert previa["area_ha"] == pytest.approx(118.6, rel=0.005) and previa["geojson"]["type"] == "MultiPolygon"
    assert cli.get(f"/projetos/{pid}", headers=H).json()["area"]["geojson"] is None     # prévia não grava
    r = cli.post(f"/projetos/{pid}/area/arquivo?previa=false", headers=H, files=arq).json()
    assert r["versao"] == 1
    desenho = cli.put(f"/projetos/{pid}/area", headers=H, json={"geojson": quadrado(LON, LAT, 0.011)}).json()
    assert desenho["versao"] == 2
    ruim = cli.post(f"/projetos/{pid}/area/arquivo", headers=H,
                    files={"arquivo": ("car.zip", shp_zip([anel(LON, LAT)], prj=UTM), "application/zip")})
    assert ruim.status_code == 409 and "SIRGAS" in ruim.json()["erro"]
    d = cli.get(f"/projetos/{pid}/area.geojson", headers=H)
    assert d.headers["content-type"].startswith("application/geo+json")
    assert d.json()["features"][0]["properties"]["versao"] == 2
    assert "https://tile.openstreetmap.org" in d.headers["content-security-policy"]
    assert cli.get("/auth/config").json()["mapa"]["tiles"].startswith("https://")
