"""Compra de créditos: credenciamento aberto, catálogo e rito da despesa pública."""
from datetime import date

import pytest
from fastapi.testclient import TestClient

from plataforma import anexos, marketplace as mk, mrv, registro
from plataforma.api import criar_app
from plataforma.banco import ErroNegocio, NaoAutorizado, conectar

PDF = b"%PDF-1.4\n% teste\n%%EOF\n"
HOJE = date(2026, 11, 14)


@pytest.fixture(autouse=True)
def _ambiente(tmp_path, monkeypatch):
    monkeypatch.setenv("CARBONO_ANEXOS", str(tmp_path / "anexos"))
    monkeypatch.setenv("CARBONO_TAXA_MARKETPLACE", "10")


def pdf(c, uid, nome="doc"):
    return anexos.salvar(c, uid, f"{nome}.pdf", "application/pdf", PDF + nome.encode())["id"]


@pytest.fixture
def s(tmp_path):
    c = conectar(str(tmp_path / "m.db"))
    ins = lambda sql, *a: c.execute(sql, a).lastrowid  # noqa: E731
    d = {"c": c}
    d["reg"] = ins("INSERT INTO usuario (nome,papel) VALUES ('R','registrador')")
    d["org"] = ins("INSERT INTO organizacao (nome,cnpj,tipo,esfera) VALUES ('Pref','11111111000191','orgao_publico','municipal')")
    d["vend_org"] = ins("INSERT INTO organizacao (nome,cnpj,tipo) VALUES ('Vendedora Verde','66666666000191','empresa')")
    d["elab"] = ins("INSERT INTO usuario (nome,papel,organizacao_id) VALUES ('E','elaborador',?)", d["org"])
    d["fiscal"] = ins("INSERT INTO usuario (nome,papel,organizacao_id) VALUES ('F','fiscal',?)", d["org"])
    d["vend"] = ins("INSERT INTO usuario (nome,papel,organizacao_id) VALUES ('V','fornecedor',?)", d["vend_org"])
    d["conta_org"] = registro.abrir_conta(c, d["org"])
    return d


def docs(s):
    return {k: pdf(s["c"], s["vend"], k) for k in mk.CRITERIOS}


def credenciar(s):
    cid = mk.solicitar_credenciamento(s["c"], s["vend"], documentos=docs(s), declaracao_dupla_contagem=True)
    mk.analisar_credenciamento(s["c"], s["reg"], cid, criterios={k: True for k in mk.CRITERIOS}, hoje=HOJE)
    return cid


def lote_voluntario(s, qtd=1000, serial="VCS-1"):
    conta = registro.abrir_conta(s["c"], s["vend_org"])
    return registro.emitir(s["c"], s["reg"], conta_id=conta, tipo_ativo="VOLUNTARIO", projeto="Biogás MG",
                           metodologia="ACM0001", certificadora="Verra", certificado_anexo_id=pdf(s["c"], s["reg"]),
                           safra=2025, quantidade=qtd, serial=serial)["lote_id"]


def ofertar(s, qtd=500, preco=40.0, uf="MG", categoria="residuos_biogas", lote=None):
    return mk.publicar_oferta(s["c"], s["vend"], lote_id=lote or lote_voluntario(s), categoria=categoria,
                              municipio="Montes Claros", uf=uf, preco_unitario=preco, quantidade=qtd, hoje=HOJE)


# ---------------------------------------------------------------- credenciamento
def test_credenciamento_exige_documentos_e_declaracao(s):
    c = s["c"]
    with pytest.raises(ErroNegocio, match="Anexe"):
        mk.solicitar_credenciamento(c, s["vend"], documentos={"cnpj": pdf(c, s["vend"])}, declaracao_dupla_contagem=True)
    with pytest.raises(ErroNegocio, match="dupla contagem"):
        mk.solicitar_credenciamento(c, s["vend"], documentos=docs(s), declaracao_dupla_contagem=False)
    with pytest.raises(NaoAutorizado):
        mk.solicitar_credenciamento(c, s["elab"], documentos=docs(s), declaracao_dupla_contagem=True)


def test_analise_objetiva_e_validade(s):
    c = s["c"]
    cid = mk.solicitar_credenciamento(c, s["vend"], documentos=docs(s), declaracao_dupla_contagem=True)
    with pytest.raises(NaoAutorizado):
        mk.analisar_credenciamento(c, s["elab"], cid, criterios={k: True for k in mk.CRITERIOS})
    reprova = {k: True for k in mk.CRITERIOS} | {"fgts": False}
    with pytest.raises(ErroNegocio, match="parecer"):
        mk.analisar_credenciamento(c, s["reg"], cid, criterios=reprova)
    assert mk.analisar_credenciamento(c, s["reg"], cid, criterios=reprova, parecer="FGTS vencido") == "indeferido"
    assert not mk.credenciado(c, s["vend_org"], HOJE)
    credenciar(s)
    assert mk.credenciado(c, s["vend_org"], HOJE)
    assert not mk.credenciado(c, s["vend_org"], date(2027, 11, 15))  # 12 meses


# ---------------------------------------------------------------- ofertas e catálogo
def test_oferta_exige_credenciamento_e_lote_proprio_certificado(s):
    c = s["c"]
    lote = lote_voluntario(s)
    with pytest.raises(ErroNegocio, match="credenciamento"):
        ofertar(s, lote=lote)
    credenciar(s)
    with pytest.raises(ErroNegocio, match="saldo"):
        ofertar(s, qtd=5000, lote=lote)
    cbe = registro.emitir(c, s["reg"], conta_id=registro.abrir_conta(c, s["vend_org"]), tipo_ativo="CBE",
                          projeto="PNA", metodologia="PNA", safra=2026, quantidade=10, serial="CBE-1")["lote_id"]
    with pytest.raises(ErroNegocio, match="CRVE"):
        ofertar(s, lote=cbe)
    ofertar(s, lote=lote)
    with pytest.raises(ErroNegocio, match="ativa"):
        ofertar(s, lote=lote)


def test_catalogo_filtra_e_esconde_credenciamento_vencido(s):
    credenciar(s)
    ofertar(s, uf="MG", categoria="residuos_biogas")
    ofertar(s, uf="PA", categoria="redd", lote=lote_voluntario(s, serial="VCS-2"), preco=30)
    assert len(mk.catalogo(s["c"], hoje=HOJE)) == 2
    assert [o["uf"] for o in mk.catalogo(s["c"], uf="mg", hoje=HOJE)] == ["MG"]
    assert [o["categoria"] for o in mk.catalogo(s["c"], categoria="redd", hoje=HOJE)] == ["redd"]
    assert mk.catalogo(s["c"], hoje=date(2028, 1, 1)) == []


def test_simulacao_mostra_a_taxa(s):
    credenciar(s)
    o = ofertar(s, preco=32.0)
    r = mk.simular(s["c"], o, 120)
    assert r == {"oferta_id": o, "quantidade": 120, "preco_unitario": 32.0, "valor_creditos": 3840.0,
                 "taxa_percentual": 10.0, "valor_taxa": 384.0, "valor_total": 4224.0}


# ---------------------------------------------------------------- rito da despesa pública
def test_fluxo_completo_na_ordem_legal(s):
    c = s["c"]
    credenciar(s)
    o = ofertar(s, qtd=500, preco=32.0)
    pid = mk.criar_pedido(c, s["elab"], oferta_id=o, quantidade=120, hoje=HOJE)
    # não pula etapas
    with pytest.raises(ErroNegocio, match="rascunho"):
        mk.registrar_empenho(c, s["elab"], pid, numero="2026NE000123", data="2026-11-20", valor=4224)
    mk.instruir(c, s["elab"], pid, processo="SEI 1234.01.0000001/2026-00")
    with pytest.raises(ErroNegocio, match="inferior"):
        mk.registrar_empenho(c, s["elab"], pid, numero="2026NE000123", data="2026-11-20", valor=4000)
    with pytest.raises(NaoAutorizado):  # só o órgão registra empenho
        mk.registrar_empenho(c, s["vend"], pid, numero="x", data="2026-11-20", valor=4224)
    with pytest.raises(ErroNegocio):
        mk.entregar(c, s["vend"], pid)  # sem empenho não há entrega
    mk.registrar_empenho(c, s["elab"], pid, numero="2026NE000123", data="2026-11-20", valor=4224)
    assert c.execute("SELECT quantidade_disponivel FROM oferta WHERE id=?", (o,)).fetchone()[0] == 380
    with pytest.raises(NaoAutorizado):
        mk.entregar(c, s["elab"], pid)
    mk.entregar(c, s["vend"], pid)
    assert sum(l["quantidade"] for l in registro.saldo(c, s["conta_org"])) == 120
    with pytest.raises(NaoAutorizado):
        mk.liquidar(c, s["elab"], pid, nota_fiscal="NF 77", conferido=True)
    with pytest.raises(ErroNegocio, match="Confirme"):
        mk.liquidar(c, s["fiscal"], pid, nota_fiscal="NF 77", conferido=False)
    with pytest.raises(ErroNegocio, match="entregue"):
        mk.registrar_pagamento(c, s["elab"], pid, referencia="OB 555", data="2026-12-01")
    mk.liquidar(c, s["fiscal"], pid, nota_fiscal="NF 77", conferido=True)
    mk.registrar_pagamento(c, s["elab"], pid, referencia="2026OB000555", data="2026-12-01")
    p = mk.ver_pedido(c, s["elab"], pid)
    assert p["status"] == "pago" and p["valor_total"] == 4224.0
    with pytest.raises(ErroNegocio):
        mk.cancelar_pedido(c, s["elab"], pid, motivo="desistência")


def test_empenho_reserva_e_cancelamento_devolve(s):
    c = s["c"]
    credenciar(s)
    o = ofertar(s, qtd=100)
    p1 = mk.criar_pedido(c, s["elab"], oferta_id=o, quantidade=80, hoje=HOJE)
    p2 = mk.criar_pedido(c, s["elab"], oferta_id=o, quantidade=50, hoje=HOJE)
    for p in (p1, p2):
        mk.instruir(c, s["elab"], p, processo="SEI 1")
    mk.registrar_empenho(c, s["elab"], p1, numero="NE1", data="2026-11-20", valor=99999)
    with pytest.raises(ErroNegocio, match="não tem mais"):
        mk.registrar_empenho(c, s["elab"], p2, numero="NE2", data="2026-11-20", valor=99999)
    mk.cancelar_pedido(c, s["elab"], p1, motivo="Mudança de prioridade")
    mk.registrar_empenho(c, s["elab"], p2, numero="NE2", data="2026-11-20", valor=99999)
    assert c.execute("SELECT quantidade_disponivel FROM oferta WHERE id=?", (o,)).fetchone()[0] == 50


def test_so_orgao_publico_usa_o_rito(s):
    c = s["c"]
    credenciar(s)
    o = ofertar(s)
    emp = c.execute("INSERT INTO organizacao (nome,cnpj,tipo) VALUES ('PPP','77777777000191','ppp')").lastrowid
    u = c.execute("INSERT INTO usuario (nome,papel,organizacao_id) VALUES ('X','elaborador',?)", (emp,)).lastrowid
    with pytest.raises(ErroNegocio, match="órgãos públicos"):
        mk.criar_pedido(c, u, oferta_id=o, quantidade=1, hoje=HOJE)


def test_minuta_de_tr_e_neutra_e_alerta_controle_juridico(s):
    c = s["c"]
    credenciar(s)
    o = ofertar(s, preco=32.0)
    pid = mk.criar_pedido(c, s["elab"], oferta_id=o, quantidade=120, hoje=HOJE)
    h = mk.minuta_tr_html(c, s["elab"], pid)
    assert "art. 53" in h and "art. 23" in h and "10% = R$ 384,00" in h
    assert "Vendedora Verde" not in h  # não direciona a fornecedor
    assert "não exige aquisição por plataforma" in h


def test_carteira_mostra_saldo_a_compensar(s):
    c = s["c"]
    inv = mrv.criar_inventario(c, s["elab"], s["org"], 2025)
    mrv.adicionar_atividade(c, s["elab"], inv, descricao="glp", fator="glp", quantidade=40_000,
                            anexo_id=pdf(c, s["elab"]))
    oia = c.execute("INSERT INTO organizacao (nome,cnpj,tipo) VALUES ('OIA','44444444000191','empresa')").lastrowid
    v = c.execute("INSERT INTO usuario (nome,papel,organizacao_id,acreditado) VALUES ('V','verificador',?,1)", (oia,)).lastrowid
    m = c.execute("INSERT INTO usuario (nome,papel) VALUES ('M','moderador')").lastrowid
    for uid, a in ((s["elab"], "submeter"), (v, "assumir"), (v, "verificar"), (m, "aprovar")):
        mrv.transitar(c, uid, inv, a)
    k = mk.carteira(c, s["org"])
    assert k["inventario_ano"] == 2025 and k["saldo_a_compensar"] == 117 and k["a_comprar"] == 117


# ---------------------------------------------------------------- API
def test_api_do_pedido(tmp_path):
    app = criar_app(str(tmp_path / "api.db"))
    cli = TestClient(app)
    c = conectar(app.state.caminho)
    H = lambda u: {"X-Usuario-Id": str(u)}  # noqa: E731
    reg = c.execute("INSERT INTO usuario (nome,papel) VALUES ('R','registrador')").lastrowid
    org = cli.post("/organizacoes", headers=H(reg), json={"nome": "Pref", "cnpj": "11111111000191",
                                                          "tipo": "orgao_publico"}).json()
    vend_org = cli.post("/organizacoes", headers=H(reg), json={"nome": "Vend", "cnpj": "66666666000191",
                                                               "tipo": "empresa"}).json()
    elab = cli.post("/usuarios", headers=H(reg), json={"nome": "E", "papel": "elaborador", "organizacao_id": org["id"]}).json()["id"]
    vend = cli.post("/usuarios", headers=H(reg), json={"nome": "V", "papel": "fornecedor", "organizacao_id": vend_org["id"]}).json()["id"]
    regras = cli.get("/marketplace/regras").json()
    docs_ = {k: cli.post("/anexos", headers=H(vend), files={"arquivo": (f"{k}.pdf", PDF + k.encode(), "application/pdf")}).json()["id"]
             for k in regras["criterios"]}
    cid = cli.post("/credenciamentos", headers=H(vend), json={"documentos": docs_, "declaracao_dupla_contagem": True}).json()["id"]
    assert cli.post(f"/credenciamentos/{cid}/analise", headers=H(reg),
                    json={"criterios": {k: True for k in regras["criterios"]}}).json()["status"] == "aprovado"
    cert = cli.post("/anexos", headers=H(reg), files={"arquivo": ("c.pdf", PDF + b"c", "application/pdf")}).json()["id"]
    lote = cli.post("/registro/emissoes", headers=H(reg), json={
        "conta_id": vend_org["conta_id"], "tipo_ativo": "VOLUNTARIO", "projeto": "P", "metodologia": "M",
        "safra": 2025, "quantidade": 100, "serial": "VCS-9", "certificadora": "Verra",
        "certificado_anexo_id": cert}).json()["lote_id"]
    oferta = cli.post("/ofertas", headers=H(vend), json={"lote_id": lote, "categoria": "energia", "municipio": "Janaúba",
                                                         "uf": "MG", "preco_unitario": 35, "quantidade": 100}).json()["id"]
    assert cli.get("/ofertas", headers=H(elab), params={"uf": "MG"}).json()[0]["id"] == oferta
    pid = cli.post("/pedidos", headers=H(elab), json={"oferta_id": oferta, "quantidade": 10}).json()["id"]
    assert "Termo de Referência" in cli.get(f"/pedidos/{pid}/minuta-tr.html", headers=H(elab)).text
    assert cli.post(f"/pedidos/{pid}/entrega", headers=H(vend)).status_code == 409
    assert cli.get(f"/pedidos/{pid}", headers=H(vend)).json()["status"] == "rascunho"
    assert cli.get("/pedidos", headers=H(vend)).json() == []  # rascunho não aparece ao vendedor
