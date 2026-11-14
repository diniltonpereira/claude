"""E-mail por caixa de saída: fila na transação do ato, despacho com novas tentativas, LGPD."""
import smtplib
import socketserver
import threading
from datetime import date, datetime, timedelta, timezone
from email import message_from_bytes, policy

import pytest
from fastapi.testclient import TestClient

from plataforma import acesso, envio, terceirizadas
from plataforma.banco import ErroNegocio, conectar

from .test_carbonmg import _anexos, ct, lancar_meses, s  # noqa: F401 — fixtures reaproveitadas
from .test_producao import SENHA, entrar, prod  # noqa: F401


class Caixa:
    """Transporte falso: guarda as mensagens ou levanta o erro programado."""
    def __init__(self, erros=()):
        self.msgs, self.erros, self.fechado = [], list(erros), 0

    def enviar(self, msg):
        if self.erros:
            raise self.erros.pop(0)
        self.msgs.append(msg)

    def fechar(self):
        self.fechado += 1


def com_email(s):
    c = s["c"]
    c.execute("UPDATE usuario SET email='fiscal@pref.mg.gov.br' WHERE id=?", (s["fiscal"],))
    c.execute("UPDATE usuario SET email='contato@limpa.com.br' WHERE id=?", (s["forn"],))
    return c


def fila(c, **filtro):
    sql = "SELECT * FROM email_saida" + (" WHERE " + " AND ".join(f"{k}=?" for k in filtro) if filtro else "")
    return c.execute(sql + " ORDER BY id", tuple(filtro.values())).fetchall()


# ---------------------------------------------------------------- fila
def test_rotina_diaria_poe_lembrete_e_alerta_na_fila_sem_repetir(s, ct):
    c = com_email(s)
    terceirizadas.aceitar(c, s["forn"], ct)
    lancar_meses(s, ct, (2026, 9))
    terceirizadas.rotina_diaria(c, date(2026, 11, 5))
    terceirizadas.rotina_diaria(c, date(2026, 11, 6))
    (lemb,) = fila(c, tipo="lembrete")
    assert lemb["para"] == "contato@limpa.com.br" and "10/2026" in lemb["assunto"]
    assert "Prazo: 10/11/2026" in lemb["texto"] and "Minha conta" in lemb["texto"]
    terceirizadas.rotina_diaria(c, date(2026, 11, 11))
    (alerta,) = fila(c, tipo="alerta")
    assert alerta["para"] == "fiscal@pref.mg.gov.br" and "notificação formal" in alerta["texto"]


def test_titular_desliga_lembretes_mas_nao_a_notificacao_formal(s, ct):
    c = com_email(s)
    terceirizadas.aceitar(c, s["forn"], ct)
    lancar_meses(s, ct, (2026, 9))
    envio.definir_preferencia(c, s["forn"], False)
    terceirizadas.rotina_diaria(c, date(2026, 11, 5))
    assert fila(c, tipo="lembrete") == []
    terceirizadas.notificar(c, s["fiscal"], ct, assunto="Relato em atraso", texto="Regularize.", prazo="2026-12-01")
    (n,) = fila(c, tipo="notificacao_formal")
    assert n["para"] == "contato@limpa.com.br" and "Regularize." in n["texto"] and "Prazo: 01/12/2026" in n["texto"]


def test_ato_desfeito_nao_deixa_email(s, ct):
    c = com_email(s)
    terceirizadas.aceitar(c, s["forn"], ct)
    c.execute("BEGIN")
    terceirizadas.notificar(c, s["fiscal"], ct, assunto="X", texto="Y")
    c.execute("ROLLBACK")
    assert fila(c) == []


def test_assunto_sem_quebra_de_linha_e_sem_email_nao_enfileira(s, ct):
    c = com_email(s)
    eid = envio.enfileirar(c, chave="k", usuario_id=s["forn"], tipo="seguranca",
                           assunto="Oi\r\nBcc: alvo@x.com", texto="t")
    assert fila(c)[0]["assunto"] == "Oi Bcc: alvo@x.com" and eid
    assert envio.enfileirar(c, chave="k", usuario_id=s["forn"], tipo="seguranca", assunto="a", texto="t") is None
    assert envio.enfileirar(c, chave="k2", usuario_id=s["elab"], tipo="seguranca", assunto="a", texto="t") is None


# ---------------------------------------------------------------- despacho
def test_despacho_entrega_e_apaga_o_link_do_convite(s, ct):
    c = com_email(s)
    envio.enfileirar_convite(c, s["forn"], "https://x/painel/#/definir-senha/SEGREDO", 72)
    cx = Caixa()
    assert envio.despachar(c, cx)["enviados"] == 1
    msg = cx.msgs[0]
    assert msg["To"] == "contato@limpa.com.br" and msg["Auto-Submitted"] == "auto-generated"
    assert "SEGREDO" in msg.get_content() and "72 horas" in msg.get_content()
    e = fila(c)[0]
    assert e["situacao"] == "enviado" and "SEGREDO" not in e["texto"]
    assert envio.despachar(c, Caixa())["enviados"] == 0      # nada a reenviar


def test_falha_temporaria_espera_e_permanente_desiste(s, ct):
    c = com_email(s)
    for i in range(2):
        envio.enfileirar(c, chave=f"k{i}", usuario_id=s["forn"], tipo="seguranca", assunto="a", texto="t")
    t0 = datetime.now(timezone.utc) + timedelta(seconds=1)
    r = envio.despachar(c, Caixa([smtplib.SMTPServerDisconnected("caiu")]), agora=t0)
    assert r == {"situacao": "erro_smtp", "enviados": 0, "falhas": 0, "adiados": 1}
    a, b = fila(c)
    assert a["situacao"] == "pendente" and a["tentativas"] == 1 and a["proxima_tentativa"] > envio._iso(t0 + timedelta(minutes=4))
    assert b["tentativas"] == 0                                 # servidor fora: o resto espera
    r = envio.despachar(c, Caixa([envio.ErroPermanente("550 caixa inexistente")]), agora=t0 + timedelta(minutes=1))
    assert r["falhas"] == 1 and fila(c)[1]["situacao"] == "falhou"
    assert envio.despachar(c, Caixa(), agora=t0 + timedelta(minutes=6))["enviados"] == 1


def test_desiste_apos_seis_tentativas_e_administracao_reenvia(s, ct):
    c = com_email(s)
    envio.enfileirar(c, chave="k", usuario_id=s["forn"], tipo="seguranca", assunto="a", texto="t")
    t = datetime.now(timezone.utc) + timedelta(seconds=1)
    for _ in range(envio.MAX_TENTATIVAS):
        envio.despachar(c, Caixa([OSError("recusado")]), agora=t)
        t += timedelta(days=1)
    e = fila(c)[0]
    assert e["situacao"] == "falhou" and e["tentativas"] == envio.MAX_TENTATIVAS and "recusado" in e["ultimo_erro"]
    envio.reenviar(c, s["reg"], e["id"])
    with pytest.raises(ErroNegocio, match="falharam"):
        envio.reenviar(c, s["reg"], e["id"])                    # já voltou para a fila
    assert envio.despachar(c, Caixa(), agora=t)["enviados"] == 1


def test_reserva_abandonada_volta_para_a_fila(s, ct):
    c = com_email(s)
    eid = envio.enfileirar(c, chave="k", usuario_id=s["forn"], tipo="seguranca", assunto="a", texto="t")
    c.execute("UPDATE email_saida SET situacao='enviando', reservado_em='2026-01-01T00:00:00.000Z' WHERE id=?", (eid,))
    assert envio.despachar(c, Caixa())["enviados"] == 1


def test_sem_smtp_configurado_a_fila_espera(s, ct, monkeypatch):
    c = com_email(s)
    monkeypatch.delenv("CARBONO_SMTP_HOST", raising=False)
    envio.enfileirar(c, chave="k", usuario_id=s["forn"], tipo="seguranca", assunto="a", texto="t")
    assert envio.despachar(c)["situacao"] == "nao_configurado" and fila(c)[0]["situacao"] == "pendente"


# ---------------------------------------------------------------- SMTP de verdade (servidor local mínimo)
class _Smtp(socketserver.StreamRequestHandler):
    def handle(self):
        w = lambda s: self.wfile.write(s.encode() + b"\r\n")  # noqa: E731
        w("220 teste")
        while (linha := self.rfile.readline()):
            cmd = linha.decode().strip().upper()
            if cmd.startswith(("EHLO", "HELO")):
                w("250 ok")
            elif cmd.startswith("RCPT") and "INEXISTENTE" in cmd:
                w("550 caixa inexistente")
            elif cmd.startswith(("MAIL", "RCPT", "RSET", "NOOP")):
                w("250 ok")
            elif cmd == "DATA":
                w("354 envie")
                dados = b""
                while (l := self.rfile.readline()) != b".\r\n":
                    dados += l
                self.server.recebidos.append(dados)
                w("250 aceito")
            elif cmd == "QUIT":
                w("221 tchau")
                return


@pytest.fixture
def smtp(monkeypatch):
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _Smtp)
    srv.recebidos = []
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("CARBONO_SMTP_HOST", "127.0.0.1")
    monkeypatch.setenv("CARBONO_SMTP_PORTA", str(srv.server_address[1]))
    monkeypatch.setenv("CARBONO_SMTP_SEGURANCA", "nenhuma")
    monkeypatch.setenv("CARBONO_SMTP_REMETENTE", "CarbonMG <nao-responda@pref.mg.gov.br>")
    yield srv
    srv.shutdown()


def test_entrega_por_smtp_com_acentos_e_recusa_definitiva(s, ct, smtp):
    c = com_email(s)
    envio.enfileirar(c, chave="a", usuario_id=s["forn"], tipo="seguranca", assunto="Ação na sua conta", texto="Olá, ação!")
    c.execute("UPDATE usuario SET email='inexistente@limpa.com.br' WHERE id=?", (s["fiscal"],))
    envio.enfileirar(c, chave="b", usuario_id=s["fiscal"], tipo="seguranca", assunto="x", texto="y")
    r = envio.despachar(c)
    assert r["enviados"] == 1 and r["falhas"] == 1
    m = message_from_bytes(smtp.recebidos[0], policy=policy.default)
    assert m["Subject"] == "Ação na sua conta" and "Olá, ação!" in m.get_content()
    assert m["Message-ID"].endswith("@pref.mg.gov.br>")
    assert fila(c)[1]["situacao"] == "falhou" and "550" in fila(c)[1]["ultimo_erro"]


# ---------------------------------------------------------------- LGPD e API
def test_anonimizar_apaga_endereco_e_conteudo(s, ct):
    c = com_email(s)
    envio.enfileirar(c, chave="k", usuario_id=s["forn"], tipo="seguranca", assunto="a", texto="dado pessoal")
    acesso.anonimizar(c, s["reg"], s["forn"])
    e = fila(c)[0]
    assert (e["para"], e["texto"], e["situacao"]) == ("anonimizado", "[anonimizado]", "cancelado")


def test_api_convite_caixa_de_saida_e_preferencia(prod, monkeypatch):
    monkeypatch.delenv("CARBONO_SMTP_HOST", raising=False)
    c, cli = prod["c"], prod["cli"]
    from plataforma import operacao
    adm = operacao.criar_admin(prod["banco"], nome="Adm", email="adm@x.br", senha=SENHA)
    r = entrar(cli, "adm@x.br")
    cli.post("/auth/login/2fa", json={"desafio": r["desafio"], "codigo": acesso.codigo_totp(adm["totp_segredo"])})
    h = {"X-CSRF-Token": cli.get("/eu").json()["csrf"]}
    cli.post("/eu/termos", json={"versao": acesso.TERMOS_VERSAO}, headers=h)
    novo = c.execute("INSERT INTO usuario (nome,papel,email) VALUES ('Bia','fiscal','bia@pref.gov.br')").lastrowid
    r = cli.post(f"/usuarios/{novo}/convite", headers=h).json()
    assert r["email"] == "aguardando_configuracao" and "definir-senha" in r["link"]
    cx = cli.get("/emails").json()
    assert cx["resumo"]["pendente"] >= 1 and cx["resumo"]["configurado"] is False
    assert all("texto" not in e for e in cx["emails"])            # o link do convite não vaza na listagem
    # o titular desliga os lembretes
    cli2 = TestClient(prod["app"])
    entrar(cli2)
    h2 = {"X-CSRF-Token": cli2.get("/eu").json()["csrf"]}
    cli2.post("/eu/termos", json={"versao": acesso.TERMOS_VERSAO}, headers=h2)
    assert cli2.patch("/eu/preferencias", json={"avisos_email": False}, headers=h2).status_code == 200
    assert cli2.get("/eu").json()["usuario"]["avisos_email"] == 0
    assert cli2.get("/eu/dados").json()["usuario"]["avisos_email"] == 0
    assert cli2.get("/emails").status_code == 403


def test_migra_banco_da_versao_8(tmp_path):
    caminho = str(tmp_path / "v8.db")
    c = conectar(caminho)
    c.execute("DROP TABLE email_saida")
    c.execute("ALTER TABLE usuario DROP COLUMN avisos_email")
    c.execute("PRAGMA user_version = 8")
    c.close()
    c = conectar(caminho)
    assert c.execute("PRAGMA user_version").fetchone()[0] == 9
    uid = c.execute("INSERT INTO usuario (nome,papel) VALUES ('X','fiscal')").lastrowid
    assert c.execute("SELECT avisos_email FROM usuario WHERE id=?", (uid,)).fetchone()[0] == 1
    assert c.execute("SELECT COUNT(*) FROM email_saida").fetchone()[0] == 0
