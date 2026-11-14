"""Modo produção: sessão, CSRF, senha, TOTP, bloqueio, convite, gov.br, LGPD, migração e backup."""
import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from plataforma import acesso, operacao, registro
from plataforma.api import COOKIE, criar_app
from plataforma.banco import VERSAO_ESQUEMA, conectar

SEGREDO = "x" * 40
SENHA = "uma-senha-bem-longa"


@pytest.fixture
def prod(tmp_path, monkeypatch):
    monkeypatch.setenv("CARBONO_MODO", "producao")
    monkeypatch.setenv("CARBONO_SEGREDO", SEGREDO)
    monkeypatch.setenv("CARBONO_COOKIE_INSEGURO", "1")  # TestClient usa http
    monkeypatch.setenv("CARBONO_ANTIVIRUS", "desativado")  # o antivírus tem testes próprios
    monkeypatch.setenv("CARBONO_ANEXOS", str(tmp_path / "anexos"))
    banco = str(tmp_path / "prod.db")
    app = criar_app(banco)
    c = conectar(banco)
    org = c.execute("INSERT INTO organizacao (nome,cnpj,tipo) VALUES ('Pref','11111111000191','orgao_publico')").lastrowid
    gestor = c.execute("INSERT INTO usuario (nome,papel,organizacao_id,email,senha_hash) VALUES (?,?,?,?,?)",
                       ("Ana", "elaborador", org, "ana@pref.gov.br", acesso.gerar_hash_senha(SENHA))).lastrowid
    return {"app": app, "c": c, "banco": banco, "org": org, "gestor": gestor, "cli": TestClient(app)}


def entrar(cli, email="ana@pref.gov.br", senha=SENHA):
    r = cli.post("/auth/login", json={"email": email, "senha": senha})
    assert r.status_code == 200, r.text
    return r.json()


def test_producao_exige_segredo(tmp_path, monkeypatch):
    monkeypatch.setenv("CARBONO_MODO", "producao")
    monkeypatch.delenv("CARBONO_SEGREDO", raising=False)
    with pytest.raises(RuntimeError, match="CARBONO_SEGREDO"):
        criar_app(str(tmp_path / "x.db"))


def test_producao_exige_antivirus(tmp_path, monkeypatch):
    monkeypatch.setenv("CARBONO_MODO", "producao")
    monkeypatch.setenv("CARBONO_SEGREDO", SEGREDO)
    monkeypatch.delenv("CARBONO_CLAMAV", raising=False)
    monkeypatch.delenv("CARBONO_ANTIVIRUS", raising=False)
    with pytest.raises(RuntimeError, match="CARBONO_CLAMAV"):
        criar_app(str(tmp_path / "x.db"))


def test_cabecalho_de_demonstracao_e_ignorado(prod):
    assert prod["cli"].get("/eu", headers={"X-Usuario-Id": str(prod["gestor"])}).status_code == 401
    assert prod["cli"].get("/auth/config").json()["modo"] == "producao"


def test_login_cookie_csrf_e_termos(prod):
    cli = prod["cli"]
    r = cli.post("/auth/login", json={"email": "ana@pref.gov.br", "senha": SENHA})
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie
    eu = cli.get("/eu").json()
    assert eu["termos_pendentes"] is True and eu["csrf"] and "senha_hash" not in eu["usuario"]
    # termos pendentes bloqueiam o resto
    assert cli.get("/inventarios").status_code == 403
    # escrita sem CSRF é recusada
    assert cli.post("/eu/termos", json={"versao": acesso.TERMOS_VERSAO}).status_code == 403
    h = {"X-CSRF-Token": eu["csrf"]}
    assert cli.post("/eu/termos", json={"versao": acesso.TERMOS_VERSAO}, headers=h).status_code == 200
    assert cli.get("/inventarios").status_code == 200
    assert cli.post("/inventarios", json={"organizacao_id": prod["org"], "ano": 2025}, headers=h).status_code == 201
    assert cli.post("/auth/logout", headers=h).status_code == 200
    assert cli.get("/eu").status_code == 401


def test_cabecalhos_de_seguranca(prod):
    r = prod["cli"].get("/painel/")
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["x-frame-options"] == "DENY" and r.headers["x-content-type-options"] == "nosniff"


def test_senha_errada_bloqueia_apos_cinco(prod):
    cli = prod["cli"]
    for _ in range(5):
        assert cli.post("/auth/login", json={"email": "ana@pref.gov.br", "senha": "errada-errada"}).status_code == 403
    r = cli.post("/auth/login", json={"email": "ana@pref.gov.br", "senha": SENHA})
    assert r.status_code == 403 and "bloqueado" in r.json()["erro"]
    assert cli.post("/auth/login", json={"email": "ninguem@x.br", "senha": SENHA}).json()["erro"] == \
        "E-mail ou senha incorretos."


def test_admin_exige_totp_e_codigo_nao_reutiliza(prod, tmp_path):
    c = prod["c"]
    c.execute("INSERT INTO usuario (nome,papel,email,senha_hash) VALUES ('Sem2FA','registrador','a@x.br',?)",
              (acesso.gerar_hash_senha(SENHA),))
    r = prod["cli"].post("/auth/login", json={"email": "a@x.br", "senha": SENHA})
    assert r.status_code == 403 and "duas etapas" in r.json()["erro"]
    adm = operacao.criar_admin(prod["banco"], nome="Admin", email="adm@x.br", senha=SENHA)
    r = entrar(prod["cli"], "adm@x.br")
    assert r["segundo_fator"] is True
    codigo = acesso.codigo_totp(adm["totp_segredo"])
    assert prod["cli"].post("/auth/login/2fa", json={"desafio": r["desafio"], "codigo": "000000"}).status_code == 403
    ok = prod["cli"].post("/auth/login/2fa", json={"desafio": r["desafio"], "codigo": codigo})
    assert ok.status_code == 200
    r2 = entrar(TestClient(prod["app"]), "adm@x.br")
    reuso = prod["cli"].post("/auth/login/2fa", json={"desafio": r2["desafio"], "codigo": codigo})
    assert reuso.status_code == 403  # mesmo código não vale duas vezes


def test_totp_rfc6238_vetor_conhecido():
    # RFC 6238, apêndice B (SHA-1, segredo "12345678901234567890", T=59 -> 94287082; 6 dígitos = 287082)
    import base64
    seg = base64.b32encode(b"12345678901234567890").decode().rstrip("=")
    assert acesso.codigo_totp(seg, 59) == "287082"


def test_sessao_ociosa_expira(prod):
    cli = prod["cli"]
    entrar(cli)
    antigo = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(timespec="seconds")
    prod["c"].execute("UPDATE sessao SET ultimo_uso=?", (antigo,))
    assert cli.get("/eu").status_code == 401


def test_convite_define_senha_uma_vez(prod):
    c = prod["c"]
    adm = c.execute("INSERT INTO usuario (nome,papel) VALUES ('Adm','registrador')").lastrowid
    novo = c.execute("INSERT INTO usuario (nome,papel,email) VALUES ('Fiscal','fiscal','f@pref.gov.br')").lastrowid
    token = acesso.gerar_convite(c, adm, novo)
    cli = prod["cli"]
    assert cli.post("/auth/definir-senha", json={"token": token, "senha": "curta"}).status_code == 409
    assert cli.post("/auth/definir-senha", json={"token": token, "senha": SENHA}).status_code == 200
    assert cli.post("/auth/definir-senha", json={"token": token, "senha": SENHA}).status_code == 403
    assert entrar(cli, "f@pref.gov.br")["segundo_fator"] is False


def _govbr_env(monkeypatch):
    for k, v in {"GOVBR_CLIENT_ID": "cid", "GOVBR_CLIENT_SECRET": "seg",
                 "GOVBR_REDIRECT_URI": "https://carbonmg.exemplo/auth/govbr/retorno",
                 "CARBONO_URL_PUBLICA": "https://carbonmg.exemplo"}.items():
        monkeypatch.setenv(k, v)


def test_govbr_pkce_id_token_nonce_e_cpf_por_hmac(prod, monkeypatch):
    from .govbr_falso import GovBrFalso
    _govbr_env(monkeypatch)
    c = prod["c"]
    c.execute("UPDATE usuario SET cpf_hmac=? WHERE id=?", (acesso.cpf_hmac("123.456.789-09"), prod["gestor"]))
    url = acesso.govbr_url_login(c)
    assert "code_challenge_method=S256" in url and "govbr_confiabilidades_idtoken" in url
    gov = GovBrFalso("12345678909")
    code, state = gov.autorizar(url)
    r = acesso.govbr_callback(c, code, state, cliente=gov.cliente)
    assert r["token"]
    assert c.execute("SELECT metodo FROM sessao WHERE usuario_id=? AND metodo='govbr'", (prod["gestor"],)).fetchone()
    with pytest.raises(Exception, match="expirado"):  # state não pode ser reutilizado
        acesso.govbr_callback(c, code, state, cliente=gov.cliente)
    # nonce diferente do pedido: recusa (resposta de outro login)
    code2, state2 = gov.autorizar(acesso.govbr_url_login(c))
    gov.nonce_por_code[code2] = "outro"
    with pytest.raises(Exception, match="não corresponde"):
        acesso.govbr_callback(c, code2, state2, cliente=gov.cliente)
    # id_token de outro aplicativo (aud diferente): recusa
    outro = GovBrFalso("12345678909", client_id="app-de-terceiro")
    code3, state3 = outro.autorizar(acesso.govbr_url_login(c))
    with pytest.raises(Exception, match="confirmar a identidade"):
        acesso.govbr_callback(c, code3, state3, cliente=outro.cliente)
    desconhecido = GovBrFalso("98765432100")
    code4, state4 = desconhecido.autorizar(acesso.govbr_url_login(c))
    with pytest.raises(Exception, match="CPF não cadastrado"):
        acesso.govbr_callback(c, code4, state4, cliente=desconhecido.cliente)
    assert c.execute("SELECT COUNT(*) FROM usuario WHERE cpf_hmac LIKE '%123%456%'").fetchone()[0] == 0


def test_logout_de_quem_entrou_pelo_govbr_passa_pelo_login_unico(prod, monkeypatch):
    from .govbr_falso import GovBrFalso
    _govbr_env(monkeypatch)
    c, cli = prod["c"], prod["cli"]
    c.execute("UPDATE usuario SET cpf_hmac=? WHERE id=?", (acesso.cpf_hmac("12345678909"), prod["gestor"]))
    gov = GovBrFalso("12345678909")
    code, state = gov.autorizar(acesso.govbr_url_login(c))
    r = acesso.govbr_callback(c, code, state, cliente=gov.cliente)
    cli.cookies.set("carbonmg_sessao", r["token"])
    saida = cli.post("/auth/logout", headers={"X-CSRF-Token": r["csrf"]}).json()
    assert saida["logout_govbr"] == ("https://sso.staging.acesso.gov.br/logout?post_logout_redirect_uri="
                                     "https%3A%2F%2Fcarbonmg.exemplo%2Fpainel%2F")
    entrar(cli)  # login por senha: sem logout no gov.br
    csrf = cli.get("/eu").json()["csrf"]
    assert cli.post("/auth/logout", headers={"X-CSRF-Token": csrf}).json()["logout_govbr"] is None


def test_lgpd_politica_exportacao_e_anonimizacao(prod):
    cli, c = prod["cli"], prod["c"]
    assert "encarregado" in cli.get("/privacidade").json()
    entrar(cli)
    csrf = cli.get("/eu").json()["csrf"]
    cli.post("/eu/termos", json={"versao": acesso.TERMOS_VERSAO}, headers={"X-CSRF-Token": csrf})
    dados = cli.get("/eu/dados").json()
    assert dados["usuario"]["email"] == "ana@pref.gov.br" and "senha_hash" not in json.dumps(dados)
    adm = c.execute("INSERT INTO usuario (nome,papel) VALUES ('Adm','registrador')").lastrowid
    acesso.anonimizar(c, adm, prod["gestor"])
    u = c.execute("SELECT * FROM usuario WHERE id=?", (prod["gestor"],)).fetchone()
    assert u["email"] is None and u["senha_hash"] is None and u["ativo"] == 0 and "anonimizado" in u["nome"]
    assert cli.get("/eu").status_code == 401  # sessões encerradas
    assert c.execute("SELECT COUNT(*) FROM auditoria WHERE usuario_id=?", (prod["gestor"],)).fetchone()[0] > 0


# ---------------------------------------------------------------- migração e backup
def test_migracao_da_versao_4_preserva_dados(tmp_path):
    p = str(tmp_path / "v4.db")
    c = conectar(p)
    c.execute("INSERT INTO usuario (nome,papel) VALUES ('Antigo','elaborador')")
    for idx in ("ux_usuario_email", "ux_usuario_cpf"):
        c.execute(f"DROP INDEX {idx}")
    for t in ("sessao", "convite", "oidc_estado"):
        c.execute(f"DROP TABLE {t}")
    for col in ("email", "cpf_hmac", "senha_hash", "totp_segredo", "totp_ativo", "totp_ultimo_passo",
                "tentativas_falhas", "bloqueado_ate", "termos_versao", "termos_aceitos_em", "ativo", "anonimizado_em"):
        c.execute(f"ALTER TABLE usuario DROP COLUMN {col}")
    c.execute("PRAGMA user_version = 4")
    c.close()
    c = conectar(p)
    assert c.execute("PRAGMA user_version").fetchone()[0] == VERSAO_ESQUEMA
    u = c.execute("SELECT nome, ativo, email FROM usuario").fetchone()
    assert tuple(u) == ("Antigo", 1, None)
    assert c.execute("SELECT COUNT(*) FROM sessao").fetchone()[0] == 0


def test_backup_e_restauracao_verificados(tmp_path, monkeypatch):
    monkeypatch.setenv("CARBONO_ANEXOS", str(tmp_path / "anexos"))
    from plataforma.demo import executar
    banco = str(tmp_path / "demo.db")
    monkeypatch.chdir(tmp_path)
    executar(banco)
    m = operacao.backup(banco, str(tmp_path / "anexos"), str(tmp_path / "bkp"))
    assert m["anexos_total"] > 0 and m["anexos_novos"] == m["anexos_total"]
    assert operacao.backup(banco, str(tmp_path / "anexos"), str(tmp_path / "bkp"))["anexos_novos"] == 0
    arquivo = tmp_path / "bkp" / m["banco"]
    # desastre: banco atual perdido
    Path(banco).unlink()
    r = operacao.restaurar(str(arquivo), banco, str(tmp_path / "anexos2"))
    assert r["movimentos_conferidos"] > 0 and r["anexos_restaurados"] == m["anexos_total"]
    assert registro.verificar_cadeia(conectar(banco))["integra"]
    # backup adulterado é recusado
    with open(arquivo, "r+b") as f:
        f.seek(200)
        f.write(b"XX")
    with pytest.raises(SystemExit, match="não confere"):
        operacao.restaurar(str(arquivo), banco, str(tmp_path / "anexos2"))
