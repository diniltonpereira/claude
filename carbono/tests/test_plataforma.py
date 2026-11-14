import sqlite3

import pytest
from fastapi.testclient import TestClient

from plataforma import compensacao, mrv, registro
from plataforma.api import criar_app
from plataforma.banco import ErroNegocio, NaoAutorizado, conectar
from plataforma.calculo import calcular_por_fator, calcular_por_gas
from datetime import date

from plataforma.enquadramento import (checar_projeto_publico, checar_reparticao_comunidade,
                                     enquadrar, faixa_sbce, fase_em)


@pytest.fixture
def cen(tmp_path):
    """Cenário mínimo: órgão público, organismo acreditado, moderador, gerador de créditos."""
    c = conectar(str(tmp_path / "t.db"))
    ins = lambda sql, *a: c.execute(sql, a).lastrowid  # noqa: E731
    s = {"c": c}
    s["reg"] = ins("INSERT INTO usuario (nome,papel) VALUES ('R','registrador')")
    s["org"] = ins("INSERT INTO organizacao (nome,cnpj,tipo) VALUES ('Pref','11111111000191','orgao_publico')")
    s["ovv"] = ins("INSERT INTO organizacao (nome,cnpj,tipo) VALUES ('OVV','44444444000191','empresa')")
    s["ger"] = ins("INSERT INTO organizacao (nome,cnpj,tipo) VALUES ('Flor','33333333000191','concessao_florestal')")
    s["elab"] = ins("INSERT INTO usuario (nome,papel,organizacao_id) VALUES ('E','elaborador',?)", s["org"])
    s["verif"] = ins("INSERT INTO usuario (nome,papel,organizacao_id,acreditado) VALUES ('V','verificador',?,1)", s["ovv"])
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
    interno = c.execute("INSERT INTO usuario (nome,papel,organizacao_id,acreditado) "
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
    registro.cancelar(cen["c"], cen["gger"], lote_id=lote, quantidade=100,
                      finalidade="compensacao_voluntaria", beneficiario="X")
    with pytest.raises(ErroNegocio, match="cancelado"):
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


def test_compensacao_voluntaria_aceita_ativos_do_sbce_art45(cen):
    inv = inventario_aprovado(cen)  # 24 unidades
    emitir(cen, qtd=10, tipo="CRVE", serial="CRVE-1", conta=cen["conta_org"])
    emitir(cen, qtd=50, tipo="CBE", safra=2025, serial="CBE-1", conta=cen["conta_org"])
    r = compensacao.compensar(cen["c"], cen["elab"], inv, simular=True)
    assert [(p["tipo_ativo"], p["quantidade"]) for p in r["plano"]] == [("CRVE", 10), ("CBE", 14)]
    so_crve = compensacao.compensar(cen["c"], cen["elab"], inv, tipos_aceitos=("CRVE",), simular=True)
    assert so_crve["deficit"] == 14  # política do ente pode ser mais restritiva que a lei


def aprovar(s, inv):
    for u, a in [("elab", "submeter"), ("verif", "assumir"), ("verif", "verificar"), ("mod", "aprovar")]:
        mrv.transitar(s["c"], s[u], inv, a)


def test_conciliacao_limite_de_crve_e_periodo_da_cbe(cen):
    c = cen["c"]
    inv = mrv.criar_inventario(c, cen["elab"], cen["org"], 2025)
    mrv.adicionar_atividade(c, cen["elab"], inv, descricao="caldeira", gas="CO2", quantidade=30_000)
    aprovar(cen, inv)
    emitir(cen, qtd=10_000, tipo="CBE", safra=2025, serial="CBE-25", conta=cen["conta_org"])
    emitir(cen, qtd=10_000, tipo="CBE", safra=2024, serial="CBE-24", conta=cen["conta_org"])
    emitir(cen, qtd=50_000, tipo="CRVE", serial="CRVE-1", conta=cen["conta_org"])
    r = compensacao.compensar(c, cen["elab"], inv, finalidade="conciliacao_sbce",
                              limite_crve=0.25, simular=True)
    usado = {p["serial"]: p["quantidade"] for p in r["plano"]}
    assert usado == {"CBE-25": 10_000, "CRVE-1": 7_500} and r["deficit"] == 12_500
    assert any("art. 11, § 2º" in m for m in r["lotes_ignorados"])
    # Com autorização do PNA para usar CBE de outro período (art. 11, § 2º, II):
    r2 = compensacao.compensar(c, cen["elab"], inv, finalidade="conciliacao_sbce",
                               limite_crve=0.25, cbe_outros_periodos=True, simular=True)
    assert r2["deficit"] == 2_500


def test_abaixo_do_patamar_nao_ha_conciliacao(cen):
    inv = inventario_aprovado(cen)  # 23 tCO2e
    with pytest.raises(ErroNegocio, match="art. 30, II"):
        compensacao.compensar(cen["c"], cen["elab"], inv, finalidade="conciliacao_sbce", simular=True)


def test_conciliacao_recusa_credito_voluntario(cen):
    inv = inventario_aprovado(cen)
    with pytest.raises(ErroNegocio, match="art. 10"):
        compensacao.compensar(cen["c"], cen["elab"], inv, finalidade="conciliacao_sbce",
                              tipos_aceitos=("VOLUNTARIO",), simular=True)


def test_conciliacao_usa_emissoes_liquidas(cen):
    c = cen["c"]
    inv = mrv.criar_inventario(c, cen["elab"], cen["org"], 2025)
    mrv.adicionar_atividade(c, cen["elab"], inv, descricao="caldeira", gas="CO2", quantidade=30_000)
    mrv.adicionar_atividade(c, cen["elab"], inv, descricao="reflorestamento", gas="CO2",
                            quantidade=5, remocao=True)
    with pytest.raises(ErroNegocio, match="gas='CO2'"):
        mrv.adicionar_atividade(c, cen["elab"], inv, descricao="x", fator="glp", quantidade=1, remocao=True)
    for u, a in [("elab", "submeter"), ("verif", "assumir"), ("verif", "verificar"), ("mod", "aprovar")]:
        mrv.transitar(c, cen[u], inv, a)
    t = mrv.totais(c, inv)
    assert t["remocoes"] == 5 and t["escopo1_liquido"] == pytest.approx(t["escopo1"] - 5)
    r = compensacao.compensar(c, cen["elab"], inv, finalidade="conciliacao_sbce", simular=True)
    assert r["meta_unidades"] == 29_995


def test_verificador_precisa_ser_acreditado(cen):
    c = cen["c"]
    nao = c.execute("INSERT INTO usuario (nome,papel,organizacao_id) VALUES ('N','verificador',?)",
                    (cen["ovv"],)).lastrowid
    inv = mrv.criar_inventario(c, cen["elab"], cen["org"], 2025)
    mrv.adicionar_atividade(c, cen["elab"], inv, descricao="x", fator="glp", quantidade=1)
    mrv.transitar(c, cen["elab"], inv, "submeter")
    with pytest.raises(NaoAutorizado, match="art. 32"):
        mrv.transitar(c, nao, inv, "assumir")


# ---------------- Enquadramento por instalação (art. 30) ----------------
def operador_com_unidades(s, unidades):
    """unidades: lista de (nome, tCO2e, kwargs). Retorna (inventário aprovado, ids)."""
    c = s["c"]
    inv = mrv.criar_inventario(c, s["elab"], s["org"], 2025)
    ids = {}
    for nome, t, kw in unidades:
        ids[nome] = mrv.criar_instalacao(c, s["elab"], s["org"], nome=nome, **kw)
        mrv.adicionar_atividade(c, s["elab"], inv, descricao=nome, gas="CO2", quantidade=t,
                                instalacao_id=ids[nome])
    aprovar(s, inv)
    return inv, ids


def test_patamar_e_por_instalacao_e_nao_pelo_total(cen):
    inv, _ = operador_com_unidades(cen, [("ETE A", 8_000, {}), ("ETE B", 8_000, {}), ("ETE C", 8_000, {})])
    from plataforma.enquadramento import enquadrar_operador
    r = enquadrar_operador("concessao", mrv.totais_por_instalacao(cen["c"], inv))
    assert r["faixa_sbce"] == "nao_regulado" and r["escopo1_total_operador"] == 24_000
    assert any("nenhuma instalação ou fonte isolada" in a for a in r["alertas"])


def test_conciliacao_so_considera_instalacoes_acima_de_25_mil(cen):
    inv, ids = operador_com_unidades(cen, [
        ("Aterro", 32_000, {}), ("Frota", 12_000, {"natureza": "fonte_movel"}), ("ETE", 8_000, {})])
    from plataforma.enquadramento import enquadrar_operador
    r = enquadrar_operador("concessao", mrv.totais_por_instalacao(cen["c"], inv))
    faixas = {i["nome"]: i["faixa_sbce"] for i in r["instalacoes"]}
    assert faixas == {"Aterro": "regulado_conciliacao", "Frota": "regulado_monitoramento",
                      "ETE": "nao_regulado"}
    assert r["faixa_sbce"] == "regulado_conciliacao" and r["base_conciliacao_tco2e"] == 32_000
    plano = compensacao.compensar(cen["c"], cen["elab"], inv, finalidade="conciliacao_sbce", simular=True)
    assert plano["meta_unidades"] == 32_000
    assert [i["nome"] for i in plano["instalacoes_consideradas"]] == ["Aterro"]


def test_unidade_excluida_nao_contamina_o_operador(cen):
    inv, _ = operador_com_unidades(cen, [("Aterro com captação", 90_000, {"residuos_neutralizacao": True}),
                                         ("Sede", 500, {})])
    from plataforma.enquadramento import enquadrar_operador
    r = enquadrar_operador("concessao", mrv.totais_por_instalacao(cen["c"], inv))
    assert r["faixa_sbce"] == "nao_regulado" and r["emissoes_escopo1_tco2e"] == 500
    assert any("art. 30, § 3º" in a for a in r["alertas"])


def test_atividade_sem_instalacao_gera_alerta(cen):
    c = cen["c"]
    inv, _ = operador_com_unidades(cen, [("Aterro", 30_000, {})])
    c.execute("UPDATE inventario SET status='rascunho' WHERE id=?", (inv,))
    mrv.adicionar_atividade(c, cen["elab"], inv, descricao="solta", gas="CO2", quantidade=100)
    from plataforma.enquadramento import enquadrar_operador
    r = enquadrar_operador("concessao", mrv.totais_por_instalacao(c, inv))
    assert any("sem instalação vinculada" in a for a in r["alertas"])


def test_instalacao_de_outra_organizacao_e_recusada(cen):
    c = cen["c"]
    alheia = mrv.criar_instalacao(c, cen["gger"], cen["ger"], nome="Base florestal")
    inv = mrv.criar_inventario(c, cen["elab"], cen["org"], 2025)
    with pytest.raises(ErroNegocio, match="outra organização"):
        mrv.adicionar_atividade(c, cen["elab"], inv, descricao="x", fator="glp", quantidade=1,
                                instalacao_id=alheia)
    with pytest.raises(NaoAutorizado):
        mrv.criar_instalacao(c, cen["elab"], cen["ger"], nome="Invasão")


# ---------------- Lei 15.042/2024: fases e checagens ----------------
@pytest.mark.parametrize("data,prorrogada,fase", [
    (date(2025, 6, 1), True, "I"), (date(2026, 12, 11), True, "I"), (date(2026, 12, 12), True, "II"),
    (date(2028, 1, 1), True, "III"), (date(2029, 12, 12), True, "IV"),
    (date(2026, 1, 1), False, "II")])
def test_fases_art50(data, prorrogada, fase):
    assert fase_em(data, prorrogada).numero == fase


def test_obrigacoes_so_exigiveis_a_partir_da_fase_certa():
    e = enquadrar("concessao", 30_000, data_referencia=date(2026, 6, 1))
    assert not any(o.exigivel for o in e.obrigacoes if o.titulo.startswith(("Plano", "Relato", "Conciliação")))
    e4 = enquadrar("concessao", 30_000, data_referencia=date(2030, 1, 1))
    assert all(o.exigivel for o in e4.obrigacoes)


def test_residuos_com_neutralizacao_ficam_fora_art30_p3():
    assert enquadrar("concessao", 90_000, residuos_com_neutralizacao=True).faixa_sbce == "nao_regulado"


def test_patamares_so_podem_ser_majorados_art30_p1():
    with pytest.raises(ValueError, match="majorados"):
        enquadrar("empresa", 1, limiar_monitoramento=5_000)
    assert enquadrar("empresa", 12_000, limiar_monitoramento=15_000).faixa_sbce == "nao_regulado"


def test_ente_subnacional_nao_tributa_emissoes_art22():
    titulos = [o.titulo for o in enquadrar("orgao_publico", 0, esfera="municipal").obrigacoes]
    assert "Vedação de tributo sobre emissões reguladas" in titulos


def test_municipio_nao_faz_programa_jurisdicional():
    p = checar_projeto_publico(esfera="municipal", proprietario_e_usufrutuario=True,
                               sobreposicao_terceiros=False, parceria_privada=False, licitacao=False,
                               anuencia_gestor_area=True, programa_jurisdicional=True, venda_antecipada=True)
    assert len(p) == 2


def test_projeto_publico_com_parceiro_exige_licitacao_e_anuencia():
    p = checar_projeto_publico(esfera="estadual", proprietario_e_usufrutuario=True,
                               sobreposicao_terceiros=False, parceria_privada=True, licitacao=False,
                               anuencia_gestor_area=False)
    assert any("licitação" in x for x in p) and any("art. 49" in x for x in p)


@pytest.mark.parametrize("tipo,pct,ok", [("remocao", 50, True), ("remocao", 49.9, False),
                                         ("redd_mercado", 70, True), ("redd_mercado", 60, False)])
def test_reparticao_minima_comunidades_art47(tipo, pct, ok):
    assert (checar_reparticao_comunidade(tipo, pct) == []) is ok


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
    inst = cli.post(f"/organizacoes/{o1['id']}/instalacoes", headers=H(e1), json={"nome": "Garagem"})
    assert inst.status_code == 201
    assert cli.get(f"/organizacoes/{o1['id']}/instalacoes", headers=H(e2)).status_code == 403
    assert cli.post(f"/inventarios/{inv}/atividades", headers=H(e1), json={
        "descricao": "frota", "fator": "diesel_b", "quantidade": 100,
        "instalacao_id": inst.json()["id"]}).status_code == 201
    # IDOR: elaborador de outra organização não lê nem movimenta.
    assert cli.get(f"/inventarios/{inv}", headers=H(e2)).status_code == 403
    assert cli.get(f"/contas/{o1['conta_id']}/saldo", headers=H(e2)).status_code == 403
    assert cli.get("/sbce/calendario").json()["fases"][0]["numero"] == "I"
    chk = cli.post("/checagens/reparticao-comunidade", json={"tipo_projeto": "redd_mercado",
                                                             "percentual_comunidade": 60}).json()
    assert chk["apto"] is False
    enq = cli.post(f"/inventarios/{inv}/enquadramento", headers=H(e1), json={}).json()
    assert enq["faixa_sbce"] == "nao_regulado" and any("preliminar" in a for a in enq["alertas"])
    assert cli.get("/registro/auditoria").json()["integra"] is True
    # Painel web servido pela própria aplicação; listagens respeitam o papel.
    assert "Carbono Público" in cli.get("/painel/").text
    assert cli.get("/", follow_redirects=False).headers["location"] == "/painel/"
    assert cli.get("/eu", headers=H(e1)).json()["organizacao"]["id"] == o1["id"]
    assert [i["id"] for i in cli.get("/inventarios", headers=H(e2)).json()] == []
    assert cli.get("/usuarios", headers=H(e1)).status_code == 403
