"""Conferência documental de créditos, credenciamento de verificadores e pacote de verificação."""
import hashlib
import io
import zipfile
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from plataforma import anexos, creditos, mrv, registro, verificacao
from plataforma.api import criar_app
from plataforma.banco import ErroNegocio, NaoAutorizado

from .test_modelos import cenario  # noqa: F401 — cenário da demonstração

PDF = b"%PDF-1.4\n% teste\n%%EOF\n"


def pdf(c, uid, nome):
    return anexos.salvar(c, uid, f"{nome}.pdf", "application/pdf", PDF + nome.encode())["id"]


def usuario(c, papel):
    return c.execute("SELECT * FROM usuario WHERE papel=? ORDER BY id LIMIT 1", (papel,)).fetchone()


# ---------------------------------------------------------------- séries
def test_le_series_da_verra_e_da_gold_standard():
    _, b = creditos.serie_verra(397034047, 500, 2017, 1529, lote=7360)
    assert b == "7360-397034047-397034546-VCS-VCU-259-VER-BR-1-1529-01012017-31122017-0"
    assert creditos.interpretar_serie("Verra", b) == {"projeto_ref": "1529", "safra": 2017, "inicio": 397034047,
                                                      "fim": 397034546}
    assert creditos.interpretar_serie("Gold Standard", "GS1-1-BR-GS2446-12-2021-6339-10-109") == {
        "projeto_ref": "GS2446", "safra": 2021, "inicio": 10, "fim": 109}
    assert creditos.interpretar_serie("Verra", "formato-desconhecido-123") is None


def test_escrituracao_confere_link_quantidade_safra_e_barra_serie_repetida(cenario):
    c = cenario["c"]
    reg = usuario(c, "registrador")["id"]
    conta = c.execute("SELECT id FROM conta ORDER BY id LIMIT 1").fetchone()[0]
    cert = pdf(c, reg, "certificado")

    def emitir(serial, qtd=100, safra=2025, url=None, bloco=None, inicio=1, projeto=4242):
        u, b = creditos.serie_verra(inicio, qtd, safra, projeto)
        return registro.emitir(c, reg, conta_id=conta, tipo_ativo="VOLUNTARIO", projeto="P", metodologia="M",
                               safra=safra, quantidade=qtd, serial=serial, certificadora="Verra",
                               certificado_anexo_id=cert, registro_url=url or u, serie_bloco=bloco or b)

    with pytest.raises(ErroNegocio, match="link https"):
        emitir("A", url="http://registry.verra.org/x")
    with pytest.raises(ErroNegocio, match="link https"):
        emitir("A", url="https://verra.org.golpe.com/x")
    with pytest.raises(ErroNegocio, match="tem 50 unidades"):
        emitir("A", bloco=creditos.serie_verra(1, 50, 2025, 4242)[1])
    with pytest.raises(ErroNegocio, match="safra 2024"):
        emitir("A", bloco=creditos.serie_verra(1, 100, 2024, 4242)[1])
    lote = emitir("A")["lote_id"]
    with pytest.raises(ErroNegocio, match=f"lote nº {lote}"):
        emitir("B", inicio=51)                                  # sobreposição parcial, mesmo projeto
    assert emitir("C", inicio=51, projeto=4343)["lote_id"]      # mesmo intervalo, outro projeto: ok
    assert emitir("D", inicio=101)["lote_id"]                   # intervalo seguinte: ok
    assert emitir("E", bloco="Bloco em formato livre 123")["lote_id"]   # formato não reconhecido: aceito
    with pytest.raises(ErroNegocio, match="já foi escriturado"):          # mas não duas vezes
        emitir("F", bloco="Bloco em formato livre 123")
    # o lote transferido aponta para o bloco de origem
    org2 = c.execute("SELECT id FROM conta WHERE id <> ? ORDER BY id LIMIT 1", (conta,)).fetchone()[0]
    filho = registro.transferir(c, reg, lote_id=lote, quantidade=10, para_conta=org2)["lote_id"]
    assert creditos.bloco_do_lote(c, filho)["lote_id"] == lote


# ---------------------------------------------------------------- verificadores
def test_credenciamento_de_verificador_com_validade(cenario):
    c = cenario["c"]
    verif, reg = usuario(c, "verificador"), usuario(c, "registrador")
    assert creditos.verificador_habilitado(c, verif)            # cadastro manual antigo continua valendo
    doc = pdf(c, verif["id"], "acreditacao")
    with pytest.raises(ErroNegocio, match="vencida"):
        creditos.solicitar_acreditacao(c, verif["id"], acreditador="Inmetro/Cgcre", numero="OVV 0001",
                                       normas="ABNT NBR ISO 14065", valido_ate="2020-01-01", documento_anexo_id=doc)
    aid = creditos.solicitar_acreditacao(c, verif["id"], acreditador="Inmetro/Cgcre", numero="OVV 0001",
                                         normas="ABNT NBR ISO 14065 e ISO 14064-3",
                                         valido_ate=(date.today() + timedelta(days=30)).isoformat(),
                                         documento_anexo_id=doc)
    assert not creditos.verificador_habilitado(c, verif)        # com pedido registrado, só vale aprovado
    with pytest.raises(NaoAutorizado):
        creditos.analisar_acreditacao(c, verif["id"], aid, aprovar=True, parecer="ok ok")
    creditos.analisar_acreditacao(c, reg["id"], aid, aprovar=True, parecer="Conferido na consulta pública do Inmetro")
    assert creditos.verificador_habilitado(c, verif)
    assert not creditos.verificador_habilitado(c, verif, hoje=date.today() + timedelta(days=31))   # vencida


def test_verificador_sem_acreditacao_vigente_nao_assume(cenario):
    c = cenario["c"]
    verif = usuario(c, "verificador")
    elab = cenario["elab"]
    org = c.execute("SELECT organizacao_id FROM usuario WHERE id=?", (elab,)).fetchone()[0]
    inv = mrv.criar_inventario(c, elab, org, 2099)
    mrv.adicionar_atividade(c, elab, inv, descricao="glp", fator="glp", quantidade=100, anexo_id=pdf(c, elab, "nf"))
    mrv.transitar(c, elab, inv, "submeter")
    creditos.solicitar_acreditacao(c, verif["id"], acreditador="Inmetro/Cgcre", numero="OVV 2",
                                   normas="ISO 14065", valido_ate=(date.today() + timedelta(days=5)).isoformat(),
                                   documento_anexo_id=pdf(c, verif["id"], "ac"))
    with pytest.raises(NaoAutorizado, match="acreditado"):
        mrv.transitar(c, verif["id"], inv, "assumir")


# ---------------------------------------------------------------- pacote de verificação
def test_pacote_traz_comprovantes_trilha_e_hashes_conferiveis(cenario):
    c, inv = cenario["c"], cenario["inv"]
    verif = usuario(c, "verificador")
    dono = c.execute("SELECT id FROM usuario WHERE organizacao_id=? AND papel='elaborador'",
                     (inv["organizacao_id"],)).fetchone()[0]
    conteudo, nome = verificacao.pacote(c, dono, inv["id"])
    z = zipfile.ZipFile(io.BytesIO(conteudo))
    nomes = z.namelist()
    assert {"LEIA-ME.txt", "inventario.json", "relatorio.html", "inventario.xlsx", "trilha.csv", "SHA256SUMS.txt"} <= set(nomes)
    assert any(n.startswith("comprovantes/") for n in nomes) and nome.endswith(".zip")
    for linha in z.read("SHA256SUMS.txt").decode().splitlines():   # o que o verificador vai rodar
        h, arq = linha.split("  ", 1)
        assert hashlib.sha256(z.read(arq)).hexdigest() == h
    comp = next(n for n in nomes if n.startswith("comprovantes/"))
    assert hashlib.sha256(z.read(comp)).hexdigest()[:16] == comp.split("/")[1][:16]
    trilha = z.read("trilha.csv").decode("utf-8-sig")
    assert trilha.startswith("em;acao;") and "submeter" in trilha
    estranho = c.execute("SELECT id FROM usuario WHERE papel='elaborador' AND organizacao_id <> ?",
                         (inv["organizacao_id"],)).fetchone()[0]
    with pytest.raises(NaoAutorizado):
        verificacao.pacote(c, estranho, inv["id"])
    assert c.execute("SELECT COUNT(*) FROM auditoria WHERE acao='exportar_pacote_verificacao'").fetchone()[0] == 1
    if inv["verificado_por"]:                                            # quem verificou também baixa
        assert verificacao.pacote(c, inv["verificado_por"], inv["id"])[0][:2] == b"PK"


# ---------------------------------------------------------------- API
def test_rotas_de_conferencia_acreditacao_e_pacote(cenario):
    cli = TestClient(criar_app(cenario["caminho"]))
    c, inv = cenario["c"], cenario["inv"]
    H = lambda u: {"X-Usuario-Id": str(u)}  # noqa: E731
    verif, reg = usuario(c, "verificador")["id"], usuario(c, "registrador")["id"]
    doc = pdf(c, verif, "acred")
    aid = cli.post("/acreditacoes", headers=H(verif), json={
        "acreditador": "Inmetro/Cgcre", "numero": "OVV 9", "normas": "ISO 14065",
        "valido_ate": (date.today() + timedelta(days=90)).isoformat(), "documento_anexo_id": doc}).json()["id"]
    assert cli.post(f"/acreditacoes/{aid}/analise", headers=H(reg),
                    json={"aprovar": True, "parecer": "Conferido no Inmetro"}).status_code == 200
    assert cli.get("/acreditacoes", headers=H(verif)).json()[0]["status"] == "aprovada"
    dono = c.execute("SELECT id FROM usuario WHERE organizacao_id=? AND papel='elaborador'",
                     (inv["organizacao_id"],)).fetchone()[0]
    r = cli.get(f"/inventarios/{inv['id']}/pacote-verificacao.zip", headers=H(dono))
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    # o pedido da demonstração já foi conferido e liquidado; ele mostra o bloco e a conferência
    ped = c.execute("SELECT id FROM pedido WHERE status='pago' LIMIT 1").fetchone()[0]
    p = cli.get(f"/pedidos/{ped}", headers=H(dono)).json()
    assert p["registro_lote"]["registro_url"].startswith("https://registry.verra.org/")
    assert p["conferencia"]["resultado"] == "conforme"
