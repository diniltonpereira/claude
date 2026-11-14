"""Assinatura eletrônica avançada gov.br do contrato de parceria (fluxo completo com gov.br e ITI simulados)."""
import hashlib

import httpx
import pytest
from fastapi.testclient import TestClient

from plataforma import acesso, anexos, assinatura as ass, projetos as pj
from plataforma.api import criar_app
from plataforma.banco import ErroNegocio, NaoAutorizado, conectar

from cryptography.hazmat.primitives.asymmetric import rsa

from .govbr_falso import GovBrFalso, certificado, pkcs7_do_hash
from .test_projetos import MOC, PDF, _anexos, criar, s  # noqa: F401 — fixtures reaproveitadas

CONTRATO = PDF + b"% contrato de parceria v1\n"


@pytest.fixture
def credenciais(monkeypatch):
    for k, v in {"CARBONO_SEGREDO": "x" * 40, "GOVBR_CLIENT_ID": "cid", "GOVBR_CLIENT_SECRET": "seg",
                 "GOVBR_REDIRECT_URI": "https://lastro.exemplo/auth/govbr/retorno",
                 "ITI_CLIENT_ID": "iti-cid", "ITI_CLIENT_SECRET": "iti-seg",
                 "ITI_REDIRECT_URI": "https://lastro.exemplo/assinaturas/iti/retorno"}.items():
        monkeypatch.setenv(k, v)


def projeto_com_parceria(s):
    c = s["c"]
    pid = criar(s, "gestor")
    pj.registrar_parceria(c, s["gestor"], pid, desenvolvedor_cnpj="22222222000191", desenvolvedor_nome="Fazenda",
                          percentual_titular=60, percentual_desenvolvedor=40, licitacao_ref="Concorrência 1/2026")
    anexo = anexos.salvar(c, s["gestor"], "contrato.pdf", "application/pdf", CONTRATO)["id"]
    did = ass.criar_documento(c, s["gestor"], pid, anexo_id=anexo, titulo="Contrato de parceria")
    return pid, did


def assinar(c, uid, did, gov: GovBrFalso):
    """Percorre as três etapas como o navegador faria."""
    code, state = gov.autorizar(ass.iniciar(c, uid, did))
    url_iti = ass.apos_identidade(c, uid, code, state, cliente=gov.cliente)
    assert url_iti.startswith("https://cas.staging.iti.br/oauth2.0/authorize?") and "scope=sign+govbr" in url_iti
    code, state = gov.autorizar(url_iti)
    return ass.apos_autorizacao(c, uid, code, state, cliente=gov.cliente)


def test_as_duas_partes_assinam_e_a_parceria_fica_assinada(s, credenciais):
    c = s["c"]
    pid, did = projeto_com_parceria(s)
    gestora, empresa = GovBrFalso("12345678909", "Ana Gestora"), GovBrFalso("98765432100", "Bruno Empresário", "gold")
    r = assinar(c, s["gestor"], did, gestora)
    assert r == {"documento_id": did, "projeto_id": pid, "concluido": False}
    sit = ass.situacao(c, pid, s["fazendeiro"])
    titular = next(p for p in sit["partes"] if p["parte"] == "titular")
    assert titular["signatario_nome"] == "ANA GESTORA" and "Governo Federal" in titular["certificado_emissor"]
    assert next(p for p in sit["partes"] if p["parte"] == "desenvolvedor")["pode_assinar"]
    assert assinar(c, s["fazendeiro"], did, empresa)["concluido"]
    par = c.execute("SELECT * FROM parceria WHERE projeto_id=?", (pid,)).fetchone()
    assert par["assinatura"] == "assinado" and f"documento nº {did}" in par["assinatura_ref"]
    assert ass.situacao(c, pid)["situacao"] == "concluido"
    # o .p7s guardado assina o SHA-256 do PDF e confere com o certificado
    p7s, nome = ass.p7s(c, s["gestor"], titular["assinatura_id"])
    assert ass.verificar_p7s(p7s, hashlib.sha256(CONTRATO).digest())["cpf"] == "12345678909" and nome.endswith(".p7s")
    # o CPF passa a ficar vinculado ao usuário (só como HMAC)
    assert c.execute("SELECT cpf_hmac FROM usuario WHERE id=?", (s["gestor"],)).fetchone()[0] == acesso.cpf_hmac("12345678909")
    with pytest.raises(Exception, match="imutável"):
        c.execute("DELETE FROM assinatura_eletronica")
    acoes = [r[0] for r in c.execute("SELECT acao FROM auditoria WHERE acao LIKE 'assina%' ORDER BY id")]
    assert acoes == ["assinar", "assinar", "assinatura_concluida"]


def test_conta_bronze_e_barrada_antes_da_autorizacao_do_iti(s, credenciais):
    c = s["c"]
    _, did = projeto_com_parceria(s)
    gov = GovBrFalso(nivel="bronze")
    code, state = gov.autorizar(ass.iniciar(c, s["gestor"], did))
    with pytest.raises(ass.ContaBronze, match="prata ou ouro"):
        ass.apos_identidade(c, s["gestor"], code, state, cliente=gov.cliente)
    assert not any("iti.br" in u for _, u in gov.chamadas)          # nem chegou ao ITI
    assert c.execute("SELECT COUNT(*) FROM oidc_estado WHERE proposito='assinatura_iti'").fetchone()[0] == 0


def test_conta_gov_br_de_outro_cpf_e_recusada(s, credenciais):
    c = s["c"]
    _, did = projeto_com_parceria(s)
    c.execute("UPDATE usuario SET cpf_hmac=? WHERE id=?", (acesso.cpf_hmac("11122233396"), s["gestor"]))
    gov = GovBrFalso("12345678909")
    code, state = gov.autorizar(ass.iniciar(c, s["gestor"], did))
    with pytest.raises(NaoAutorizado, match="CPF cadastrado"):
        ass.apos_identidade(c, s["gestor"], code, state, cliente=gov.cliente)


def test_pacote_que_nao_assina_este_documento_nao_e_guardado(s, credenciais):
    c = s["c"]
    _, did = projeto_com_parceria(s)
    gov = GovBrFalso()
    gov.adulterar_hash = True
    with pytest.raises(ErroNegocio, match="não corresponde a este documento"):
        assinar(c, s["gestor"], did, gov)
    assert c.execute("SELECT COUNT(*) FROM assinatura_eletronica").fetchone()[0] == 0


def test_certificado_de_outra_pessoa_e_recusado(s, credenciais):
    c = s["c"]
    _, did = projeto_com_parceria(s)
    gov = GovBrFalso("12345678909")
    gov.chave_cidadao = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    gov.cert = certificado("OUTRA PESSOA", "98765432100", gov.chave_cidadao)   # ITI devolve outro CPF
    with pytest.raises(NaoAutorizado, match="não é do CPF autenticado"):
        assinar(c, s["gestor"], did, gov)


def test_assinatura_adulterada_nao_confere():
    gov = GovBrFalso()
    digest = hashlib.sha256(CONTRATO).digest()
    pacote = bytearray(pkcs7_do_hash(digest, gov.chave_cidadao, gov.cert))
    pacote[-5] ^= 0xFF                                     # último bloco: o valor da assinatura
    with pytest.raises(ErroNegocio, match="não confere"):
        ass.verificar_p7s(bytes(pacote), digest)
    with pytest.raises(ErroNegocio, match="inválido"):
        ass.verificar_p7s(b"lixo", digest)


def test_quem_nao_e_parte_nao_assina_e_ninguem_assina_duas_vezes(s, credenciais):
    c = s["c"]
    pid, did = projeto_com_parceria(s)
    with pytest.raises(NaoAutorizado, match="não representa"):
        ass.iniciar(c, s["quilombo"], did)
    assinar(c, s["gestor"], did, GovBrFalso())
    with pytest.raises(ErroNegocio, match="já assinou"):
        ass.iniciar(c, s["gestor"], did)
    # o pedido iniciado por uma pessoa não pode ser concluído por outra
    gov = GovBrFalso("98765432100")
    code, state = gov.autorizar(ass.iniciar(c, s["fazendeiro"], did))
    with pytest.raises(NaoAutorizado, match="outra pessoa"):
        ass.apos_identidade(c, s["outro"], code, state, cliente=gov.cliente)
    # com assinatura em andamento, não se troca o documento sem cancelar
    novo = anexos.salvar(c, s["gestor"], "v2.pdf", "application/pdf", PDF + b"% v2\n")["id"]
    with pytest.raises(ErroNegocio, match="Cancele"):
        ass.criar_documento(c, s["gestor"], pid, anexo_id=novo, titulo="Contrato v2")
    ass.cancelar_documento(c, s["gestor"], did)
    assert ass.criar_documento(c, s["gestor"], pid, anexo_id=novo, titulo="Contrato v2") != did


def test_sem_credenciais_o_botao_fica_indisponivel(s, monkeypatch):
    for k in ("ITI_CLIENT_ID", "GOVBR_CLIENT_ID"):
        monkeypatch.delenv(k, raising=False)
    pid, did = projeto_com_parceria(s)
    assert ass.situacao(s["c"], pid, s["gestor"])["assinatura_disponivel"] is False
    with pytest.raises(ErroNegocio, match="credenciais"):
        ass.iniciar(s["c"], s["gestor"], did)


# ---------------------------------------------------------------- pelas rotas, em modo produção
def test_fluxo_pelas_rotas_com_redirecionamentos(tmp_path, monkeypatch, credenciais):
    from .test_producao import SEGREDO
    for k, v in {"CARBONO_MODO": "producao", "CARBONO_SEGREDO": SEGREDO, "CARBONO_COOKIE_INSEGURO": "1",
                 "CARBONO_ANTIVIRUS": "desativado", "CARBONO_ANEXOS": str(tmp_path / "anexos")}.items():
        monkeypatch.setenv(k, v)
    app = criar_app(str(tmp_path / "p.db"))
    c = conectar(app.state.caminho)
    ins = lambda sql, *a: c.execute(sql, a).lastrowid  # noqa: E731
    pref = ins("INSERT INTO organizacao (nome,cnpj,tipo,esfera) VALUES ('Pref','11111111000191','orgao_publico','municipal')")
    g = ins("INSERT INTO usuario (nome,papel,organizacao_id,email,senha_hash,termos_versao) VALUES (?,?,?,?,?,?)",
            "Ana", "elaborador", pref, "ana@pref.gov.br", acesso.gerar_hash_senha("uma-senha-bem-longa"), acesso.TERMOS_VERSAO)
    pid = pj.criar(c, g, nome="Biogás", tipo="biogas", natureza_area="publica_propria",
                   linha_base="Flare aberto.", estimativa_tco2e_ano=100, **MOC)
    pj.registrar_parceria(c, g, pid, desenvolvedor_cnpj="22222222000191", desenvolvedor_nome="Dev",
                          percentual_titular=60, percentual_desenvolvedor=40, licitacao_ref="Concorrência 1/2026")
    cli = TestClient(app, follow_redirects=False)
    r = cli.post("/auth/login", json={"email": "ana@pref.gov.br", "senha": "uma-senha-bem-longa"}).json()
    h = {"X-CSRF-Token": r["csrf"]}
    up = cli.post("/anexos", headers=h, files={"arquivo": ("contrato.pdf", CONTRATO, "application/pdf")}).json()
    did = cli.post(f"/projetos/{pid}/documentos-assinatura", headers=h,
                   json={"anexo_id": up["id"], "titulo": "Contrato de parceria"}).json()["id"]
    gov = GovBrFalso()
    monkeypatch.setattr(httpx, "Client", lambda **_: gov.cliente)
    url = cli.post(f"/documentos-assinatura/{did}/inicio", headers=h).json()["url"]
    code, state = gov.autorizar(url)
    r1 = cli.get(f"/auth/govbr/retorno?code={code}&state={state}")
    assert r1.status_code == 302 and r1.headers["location"].startswith("https://cas.staging.iti.br/oauth2.0/authorize")
    code, state = gov.autorizar(r1.headers["location"])
    r2 = cli.get(f"/assinaturas/iti/retorno?code={code}&state={state}")
    assert r2.headers["location"] == f"/painel/#/projetos/{pid}?assinatura=ok"
    det = cli.get(f"/projetos/{pid}").json()["documento_assinatura"]
    aid = next(p["assinatura_id"] for p in det["partes"] if p["parte"] == "titular")
    baixado = cli.get(f"/assinaturas/{aid}/p7s")
    assert baixado.headers["content-type"] == "application/pkcs7-signature"
    assert ass.verificar_p7s(baixado.content, hashlib.sha256(CONTRATO).digest())
    # cancelar no ITI (sem code) volta ao painel com aviso
    assert "cancelada" in cli.get(f"/assinaturas/iti/retorno?state=x").headers["location"]
    assert cli.get("/auth/config").json()["assinatura_govbr"] is True
