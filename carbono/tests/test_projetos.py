"""Projetos de geração: titularidade (art. 43), anuência (art. 49), comunidades (art. 47) e funil."""
import pytest
from fastapi.testclient import TestClient

from plataforma import anexos, projetos as pj, registro
from plataforma.api import criar_app
from plataforma.banco import ErroNegocio, NaoAutorizado, conectar

PDF = b"%PDF-1.4\n% teste\n%%EOF\n"
MOC = dict(municipio="Montes Claros", uf="MG", latitude=-16.73, longitude=-43.86)


@pytest.fixture(autouse=True)
def _anexos(tmp_path, monkeypatch):
    monkeypatch.setenv("CARBONO_ANEXOS", str(tmp_path / "anexos"))


@pytest.fixture
def s(tmp_path):
    c = conectar(str(tmp_path / "p.db"))
    ins = lambda sql, *a: c.execute(sql, a).lastrowid  # noqa: E731
    d = {"c": c}
    d["reg"] = ins("INSERT INTO usuario (nome,papel) VALUES ('R','registrador')")
    d["pref"] = ins("INSERT INTO organizacao (nome,cnpj,tipo,esfera) VALUES ('Pref','11111111000191','orgao_publico','municipal')")
    d["emp"] = ins("INSERT INTO organizacao (nome,cnpj,tipo) VALUES ('Fazenda','22222222000191','empresa')")
    d["assoc"] = ins("INSERT INTO organizacao (nome,cnpj,tipo) VALUES ('Associação Quilombola','33333333000191','empresa')")
    d["gestor"] = ins("INSERT INTO usuario (nome,papel,organizacao_id) VALUES ('G','elaborador',?)", d["pref"])
    d["fazendeiro"] = ins("INSERT INTO usuario (nome,papel,organizacao_id) VALUES ('F','fornecedor',?)", d["emp"])
    d["quilombo"] = ins("INSERT INTO usuario (nome,papel,organizacao_id) VALUES ('Q','fornecedor',?)", d["assoc"])
    d["outro"] = ins("INSERT INTO usuario (nome,papel,organizacao_id) VALUES ('O','fornecedor',?)", d["emp"])
    return d


def pdf(c, uid, nome="ev"):
    return anexos.salvar(c, uid, f"{nome}.pdf", "application/pdf", PDF + nome.encode())["id"]


def criar(s, uid, **kw):
    base = dict(nome="Biogás do aterro municipal", tipo="biogas", natureza_area="publica_propria",
                linha_base="Queima em flare aberto, sem captação de metano.", estimativa_tco2e_ano=5000, **MOC)
    return pj.criar(s["c"], s[uid], **{**base, **kw})


def concluir_etapa(s, uid, pid):
    p = pj.ver(s["c"], s[uid], pid)
    for it in p["itens"]:
        if it["etapa"] == p["etapa"] and not it["concluido"]:
            pj.concluir_item(s["c"], s[uid], it["id"], anexo_id=pdf(s["c"], s[uid], f"i{it['id']}"), responsavel="Equipe")


# ---------------------------------------------------------------- titularidade (art. 43)
def test_titularidade_conforme_a_natureza_da_area(s):
    with pytest.raises(ErroNegocio, match="art. 43, IX"):
        criar(s, "gestor", natureza_area="publica_terceiros")
    with pytest.raises(ErroNegocio, match="titular é o ente"):
        criar(s, "fazendeiro", natureza_area="publica_propria")
    with pytest.raises(ErroNegocio, match="CAR"):
        criar(s, "fazendeiro", tipo="restauracao_psa", natureza_area="privada")
    with pytest.raises(ErroNegocio, match="coordenadas"):
        criar(s, "gestor", latitude=40.0)
    assert criar(s, "fazendeiro", tipo="restauracao_psa", natureza_area="privada", car="MG-3143302-ABC")


def test_parceria_em_area_publica_exige_licitacao(s):
    pid = criar(s, "gestor")
    with pytest.raises(ErroNegocio, match="licitação"):
        pj.registrar_parceria(s["c"], s["gestor"], pid, desenvolvedor_cnpj="66666666000191", desenvolvedor_nome="Dev",
                              percentual_titular=60, percentual_desenvolvedor=40)
    with pytest.raises(ErroNegocio, match="100%"):
        pj.registrar_parceria(s["c"], s["gestor"], pid, desenvolvedor_cnpj="66666666000191", desenvolvedor_nome="Dev",
                              percentual_titular=60, percentual_desenvolvedor=30, licitacao_ref="Concorrência 3/2026")
    pj.registrar_parceria(s["c"], s["gestor"], pid, desenvolvedor_cnpj="66666666000191", desenvolvedor_nome="Dev",
                          percentual_titular=60, percentual_desenvolvedor=40, licitacao_ref="Concorrência 3/2026")


# ---------------------------------------------------------------- funil e travas
def test_funil_exige_checklist_com_evidencia_e_anuencia(s):
    c = s["c"]
    pid = criar(s, "gestor")
    with pytest.raises(ErroNegocio, match="Concluir com evidência"):
        pj.avancar(c, s["gestor"], pid)
    with pytest.raises(NaoAutorizado):
        pj.avancar(c, s["fazendeiro"], pid)
    item_futuro = next(i for i in pj.ver(c, s["gestor"], pid)["itens"] if i["etapa"] == "validacao")
    with pytest.raises(ErroNegocio, match="etapa futura"):
        pj.concluir_item(c, s["gestor"], item_futuro["id"], anexo_id=pdf(c, s["gestor"]), responsavel="X")
    for _ in range(2):  # ideia > viabilidade > concepção
        concluir_etapa(s, "gestor", pid)
        pj.avancar(c, s["gestor"], pid)
    concluir_etapa(s, "gestor", pid)
    with pytest.raises(ErroNegocio, match="art. 49"):
        pj.avancar(c, s["gestor"], pid)
    pj.atualizar(c, s["gestor"], pid, anuencia_ref="Ofício 12/2026 da Secretaria de Meio Ambiente")
    assert pj.avancar(c, s["gestor"], pid) == "validacao"
    for _ in range(2):  # validação > verificação > emissão
        concluir_etapa(s, "gestor", pid)
        if pj.ver(c, s["gestor"], pid)["etapa"] == "verificacao":
            with pytest.raises(ErroNegocio, match="certificadora"):
                pj.avancar(c, s["gestor"], pid)
            pj.atualizar(c, s["gestor"], pid, certificadora="Verra", registro_ref="VCS 9999")
        pj.avancar(c, s["gestor"], pid)
    concluir_etapa(s, "gestor", pid)
    pj.atualizar(c, s["gestor"], pid, lote_serial="VCS-9999-2027-1")
    with pytest.raises(ErroNegocio, match="escriturado"):
        pj.avancar(c, s["gestor"], pid)
    registro.emitir(c, s["reg"], conta_id=registro.abrir_conta(c, s["pref"]), tipo_ativo="VOLUNTARIO", projeto="Biogás",
                    metodologia="ACM0001", certificadora="Verra", certificado_anexo_id=pdf(c, s["reg"]), safra=2027,
                    quantidade=5000, serial="VCS-9999-2027-1")
    assert pj.avancar(c, s["gestor"], pid) == "venda"


def test_comunidade_exige_consulta_e_repartição_minima(s):
    c = s["c"]
    pid = criar(s, "quilombo", nome="Restauração no território", tipo="restauracao_psa", natureza_area="quilombola")
    concluir_etapa(s, "quilombo", pid)
    pj.avancar(c, s["quilombo"], pid)
    concluir_etapa(s, "quilombo", pid)
    pend = pj.pendencias(c, pid)
    assert any("art. 47, I, a" in p for p in pend) and any("art. 47, I, b" in p for p in pend)
    with pytest.raises(ErroNegocio, match="50%"):
        pj.registrar_parceria(c, s["quilombo"], pid, desenvolvedor_cnpj="66666666000191", desenvolvedor_nome="Dev",
                              percentual_titular=0, percentual_desenvolvedor=60, percentual_comunidade=40)
    pj.registrar_parceria(c, s["quilombo"], pid, desenvolvedor_cnpj="66666666000191", desenvolvedor_nome="Dev",
                          percentual_titular=0, percentual_desenvolvedor=45, percentual_comunidade=55)
    pj.atualizar(c, s["quilombo"], pid, consulta_anexo_id=pdf(c, s["quilombo"], "ata"))
    assert pj.avancar(c, s["quilombo"], pid) == "concepcao"
    concluir_etapa(s, "quilombo", pid)
    pend = pj.pendencias(c, pid)
    assert any("Assinar" in p for p in pend) and any("art. 43, § 5º" in p for p in pend)
    pj.assinar_parceria(c, s["quilombo"], pid, assinatura_ref="gov.br 123", averbacao_ref="Av. 4 da matrícula 999")
    assert pj.avancar(c, s["quilombo"], pid) == "validacao"


def test_projeto_visivel_ao_titular_e_ao_desenvolvedor(s):
    c = s["c"]
    pid = criar(s, "gestor")
    with pytest.raises(NaoAutorizado):
        pj.ver(c, s["fazendeiro"], pid)
    pj.registrar_parceria(c, s["gestor"], pid, desenvolvedor_cnpj="22222222000191", desenvolvedor_nome="Fazenda",
                          percentual_titular=50, percentual_desenvolvedor=50, licitacao_ref="Concorrência 1/2026")
    assert pj.ver(c, s["fazendeiro"], pid)["parceria"]["desenvolvedor"] == "Fazenda"
    assert [p["id"] for p in pj.listar(c, s["outro"])] == [pid]


def test_api_projetos(tmp_path):
    app = criar_app(str(tmp_path / "api.db"))
    cli = TestClient(app)
    c = conectar(app.state.caminho)
    H = lambda u: {"X-Usuario-Id": str(u)}  # noqa: E731
    reg = c.execute("INSERT INTO usuario (nome,papel) VALUES ('R','registrador')").lastrowid
    org = cli.post("/organizacoes", headers=H(reg), json={"nome": "Pref", "cnpj": "11111111000191",
                                                          "tipo": "orgao_publico"}).json()
    g = cli.post("/usuarios", headers=H(reg), json={"nome": "G", "papel": "elaborador", "organizacao_id": org["id"]}).json()["id"]
    pid = cli.post("/projetos", headers=H(g), json={
        "nome": "Solar nas escolas", "tipo": "solar", "natureza_area": "publica_propria",
        "linha_base": "Energia da rede, sem geração própria.", "estimativa_tco2e_ano": 80, **MOC}).json()["id"]
    det = cli.get(f"/projetos/{pid}", headers=H(g)).json()
    assert det["etapa"] == "ideia" and len(det["pendencias"]) == 2
    assert cli.post(f"/projetos/{pid}/avanco", headers=H(g)).status_code == 409
    assert cli.post(f"/projetos/{pid}/parceria", headers=H(g), json={
        "desenvolvedor_cnpj": "66666666000191", "desenvolvedor_nome": "Dev", "percentual_titular": 50,
        "percentual_desenvolvedor": 50}).status_code == 409
    assert len(cli.get("/projetos/regras").json()["etapas"]) == 7
