import sqlite3

import pytest
from fastapi.testclient import TestClient

from plataforma import compensacao, mrv, registro
from plataforma.api import criar_app
from plataforma.banco import ErroNegocio, NaoAutorizado, conectar
from plataforma.calculo import calcular_por_fator, calcular_por_gas
from plataforma.enquadramento import enquadrar, faixa_sbce


@pytest.fixture
def cen(tmp_path):
    """Cenário mínimo: órgão público, OVV, moderador, gerador de créditos."""
    c = conectar(str(tmp_path / "t.db"))
    ins = lambda sql, *a: c.execute(sql, a).lastrowid  # noqa: E731
    s = {"c": c}
    s["reg"] = ins("INSERT INTO usuario (nome,papel) VALUES ('R','registrador')")
    s["org"] = ins("INSERT INTO organizacao (nome,cnpj,tipo) VALUES ('Pref','11111111000191','orgao_publico')")
    s["ovv"] = ins("INSERT INTO organizacao (nome,cnpj,tipo) VALUES ('OVV','44444444000191','empresa')")
    s["ger"] = ins("INSERT INTO organizacao (nome,cnpj,tipo) VALUES ('Flor','33333333000191','concessao_florestal')")
    s["elab"] = ins("INSERT INTO usuario (nome,papel,organizacao_id) VALUES ('E','elaborador',?)", s["org"])
    s["verif"] = ins("INSERT INTO usuario (nome,papel,organizacao_id,ovv_credenciado) VALUES ('V','verificador',?,1)", s["ovv"])
    s["mod"] = ins("INSERT INTO usuario (nome,papel) VALUES ('M','moderador')")
    s["gger"] = ins("INSERT INTO usuario (nome,papel,organizacao_id) VALUES ('G','elaborador',?)", s["ger"])
    s["conta_org"] = registro.abrir_conta(c, s["org"])
    s["conta_ger"] = registro.abrir_conta(c, s["ger"])
    return s


def inventario_aprovado(s, ano=2025, diesel=10_000):
    c = s["c"]
    inv = mrv.criar_inventario(c, s["elab"], s["org"], ano)
    mrv.adicionar_atividade(c, s["elab"], inv, descricao="diesel", fator="diesel_b", quantidade=diesel)
    mrv.transitar(c, s["elab"], inv, "submeter")
    mrv.transitar(c, s["verif"], inv, "assumir")
    mrv.transitar(c, s["verif"], inv, "verificar")
    mrv.transitar(c, s["mod"], inv, "aprovar")
    return inv


def emitir(s, qtd=1000, safra=2024, tipo="CRVE", serial="BR-1", conta=None):
    return registro.emitir(s["c"], s["reg"], conta_id=conta or s["conta_ger"], tipo_ativo=tipo,
                           projeto="P", metodologia="M", safra=safra, quantidade=qtd,
                           serial=serial)["lote_id"]


# ---------------- Aferição ----------------
def test_mistura_separa_parcela_biogenica():
    r = calcular_por_fator("diesel_b", 1000, fracao_biogenica=0.14)
    assert r.tco2e == pytest.approx(1000 * 0.86 * 0.002681, rel=1e-6)
    assert r.tco2_biogenico > 0 and "fonte" in r.memoria


def test_etanol_nao_soma_ao_escopo():
    assert calcular_por_fator("etanol_hidratado", 1000).tco2e == 0


def test_gas_usa_gwp_ar5():
    assert calcular_por_gas("CH4", 1).tco2e == 28


@pytest.mark.parametrize("entrada", [("diesel_b", -1), ("inexistente", 1)])
def test_entradas_invalidas(entrada):
    with pytest.raises(ValueError):
        calcular_por_fator(*entrada)


# ---------------- Enquadramento ----------------
@pytest.mark.parametrize("t,faixa", [(10_000, "nao_regulado"), (10_001, "regulado_monitoramento"),
                                     (25_000, "regulado_monitoramento"), (25_001, "regulado_conciliacao")])
def test_limiares_sbce(t, faixa):
    assert faixa_sbce(t) == faixa


def test_agropecuaria_primaria_fica_fora():
    assert enquadrar("empresa", 90_000, atividade_agropecuaria_primaria=True).faixa_sbce == "nao_regulado"


def test_concessao_tem_obrigacoes_contratuais():
    titulos = [o.titulo for o in enquadrar("concessao", 30_000).obrigacoes]
    assert "Conciliação periódica de obrigações" in titulos
    assert "Receitas alternativas/acessórias" in titulos
    assert "Identificar o operador regulado" in titulos


# ---------------- Moderação / segregação ----------------
def test_elaborador_nao_verifica(cen):
    c = cen["c"]
    inv = mrv.criar_inventario(c, cen["elab"], cen["org"], 2025)
    mrv.adicionar_atividade(c, cen["elab"], inv, descricao="x", fator="glp", quantidade=1)
    mrv.transitar(c, cen["elab"], inv, "submeter")
    with pytest.raises(NaoAutorizado):
        mrv.transitar(c, cen["elab"], inv, "assumir")


def test_verificador_da_propria_organizacao_e_barrado(cen):
    c = cen["c"]
    interno = c.execute("INSERT INTO usuario (nome,papel,organizacao_id,ovv_credenciado) "
                        "VALUES ('I','verificador',?,1)", (cen["org"],)).lastrowid
    inv = mrv.criar_inventario(c, cen["elab"], cen["org"], 2025)
    mrv.adicionar_atividade(c, cen["elab"], inv, descricao="x", fator="glp", quantidade=1)
    mrv.transitar(c, cen["elab"], inv, "submeter")
    with pytest.raises(NaoAutorizado, match="pertencer"):
        mrv.transitar(c, interno, inv, "assumir")


def test_devolucao_exige_parecer_e_reabre_edicao(cen):
    c = cen["c"]
    inv = mrv.criar_inventario(c, cen["elab"], cen["org"], 2025)
    mrv.adicionar_atividade(c, cen["elab"], inv, descricao="x", fator="glp", quantidade=1)
    mrv.transitar(c, cen["elab"], inv, "submeter")
    mrv.transitar(c, cen["verif"], inv, "assumir")
    with pytest.raises(ErroNegocio):
        mrv.adicionar_atividade(c, cen["elab"], inv, descricao="y", fator="glp", quantidade=1)
    with pytest.raises(ErroNegocio, match="parecer"):
        mrv.transitar(c, cen["verif"], inv, "devolver")
    assert mrv.transitar(c, cen["verif"], inv, "devolver", "Sem nota fiscal") == "devolvido"
    mrv.adicionar_atividade(c, cen["elab"], inv, descricao="y", fator="glp", quantidade=1)


def test_falha_na_transicao_nao_deixa_rastro(cen):
    c = cen["c"]
    inv = mrv.criar_inventario(c, cen["elab"], cen["org"], 2025)
    with pytest.raises(ErroNegocio):
        mrv.transitar(c, cen["elab"], inv, "submeter")  # sem atividades
    assert mrv.historico(c, inv) == []


# ---------------- Registro ----------------
def test_nao_gasta_duas_vezes(cen):
    lote = emitir(cen, qtd=100)
    registro.aposentar(cen["c"], cen["gger"], lote_id=lote, quantidade=100,
                       finalidade="compensacao_voluntaria", beneficiario="X")
    with pytest.raises(ErroNegocio, match="aposentado"):
        registro.transferir(cen["c"], cen["gger"], lote_id=lote, quantidade=1, para_conta=cen["conta_org"])


def test_transferencia_parcial_divide_lote(cen):
    lote = emitir(cen, qtd=100)
    r = registro.transferir(cen["c"], cen["gger"], lote_id=lote, quantidade=30, para_conta=cen["conta_org"])
    assert sum(l["quantidade"] for l in registro.saldo(cen["c"], cen["conta_ger"])) == 70
    assert registro.saldo(cen["c"], cen["conta_org"])[0]["serial"] == "BR-1.1"
    assert r["lote_id"] != lote


def test_terceiro_nao_movimenta_conta_alheia(cen):
    lote = emitir(cen)
    with pytest.raises(NaoAutorizado):
        registro.transferir(cen["c"], cen["elab"], lote_id=lote, quantidade=1, para_conta=cen["conta_org"])


def test_idempotencia(cen):
    lote = emitir(cen, qtd=100)
    a = registro.transferir(cen["c"], cen["gger"], lote_id=lote, quantidade=10,
                            para_conta=cen["conta_org"], chave_idempotencia="k1")
    b = registro.transferir(cen["c"], cen["gger"], lote_id=lote, quantidade=10,
                            para_conta=cen["conta_org"], chave_idempotencia="k1")
    assert b["repetido"] and a["movimento_id"] == b["movimento_id"]
    assert sum(l["quantidade"] for l in registro.saldo(cen["c"], cen["conta_org"])) == 10


def test_livro_razao_detecta_adulteracao(cen):
    emitir(cen)
    c = cen["c"]
    assert registro.verificar_cadeia(c)["integra"]
    with pytest.raises(sqlite3.DatabaseError):
        c.execute("UPDATE movimento SET quantidade = 999999")
    c.execute("DROP TRIGGER movimento_sem_update")  # simula acesso direto malicioso ao banco
    c.execute("UPDATE movimento SET quantidade = 999999")
    assert registro.verificar_cadeia(c) == {"integra": False, "movimentos": 0, "quebra_em": 1}


# ---------------- Compensação ----------------
def test_compensa_somente_inventario_aprovado(cen):
    inv = mrv.criar_inventario(cen["c"], cen["elab"], cen["org"], 2025)
    with pytest.raises(ErroNegocio, match="aprovados"):
        compensacao.compensar(cen["c"], cen["elab"], inv)


def test_compensacao_completa_e_nao_repete(cen):
    inv = inventario_aprovado(cen)  # ≈ 23,06 tCO2e -> 24 unidades
    lote = emitir(cen, qtd=100)
    registro.transferir(cen["c"], cen["gger"], lote_id=lote, quantidade=50, para_conta=cen["conta_org"])
    r = compensacao.compensar(cen["c"], cen["elab"], inv)
    assert r["meta_unidades"] == 24 and r["deficit"] == 0
    with pytest.raises(ErroNegocio, match="já compensado"):
        compensacao.compensar(cen["c"], cen["elab"], inv)


def test_safra_futura_ou_antiga_nao_e_elegivel(cen):
    inv = inventario_aprovado(cen, ano=2025)
    for i, safra in enumerate((2026, 2015)):
        l = emitir(cen, qtd=50, safra=safra, serial=f"S{i}")
        registro.transferir(cen["c"], cen["gger"], lote_id=l, quantidade=50, para_conta=cen["conta_org"])
    r = compensacao.compensar(cen["c"], cen["elab"], inv, simular=True)
    assert r["plano"] == [] and r["deficit"] == r["a_compensar"]
    with pytest.raises(ErroNegocio, match="insuficiente"):
        compensacao.compensar(cen["c"], cen["elab"], inv)


def test_cbe_nao_serve_para_compensacao_voluntaria_e_crve_tem_teto_na_conciliacao(cen):
    inv = inventario_aprovado(cen)
    emitir(cen, qtd=10, tipo="CBE", serial="CBE-1", conta=cen["conta_org"])
    emitir(cen, qtd=50, tipo="CRVE", serial="CRVE-1", conta=cen["conta_org"])
    vol = compensacao.compensar(cen["c"], cen["elab"], inv, simular=True)
    assert {p["tipo_ativo"] for p in vol["plano"]} == {"CRVE"}
    conc = compensacao.compensar(cen["c"], cen["elab"], inv, finalidade="conciliacao_sbce",
                                 limite_crve=0.25, simular=True)
    usado = {p["tipo_ativo"]: p["quantidade"] for p in conc["plano"]}
    assert usado == {"CBE": 10, "CRVE": 6} and conc["deficit"] == 8


# ---------------- API ----------------
def test_api_fluxo_e_controle_de_acesso(tmp_path):
    app = criar_app(str(tmp_path / "api.db"))
    cli = TestClient(app)
    c = conectar(app.state.caminho)
    reg = c.execute("INSERT INTO usuario (nome,papel) VALUES ('R','registrador')").lastrowid
    H = lambda u: {"X-Usuario-Id": str(u)}  # noqa: E731

    assert cli.get("/saude").json() == {"status": "ok"}
    assert cli.post("/organizacoes", json={}, headers=H(999)).status_code == 401
    o1 = cli.post("/organizacoes", headers=H(reg), json={"nome": "Pref", "cnpj": "11111111000191",
                                                         "tipo": "orgao_publico"}).json()
    o2 = cli.post("/organizacoes", headers=H(reg), json={"nome": "Outra", "cnpj": "22222222000191",
                                                         "tipo": "ppp"}).json()
    e1 = cli.post("/usuarios", headers=H(reg), json={"nome": "E", "papel": "elaborador",
                                                     "organizacao_id": o1["id"]}).json()["id"]
    e2 = cli.post("/usuarios", headers=H(reg), json={"nome": "E2", "papel": "elaborador",
                                                     "organizacao_id": o2["id"]}).json()["id"]
    assert cli.post("/usuarios", headers=H(e1), json={"nome": "x", "papel": "registrador"}).status_code == 403

    inv = cli.post("/inventarios", headers=H(e1), json={"organizacao_id": o1["id"], "ano": 2025}).json()["id"]
    r = cli.post(f"/inventarios/{inv}/atividades", headers=H(e1),
                 json={"descricao": "luz", "fator": "energia_sin", "quantidade": 1000})
    assert r.status_code == 201
    assert cli.get(f"/inventarios/{inv}", headers=H(e1)).json()["totais"]["escopo2"] == 38.5
    # IDOR: elaborador de outra organização não lê nem movimenta.
    assert cli.get(f"/inventarios/{inv}", headers=H(e2)).status_code == 403
    assert cli.get(f"/contas/{o1['conta_id']}/saldo", headers=H(e2)).status_code == 403
    enq = cli.post(f"/inventarios/{inv}/enquadramento", headers=H(e1), json={}).json()
    assert enq["faixa_sbce"] == "nao_regulado" and any("preliminar" in a for a in enq["alertas"])
    assert cli.get("/registro/auditoria").json()["integra"] is True
