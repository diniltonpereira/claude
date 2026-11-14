"""Testes dos recursos incorporados da especificação CarbonMG."""
import io
import sqlite3
from datetime import date

import pytest
from fastapi.testclient import TestClient

from plataforma import anexos, auditoria, compensacao, fatores, mrv, painel, registro, relatorio, terceirizadas
from plataforma.api import criar_app
from plataforma.banco import EsquemaIncompativel, ErroNegocio, NaoAutorizado, NaoEncontrado, conectar

PDF = b"%PDF-1.4\n% teste\n%%EOF\n"
HOJE = date(2026, 11, 14)


@pytest.fixture(autouse=True)
def _anexos(tmp_path, monkeypatch):
    monkeypatch.setenv("CARBONO_ANEXOS", str(tmp_path / "anexos"))


@pytest.fixture
def s(tmp_path):
    c = conectar(str(tmp_path / "t.db"))
    ins = lambda sql, *a: c.execute(sql, a).lastrowid  # noqa: E731
    d = {"c": c}
    d["reg"] = ins("INSERT INTO usuario (nome,papel) VALUES ('R','registrador')")
    d["org"] = ins("INSERT INTO organizacao (nome,cnpj,tipo,esfera) VALUES ('Pref','11111111000191','orgao_publico','municipal')")
    d["outra"] = ins("INSERT INTO organizacao (nome,cnpj,tipo) VALUES ('Outra','99999999000191','orgao_publico')")
    d["oia"] = ins("INSERT INTO organizacao (nome,cnpj,tipo) VALUES ('OIA','44444444000191','empresa')")
    d["elab"] = ins("INSERT INTO usuario (nome,papel,organizacao_id) VALUES ('E','elaborador',?)", d["org"])
    d["elab2"] = ins("INSERT INTO usuario (nome,papel,organizacao_id) VALUES ('E2','elaborador',?)", d["outra"])
    d["fiscal"] = ins("INSERT INTO usuario (nome,papel,organizacao_id) VALUES ('F','fiscal',?)", d["org"])
    d["verif"] = ins("INSERT INTO usuario (nome,papel,organizacao_id,acreditado) VALUES ('V','verificador',?,1)", d["oia"])
    d["mod"] = ins("INSERT INTO usuario (nome,papel) VALUES ('M','moderador')")
    d["aud"] = ins("INSERT INTO usuario (nome,papel) VALUES ('A','auditor')")
    d["conta"] = registro.abrir_conta(c, d["org"])
    return d


def pdf(s, uid, nome="nf"):
    return anexos.salvar(s["c"], uid, f"{nome}.pdf", "application/pdf", PDF + nome.encode())["id"]


def aprovar(s, inv):
    for u, a in [("elab", "submeter"), ("verif", "assumir"), ("verif", "verificar"), ("mod", "aprovar")]:
        mrv.transitar(s["c"], s[u], inv, a)


# ---------------------------------------------------------------- fatores versionados
def test_nova_versao_preserva_a_anterior_e_a_atividade_mantem_a_sua(s):
    c = s["c"]
    inv = mrv.criar_inventario(c, s["elab"], s["org"], 2025)
    a1 = mrv.adicionar_atividade(c, s["elab"], inv, descricao="luz", fator="energia_sin", quantidade=100,
                                 anexo_id=pdf(s, s["elab"]))
    fatores.nova_versao(c, s["reg"], "energia_sin", valor=0.05, fonte="MCTI 2024", ano_referencia=2024)
    a2 = mrv.adicionar_atividade(c, s["elab"], inv, descricao="luz 2", fator="energia_sin", quantidade=100,
                                 anexo_id=pdf(s, s["elab"], "b"))
    v = {r["id"]: (r["fator_id"], r["tco2e"]) for r in c.execute("SELECT * FROM atividade")}
    assert v[a1][1] == pytest.approx(3.85) and v[a2][1] == pytest.approx(5.0)
    assert fatores.por_id(c, v[a1][0]).versao == 1 and fatores.por_id(c, v[a2][0]).versao == 2
    historico = [f for f in fatores.listar(c, incluir_historico=True) if f["codigo"] == "energia_sin"]
    assert [(f["versao"], f["vigente"]) for f in historico] == [(1, 0), (2, 1)]
    # editar a quantidade da atividade antiga mantém a versão 1
    mrv.editar_atividade(c, s["elab"], a1, quantidade=200)
    assert c.execute("SELECT tco2e FROM atividade WHERE id=?", (a1,)).fetchone()[0] == pytest.approx(7.7)


def test_fator_publicado_e_imutavel_e_so_registrador_publica(s):
    c = s["c"]
    with pytest.raises(NaoAutorizado):
        fatores.nova_versao(c, s["elab"], "glp", valor=1, fonte="x", ano_referencia=2024)
    with pytest.raises(sqlite3.DatabaseError, match="imutável"):
        c.execute("UPDATE fator_emissao SET valor=0 WHERE codigo='glp'")
    with pytest.raises(sqlite3.DatabaseError, match="nova versão"):
        c.execute("DELETE FROM fator_emissao WHERE codigo='glp'")


def test_inventario_ar6_usa_gwp_do_ar6(s):
    c = s["c"]
    inv = mrv.criar_inventario(c, s["elab"], s["org"], 2025, gwp_conjunto="AR6")
    mrv.adicionar_atividade(c, s["elab"], inv, descricao="aterro", gas="CH4", quantidade=10, anexo_id=pdf(s, s["elab"]))
    assert mrv.totais(c, inv)["escopo1"] == pytest.approx(270)
    with pytest.raises(ErroNegocio):
        mrv.criar_inventario(c, s["elab"], s["org"], 2026, gwp_conjunto="AR4")


# ---------------------------------------------------------------- anexos e comprovação
def test_sem_anexo_fica_pendente_e_fora_do_total(s):
    c = s["c"]
    inv = mrv.criar_inventario(c, s["elab"], s["org"], 2025)
    aid = mrv.adicionar_atividade(c, s["elab"], inv, descricao="glp", fator="glp", quantidade=1000)
    t = mrv.totais(c, inv)
    assert t["total"] == 0 and t["pendentes_comprovacao"] == {"quantidade": 1, "tco2e": pytest.approx(2.932)}
    with pytest.raises(ErroNegocio, match="comprovadas"):
        mrv.transitar(c, s["elab"], inv, "submeter")
    mrv.editar_atividade(c, s["elab"], aid, anexo_id=pdf(s, s["elab"]))
    assert mrv.totais(c, inv)["total"] == pytest.approx(2.932)


def test_anexo_valida_tipo_e_conteudo_e_isola_organizacoes(s, tmp_path):
    c = s["c"]
    with pytest.raises(ErroNegocio, match="não corresponde"):
        anexos.salvar(c, s["elab"], "x.pdf", "application/pdf", b"MZ executavel")
    with pytest.raises(ErroNegocio, match="Tipo não aceito"):
        anexos.salvar(c, s["elab"], "x.exe", "application/x-msdownload", b"MZ")
    with pytest.raises(NaoAutorizado):
        anexos.salvar(c, s["aud"], "x.pdf", "application/pdf", PDF)
    a = pdf(s, s["elab"])
    inv2 = mrv.criar_inventario(c, s["elab2"], s["outra"], 2025)
    with pytest.raises(NaoAutorizado):
        mrv.adicionar_atividade(c, s["elab2"], inv2, descricao="x", fator="glp", quantidade=1, anexo_id=a)
    # adulteração do arquivo no disco é detectada
    sha = c.execute("SELECT sha256 FROM anexo WHERE id=?", (a,)).fetchone()[0]
    (anexos.diretorio() / sha).write_bytes(b"%PDF adulterado")
    with pytest.raises(ErroNegocio, match="adulteração"):
        anexos.abrir(c, s["elab"], a)


def test_emissao_evitada_nao_abate_o_inventario(s):
    c = s["c"]
    inv = mrv.criar_inventario(c, s["elab"], s["org"], 2025)
    mrv.adicionar_atividade(c, s["elab"], inv, descricao="luz", fator="energia_sin", quantidade=100,
                            anexo_id=pdf(s, s["elab"]))
    mrv.adicionar_atividade(c, s["elab"], inv, descricao="solar", fator="energia_sin", quantidade=40,
                            evitada=True, anexo_id=pdf(s, s["elab"], "solar"))
    t = mrv.totais(c, inv)
    assert t["total"] == pytest.approx(3.85) and t["emissoes_evitadas"] == pytest.approx(1.54)


def test_edicao_e_remocao_ficam_na_trilha_que_nao_pode_ser_alterada(s):
    c = s["c"]
    inv = mrv.criar_inventario(c, s["elab"], s["org"], 2025)
    aid = mrv.adicionar_atividade(c, s["elab"], inv, descricao="glp", fator="glp", quantidade=10,
                                  anexo_id=pdf(s, s["elab"]))
    mrv.editar_atividade(c, s["elab"], aid, quantidade=20)
    mrv.remover_atividade(c, s["elab"], aid)
    acoes = [a["acao"] for a in auditoria.consultar(c, entidade="atividade", entidade_id=aid)]
    assert acoes == ["remover", "editar", "criar"]
    with pytest.raises(sqlite3.DatabaseError, match="somente inclusão"):
        c.execute("DELETE FROM auditoria")


# ---------------------------------------------------------------- painel, hierarquia e metas
def test_painel_soma_pela_hierarquia_compara_anos_e_mede_meta(s):
    c = s["c"]
    saude = mrv.criar_instalacao(c, s["elab"], s["org"], nome="Saúde", natureza="unidade")
    hosp = mrv.criar_instalacao(c, s["elab"], s["org"], nome="Hospital", pai_id=saude)
    obras = mrv.criar_instalacao(c, s["elab"], s["org"], nome="Obras", natureza="unidade")
    frota = mrv.criar_instalacao(c, s["elab"], s["org"], nome="Frota", natureza="fonte_movel", pai_id=obras)
    for ano, diesel in ((2024, 100_000), (2025, 60_000)):
        inv = mrv.criar_inventario(c, s["elab"], s["org"], ano)
        mrv.adicionar_atividade(c, s["elab"], inv, descricao="luz", fator="energia_sin", quantidade=1000,
                                mes=1, instalacao_id=hosp, anexo_id=pdf(s, s["elab"], f"l{ano}"))
        mrv.adicionar_atividade(c, s["elab"], inv, descricao="diesel", fator="diesel_b", quantidade=diesel,
                                mes=2, instalacao_id=frota, anexo_id=pdf(s, s["elab"], f"d{ano}"))
    painel.definir_meta(c, s["elab"], s["org"], ano_base=2024, ano_alvo=2025, reducao_percentual=10)
    p = painel.painel(c, s["org"], 2025)
    assert [r["unidade"] for r in p["ranking_unidades"]] == ["Obras", "Saúde"]
    assert p["por_mes"][1] == pytest.approx(38.5) and p["por_mes"][2] > 100
    assert any("Obras caiu 40%" in a for a in p["alertas"])
    assert p["meta"]["progresso_percentual"] == 100 and p["variacao_anual_percentual"] < -10
    with pytest.raises(NaoAutorizado):
        painel.definir_meta(c, s["elab2"], s["org"], ano_base=2024, ano_alvo=2026, reducao_percentual=5)


# ---------------------------------------------------------------- terceirizadas
@pytest.fixture
def ct(s):
    c = s["c"]
    cid = terceirizadas.convidar(c, s["fiscal"], orgao_id=s["org"], fornecedor_cnpj="55555555000191",
                                 fornecedor_nome="Limpa", numero="1/2024", objeto="Limpeza",
                                 vigencia_inicio="2024-01-01", vigencia_fim="2027-12-31", clausula_relato=True)
    forn_org = c.execute("SELECT fornecedor_id FROM contrato WHERE id=?", (cid,)).fetchone()[0]
    s["forn"] = c.execute("INSERT INTO usuario (nome,papel,organizacao_id) VALUES ('T','fornecedor',?)",
                          (forn_org,)).lastrowid
    s["forn_org"] = forn_org
    return cid


def lancar_meses(s, cid, ate, litros=lambda n: 1000, quem="forn", com_anexo=True):
    a, m, n = 2024, 1, 0
    while (a, m) <= ate:
        terceirizadas.lancar(s["c"], s[quem], cid, ano=a, mes=m, descricao="diesel", fator="diesel_b",
                             quantidade=litros(n), anexo_id=pdf(s, s[quem], f"{a}{m}") if com_anexo else None,
                             hoje=HOJE)
        a, m, n = (a + 1, 1, n + 1) if m == 12 else (a, m + 1, n + 1)


def test_convite_aceite_e_limites_do_lancamento(s, ct):
    c = s["c"]
    with pytest.raises(ErroNegocio, match="Aceite"):
        terceirizadas.lancar(c, s["forn"], ct, ano=2024, mes=1, descricao="x", fator="glp", quantidade=1, hoje=HOJE)
    terceirizadas.aceitar(c, s["forn"], ct)
    with pytest.raises(ErroNegocio, match="futuro"):
        terceirizadas.lancar(c, s["forn"], ct, ano=2026, mes=12, descricao="x", fator="glp", quantidade=1, hoje=HOJE)
    with pytest.raises(ErroNegocio, match="vigência"):
        terceirizadas.lancar(c, s["forn"], ct, ano=2023, mes=12, descricao="x", fator="glp", quantidade=1, hoje=HOJE)
    with pytest.raises(NaoAutorizado):
        terceirizadas.lancar(c, s["elab2"], ct, ano=2024, mes=1, descricao="x", fator="glp", quantidade=1, hoje=HOJE)
    lid = terceirizadas.lancar(c, s["fiscal"], ct, ano=2024, mes=1, descricao="x", fator="glp", quantidade=1,
                               anexo_id=pdf(s, s["fiscal"]), hoje=HOJE)
    assert c.execute("SELECT lancado_pelo_fiscal FROM lancamento_fornecedor WHERE id=?", (lid,)).fetchone()[0] == 1


def test_inadimplencia_so_com_clausula_contratual(s, ct):
    c = s["c"]
    terceirizadas.aceitar(c, s["forn"], ct)
    sit = {(x["ano"], x["mes"]): x["situacao"] for x in terceirizadas.situacao_mensal(c, ct, HOJE)}
    assert sit[(2026, 10)] == "inadimplente" and sit[(2026, 11)] == "em_prazo"
    c.execute("UPDATE contrato SET clausula_relato=0 WHERE id=?", (ct,))
    sit = {(x["ano"], x["mes"]): x["situacao"] for x in terceirizadas.situacao_mensal(c, ct, HOJE)}
    assert sit[(2026, 9)] == "sem_lancamento"


def test_score_classes_selo_e_verificacao_publica(s, ct):
    c = s["c"]
    terceirizadas.aceitar(c, s["forn"], ct)
    lancar_meses(s, ct, (2026, 10), litros=lambda n: 1000 - 10 * n)  # regular e em queda
    for i in range(2):
        terceirizadas.registrar_acao(c, s["forn"], ct, data=f"2026-0{i + 1}-10", descricao="treinamento",
                                     anexo_id=pdf(s, s["forn"], f"acao{i}"))
    r = terceirizadas.calcular_score(c, ct, HOJE)
    assert r["pontos"]["regularidade"] == 40 and r["pontos"]["reducao"] == 30 and r["pontos"]["acoes"] == 5
    assert r["classe"] == "B" and r["score"] == 75
    selo = terceirizadas.emitir_selo(c, s["fiscal"], ct, HOJE)
    pub = terceirizadas.verificar_selo(c, selo["codigo"], HOJE)
    assert pub["titulo"] == "Selo de Gestão de Emissões — Nível B" and pub["situacao"] == "valido" and pub["integro"]
    assert "Descarbonizado" not in pub["titulo"]
    assert terceirizadas.verificar_selo(c, selo["codigo"], date(2027, 11, 15))["situacao"] == "expirado"
    with pytest.raises(sqlite3.DatabaseError, match="imutável"):
        c.execute("UPDATE selo SET classe='A'")
    with pytest.raises(NaoAutorizado):
        terceirizadas.emitir_selo(c, s["forn"], ct, HOJE)


def test_classe_d_nao_recebe_selo_e_queda_alerta_o_fiscal(s, ct):
    c = s["c"]
    terceirizadas.aceitar(c, s["forn"], ct)
    lancar_meses(s, ct, (2026, 10))
    terceirizadas.calcular_score(c, ct, date(2026, 10, 14))
    c.execute("DELETE FROM lancamento_fornecedor WHERE ano=2026")  # sem lançamentos em 2026
    r = terceirizadas.calcular_score(c, ct, HOJE)
    assert r["classe"] == "D" and r["alerta_queda"]
    alertas = [n for n in terceirizadas.notificacoes(c, ct) if n["tipo"] == "alerta"]
    assert alertas and alertas[0]["destinatario"] == "fiscal"
    with pytest.raises(ErroNegocio, match="classe D"):
        terceirizadas.emitir_selo(c, s["fiscal"], ct, HOJE)


def test_rotina_diaria_lembra_alerta_e_nao_repete(s, ct):
    c = s["c"]
    terceirizadas.aceitar(c, s["forn"], ct)
    lancar_meses(s, ct, (2026, 9))
    assert terceirizadas.rotina_diaria(c, date(2026, 11, 5))["lembretes"] == 1   # outubro ainda no prazo
    assert terceirizadas.rotina_diaria(c, date(2026, 11, 6))["lembretes"] == 0   # não repete
    assert terceirizadas.rotina_diaria(c, date(2026, 11, 11))["alertas"] == 1    # prazo vencido


def test_notificacao_formal_so_pelo_fiscal(s, ct):
    c = s["c"]
    with pytest.raises(NaoAutorizado):
        terceirizadas.notificar(c, s["forn"], ct, assunto="x", texto="y")
    nid = terceirizadas.notificar(c, s["fiscal"], ct, assunto="Relato em atraso", texto="Regularize em 5 dias.",
                                  prazo="2026-11-20")
    assert terceirizadas.notificacoes(c, ct)[0]["id"] == nid


def test_importacao_para_o_escopo3_usa_so_comprovados_e_substitui(s, ct):
    c = s["c"]
    terceirizadas.aceitar(c, s["forn"], ct)
    lancar_meses(s, ct, (2025, 12))
    terceirizadas.lancar(c, s["forn"], ct, ano=2025, mes=12, descricao="sem nota", fator="glp", quantidade=500,
                         hoje=HOJE)
    inv = mrv.criar_inventario(c, s["elab"], s["org"], 2025)
    r = terceirizadas.importar_para_inventario(c, s["elab"], inv)
    assert r == {"atividades_criadas": 1, "lancamentos_pendentes_ignorados": 1}
    esperado = 12 * 1000 * 0.86 * 0.002681
    assert mrv.totais(c, inv)["escopo3"] == pytest.approx(esperado)
    terceirizadas.importar_para_inventario(c, s["elab"], inv)
    assert mrv.totais(c, inv)["escopo3"] == pytest.approx(esperado)  # não duplica


# ---------------------------------------------------------------- créditos voluntários e certificado
def test_credito_voluntario_exige_certificado_e_aposentadoria_externa(s):
    c = s["c"]
    with pytest.raises(ErroNegocio, match="certificadora"):
        registro.emitir(c, s["reg"], conta_id=s["conta"], tipo_ativo="VOLUNTARIO", projeto="P", metodologia="M",
                        safra=2025, quantidade=100, serial="V1", certificadora="Qualquer")
    with pytest.raises(ErroNegocio, match="certificado"):
        registro.emitir(c, s["reg"], conta_id=s["conta"], tipo_ativo="VOLUNTARIO", projeto="P", metodologia="M",
                        safra=2025, quantidade=100, serial="V1", certificadora="Verra")
    lote = registro.emitir(c, s["reg"], conta_id=s["conta"], tipo_ativo="VOLUNTARIO", projeto="P",
                           metodologia="M", safra=2025, quantidade=100, serial="V1", certificadora="Verra",
                           certificado_anexo_id=pdf(s, s["reg"]))["lote_id"]
    inv = mrv.criar_inventario(c, s["elab"], s["org"], 2025)
    mrv.adicionar_atividade(c, s["elab"], inv, descricao="glp", fator="glp", quantidade=10_000,
                            anexo_id=pdf(s, s["elab"]))
    aprovar(s, inv)
    plano = compensacao.compensar(c, s["elab"], inv, simular=True)
    assert plano["plano"][0]["exige_registro_externo"]
    with pytest.raises(ErroNegocio, match="Aposente primeiro"):
        compensacao.compensar(c, s["elab"], inv)
    r = compensacao.compensar(c, s["elab"], inv, registros_externos={lote: "VCS-RET-1"})
    h = c.execute("SELECT hash FROM movimento WHERE id=?", (r["plano"][0]["movimento_id"],)).fetchone()[0]
    cert = registro.certificado_por_hash(c, h)
    assert cert["registro_externo_ref"] == "VCS-RET-1" and cert["certificadora"] == "Verra"
    assert cert["titular"] == "Pref" and cert["livro_razao_integro"]
    with pytest.raises(NaoEncontrado):
        registro.certificado_por_hash(c, "0" * 64)


# ---------------------------------------------------------------- relatório e transparência
def test_relatorio_html_e_xlsx(s):
    c = s["c"]
    inv = mrv.criar_inventario(c, s["elab"], s["org"], 2025)
    mrv.adicionar_atividade(c, s["elab"], inv, descricao="Frota <script>", fator="diesel_b", quantidade=1000,
                            anexo_id=pdf(s, s["elab"]))
    mrv.adicionar_atividade(c, s["elab"], inv, descricao="sem nota", fator="glp", quantidade=1)
    h = relatorio.html_relatorio(c, inv)
    assert "documento preliminar" in h and "Pendentes de comprovação" in h and "diesel_b v1" in h
    assert "<script>" not in h and "&lt;script&gt;" in h
    assert "carbono neutro" not in h.lower().replace("não afirma neutralidade de carbono", "")
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(relatorio.xlsx_relatorio(c, inv)))
    assert wb.sheetnames == ["Resumo", "Atividades", "Fatores", "Trilha"]
    assert wb["Atividades"].max_row == 3


def test_transparencia_mostra_so_inventario_aprovado(s):
    c = s["c"]
    inv = mrv.criar_inventario(c, s["elab"], s["org"], 2025)
    mrv.adicionar_atividade(c, s["elab"], inv, descricao="glp", fator="glp", quantidade=1000,
                            anexo_id=pdf(s, s["elab"]))
    assert painel.transparencia_organizacao(c, s["org"])["inventario_aprovado"] is None
    aprovar(s, inv)
    t = painel.transparencia_organizacao(c, s["org"])
    assert t["ano"] == 2025 and "carbono neutro" in t["aviso"]


def test_banco_de_versao_anterior_e_recusado(tmp_path):
    p = str(tmp_path / "velho.db")
    v = sqlite3.connect(p)
    v.execute("CREATE TABLE organizacao (id INTEGER)")
    v.close()
    with pytest.raises(EsquemaIncompativel):
        conectar(p)


# ---------------------------------------------------------------- API
def test_api_terceirizada_selo_qr_e_auditor_somente_leitura(tmp_path):
    app = criar_app(str(tmp_path / "api.db"))
    cli = TestClient(app)
    c = conectar(app.state.caminho)
    H = lambda u: {"X-Usuario-Id": str(u)}  # noqa: E731
    reg = c.execute("INSERT INTO usuario (nome,papel) VALUES ('R','registrador')").lastrowid
    org = cli.post("/organizacoes", headers=H(reg), json={"nome": "Pref", "cnpj": "11111111000191",
                                                          "tipo": "orgao_publico", "municipio_ibge": "3107307"}).json()
    fiscal = cli.post("/usuarios", headers=H(reg), json={"nome": "F", "papel": "fiscal",
                                                         "organizacao_id": org["id"]}).json()["id"]
    aud = cli.post("/usuarios", headers=H(reg), json={"nome": "A", "papel": "auditor"}).json()["id"]
    cid = cli.post("/contratos", headers=H(fiscal), json={
        "orgao_id": org["id"], "fornecedor_cnpj": "55555555000191", "fornecedor_nome": "Limpa",
        "numero": "1/2025", "objeto": "Limpeza", "vigencia_inicio": "2025-01-01", "vigencia_fim": "2027-12-31",
        "clausula_relato": True}).json()["id"]
    forn_org = cli.get(f"/contratos/{cid}", headers=H(fiscal)).json()["fornecedor_id"]
    forn = cli.post("/usuarios", headers=H(reg), json={"nome": "T", "papel": "fornecedor",
                                                       "organizacao_id": forn_org}).json()["id"]
    assert cli.post(f"/contratos/{cid}/aceite", headers=H(forn)).status_code == 200
    up = cli.post("/anexos", headers=H(forn), files={"arquivo": ("nf.pdf", PDF, "application/pdf")}).json()
    assert cli.post(f"/contratos/{cid}/lancamentos", headers=H(forn), json={
        "ano": 2025, "mes": 3, "descricao": "diesel", "fator": "diesel_b", "quantidade": 500,
        "anexo_id": up["id"]}).status_code == 201
    # o fiscal do órgão pode baixar o anexo do lançamento da terceirizada
    assert cli.get(f"/anexos/{up['id']}", headers=H(fiscal)).status_code == 200
    det = cli.get(f"/contratos/{cid}", headers=H(forn)).json()
    assert det["lancamentos"][0]["tco2e"] > 0 and det["fornecedor"] == "Limpa"
    assert cli.post(f"/contratos/{cid}/selos", headers=H(forn)).status_code == 403
    assert cli.post("/organizacoes", headers=H(aud), json={"nome": "X", "cnpj": "12345678000191",
                                                           "tipo": "empresa"}).status_code == 403
    assert cli.get("/auditoria", headers=H(aud)).status_code == 200
    assert cli.get("/auditoria", headers=H(fiscal)).status_code == 403
    assert cli.get("/publico/selos/naoexiste").status_code == 404
    hist = cli.get("/fatores", params={"historico": True}).json()
    assert "AR6" in hist["gwp"] and any(f["codigo"] == "energia_sin_kwh" for f in hist["fatores"])
    novo = cli.post("/fatores", headers=H(reg), json={"codigo": "energia_sin", "valor": 0.04,
                                                      "fonte": "MCTI", "ano_referencia": 2025})
    assert novo.status_code == 201
