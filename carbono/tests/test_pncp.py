"""PNCP (APIs públicas de consulta): vínculo do pedido e pesquisa de preços com contratações similares.

O PNCP simulado reproduz o formato das respostas reais (campos conferidos em pncp.gov.br/api)."""
import io
from datetime import date, timedelta

import httpx
import pytest
from docx import Document
from fastapi.testclient import TestClient

from plataforma import modelos, pncp
from plataforma.api import criar_app
from plataforma.banco import ErroNegocio, NaoAutorizado

from .test_modelos import cenario  # noqa: F401 — cenário da demonstração


def compra(cnpj, ano, seq, objeto="Aquisição de créditos de carbono para compensação", **extra):
    return {"numeroControlePNCP": f"{cnpj}-1-{seq:06d}/{ano}", "orgaoEntidade": {"cnpj": cnpj, "razaoSocial": f"Órgão {cnpj[:4]}"},
            "unidadeOrgao": {"nomeUnidade": "Secretaria de Meio Ambiente", "municipioNome": "Belo Horizonte", "ufSigla": "MG"},
            "objetoCompra": objeto, "modalidadeNome": "Dispensa", "situacaoCompraNome": "Divulgada no PNCP",
            "processo": "1/2026", "valorTotalEstimado": 50000.0, "valorTotalHomologado": 42000.0,
            "dataPublicacaoPncp": f"{date.today():%Y-%m-%d}T10:00:00", **extra}


class PncpFalso:
    def __init__(self):
        hoje = f"{date.today():%Y-%m-%d}"
        self.compras = {("11111111000191", 2026, 12): compra("11111111000191", 2026, 12),
                        ("22222222000191", 2026, 7): compra("22222222000191", 2026, 7)}
        self.itens = {("22222222000191", 2026, 7): [
            {"numeroItem": 1, "descricao": "Crédito de carbono certificado (1 tCO2e), com aposentadoria",
             "unidadeMedida": "tonelada", "quantidade": 500.0, "valorUnitarioEstimado": 95.0,
             "orcamentoSigiloso": False, "temResultado": True},
            {"numeroItem": 2, "descricao": "Lote de 10 créditos", "unidadeMedida": "lote", "quantidade": 5.0,
             "valorUnitarioEstimado": 900.0, "orcamentoSigiloso": False, "temResultado": False},
            {"numeroItem": 3, "descricao": "Item sigiloso", "unidadeMedida": "unidade", "quantidade": 1.0,
             "valorUnitarioEstimado": 1.0, "orcamentoSigiloso": True, "temResultado": False}]
            + [{"numeroItem": n, "descricao": f"Item {n}", "unidadeMedida": "unidade", "quantidade": 1.0,
                "valorUnitarioEstimado": 1.0, "orcamentoSigiloso": False, "temResultado": False} for n in range(4, 56)]}
        self.resultados = {1: [{"valorUnitarioHomologado": 82.5, "dataResultado": hoje,
                                "nomeRazaoSocialFornecedor": "Carbono Verde Ltda."}]}
        self.fora_do_ar = False
        self.cliente = httpx.Client(transport=httpx.MockTransport(self._responder))

    def _responder(self, req: httpx.Request) -> httpx.Response:
        if self.fora_do_ar:
            raise httpx.ConnectError("sem rota")
        partes = req.url.path.split("/")
        i = partes.index("orgaos")
        chave = (partes[i + 1], int(partes[i + 3]), int(partes[i + 4]))
        if "/consulta/v1/" in req.url.path:
            return httpx.Response(200, json=self.compras[chave]) if chave in self.compras else httpx.Response(404)
        if req.url.path.endswith("/resultados"):
            return httpx.Response(200, json=self.resultados.get(int(partes[-2]), []))
        if req.url.path.endswith("/itens"):
            pag, tam = int(req.url.params["pagina"]), int(req.url.params["tamanhoPagina"])
            lote = self.itens.get(chave, [])[(pag - 1) * tam: pag * tam]
            return httpx.Response(200, json=lote) if lote else httpx.Response(204)
        return httpx.Response(404)


@pytest.fixture
def falso(monkeypatch):
    f = PncpFalso()
    monkeypatch.setattr(pncp, "_cliente", lambda cliente=None: cliente or f.cliente)
    return f


def _pedido(cenario):
    c = cenario["c"]
    ped = cenario["ped"]
    c.execute("UPDATE organizacao SET cnpj='11111111000191' WHERE id=?", (ped["orgao_id"],))
    return ped["id"]


# ---------------------------------------------------------------- identificação
def test_aceita_numero_de_controle_ou_link_do_portal():
    assert pncp.interpretar("11111111000191-1-000012/2026") == ("11111111000191", 2026, 12)
    assert pncp.interpretar(" 11.111.111000191-1-12/2026 ") == ("11111111000191", 2026, 12)
    assert pncp.interpretar("https://pncp.gov.br/app/editais/11111111000191/2026/12") == ("11111111000191", 2026, 12)
    with pytest.raises(ErroNegocio, match="nº de controle"):
        pncp.interpretar("processo 123")


# ---------------------------------------------------------------- vínculo do pedido
def test_vincula_o_pedido_a_contratacao_do_proprio_orgao(cenario, falso):
    c, pid = cenario["c"], _pedido(cenario)
    c.execute("UPDATE pedido SET status='empenhado', empenho_numero='2026NE1' WHERE id=?", (pid,))
    assert "art. 94" in modelos_pedido(cenario)["alerta_pncp"]
    r = pncp.vincular_pedido(c, cenario["elab"], pid, "https://pncp.gov.br/app/editais/11111111000191/2026/12")
    assert r["situacao"] == "Divulgada no PNCP" and r["link"].endswith("/11111111000191/2026/12")
    p = modelos_pedido(cenario)
    assert p["pncp"]["numeroControlePNCP"] == "11111111000191-1-000012/2026" and p["alerta_pncp"] is None
    assert c.execute("SELECT COUNT(*) FROM auditoria WHERE acao='vincular_pncp'").fetchone()[0] == 1
    with pytest.raises(ErroNegocio, match="outro órgão"):
        pncp.vincular_pedido(c, cenario["elab"], pid, "22222222000191-1-000007/2026")
    with pytest.raises(ErroNegocio, match="não encontrada"):
        pncp.vincular_pedido(c, cenario["elab"], pid, "11111111000191-1-000099/2026")
    falso.fora_do_ar = True
    with pytest.raises(ErroNegocio, match="não respondeu"):
        pncp.vincular_pedido(c, cenario["elab"], pid, "11111111000191-1-000012/2026")


def modelos_pedido(cenario):
    from plataforma.marketplace import ver_pedido
    return ver_pedido(cenario["c"], cenario["elab"], cenario["ped"]["id"])


# ---------------------------------------------------------------- pesquisa de preços
def test_lista_itens_com_paginacao_homologado_e_sigilo(falso):
    r = pncp.itens_da_compra("22222222000191-1-000007/2026")
    assert len(r["itens"]) == 55                                    # duas páginas de 50
    um, _, sigiloso = r["itens"][:3]
    assert um["homologado"]["valor"] == 82.5 and um["homologado"]["fornecedor"] == "Carbono Verde Ltda."
    assert sigiloso["valor_estimado"] is None


def test_fonte_usa_homologado_converte_unidade_e_vence_em_um_ano(cenario, falso):
    c, elab = cenario["c"], cenario["elab"]
    f1 = pncp.registrar_fonte(c, elab, numero="22222222000191-1-000007/2026", numero_item=1)
    f2 = pncp.registrar_fonte(c, elab, numero="22222222000191-1-000007/2026", numero_item=2, tco2e_por_unidade=10)
    org = c.execute("SELECT organizacao_id FROM usuario WHERE id=?", (elab,)).fetchone()[0]
    fs = {f["id"]: f for f in pncp.fontes(c, org)}
    assert (fs[f1]["tipo_valor"], fs[f1]["preco_por_tco2e"]) == ("homologado", 82.5)
    assert (fs[f2]["tipo_valor"], fs[f2]["preco_por_tco2e"]) == ("estimado", 90.0)       # R$ 900 / 10 tCO2e
    with pytest.raises(ErroNegocio, match="já está"):
        pncp.registrar_fonte(c, elab, numero="22222222000191-1-000007/2026", numero_item=1)
    with pytest.raises(ErroNegocio, match="sem resultado"):
        pncp.registrar_fonte(c, elab, numero="22222222000191-1-000007/2026", numero_item=3)
    verificador = c.execute("SELECT id FROM usuario WHERE papel='verificador' LIMIT 1").fetchone()[0]
    with pytest.raises(NaoAutorizado):
        pncp.registrar_fonte(c, verificador, numero="22222222000191-1-000007/2026", numero_item=1)
    pncp.descartar_fonte(c, elab, f2)
    velha = (date.today() - timedelta(days=400)).isoformat()
    c.execute("UPDATE pesquisa_preco SET data_referencia=? WHERE id=?", (velha, f1))
    assert pncp.fontes(c, org) == [] and len(pncp.fontes(c, org, vigentes=False)) == 2


def test_etp_e_tr_citam_as_contratacoes_similares(cenario, falso):
    c, elab = cenario["c"], cenario["elab"]
    org = c.execute("SELECT organizacao_id FROM usuario WHERE id=?", (elab,)).fetchone()[0]
    inv = c.execute("SELECT id FROM inventario WHERE organizacao_id=? AND status='aprovado'", (org,)).fetchone()[0]
    pncp.registrar_fonte(c, elab, numero="22222222000191-1-000007/2026", numero_item=1)
    etp = "\n".join(x.text for x in Document(io.BytesIO(modelos.etp_compensacao(c, inv))).paragraphs)
    tabelas = Document(io.BytesIO(modelos.etp_compensacao(c, inv))).tables
    assert "Contratações similares divulgadas no PNCP" in etp and "Mediana de todas as fontes vigentes" in etp
    assert any("22222222000191-1-000007/2026#item1" in cel.text for t in tabelas for r in t.rows for cel in r.cells)
    tr = Document(io.BytesIO(modelos.tr_pedido(c, elab, cenario["ped"]["id"])))
    assert any("R$ 82,50" in cel.text for t in tr.tables for r in t.rows for cel in r.cells)


def test_rotas(cenario, falso):
    cli = TestClient(criar_app(cenario["caminho"]))
    c, elab, pid = cenario["c"], cenario["elab"], _pedido(cenario)
    H = {"X-Usuario-Id": str(elab)}
    assert len(cli.get("/pncp/itens", params={"numero": "22222222000191-1-000007/2026"}, headers=H).json()["itens"]) == 55
    r = cli.post("/pesquisa-precos", headers=H, json={"numero": "22222222000191-1-000007/2026", "numero_item": 1})
    assert r.status_code == 201
    assert cli.get("/pesquisa-precos", headers=H).json()[0]["preco_por_tco2e"] == 82.5
    v = cli.post(f"/pedidos/{pid}/pncp", headers=H, json={"numero": "11111111000191-1-000012/2026"})
    assert v.status_code == 200 and cli.get(f"/pedidos/{pid}", headers=H).json()["pncp"]["modalidade"] == "Dispensa"
    outro = c.execute("SELECT id FROM usuario WHERE papel='verificador' LIMIT 1").fetchone()[0]
    assert cli.get("/pncp/itens", params={"numero": "x" * 12}, headers={"X-Usuario-Id": str(outro)}).status_code == 403
