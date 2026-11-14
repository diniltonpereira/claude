"""Envio de e-mails por caixa de saída (outbox).

Por que caixa de saída: o e-mail é gravado na MESMA transação do ato que o gera (convite, lembrete,
notificação formal). Um despachante separado entrega pelo SMTP do órgão, com novas tentativas.
Assim, uma queda do servidor de e-mail não desfaz o ato nem perde a mensagem, e um ato desfeito
não deixa e-mail enviado.

- Idempotência: cada mensagem tem uma `chave` única (ex.: "notificacao:12:usuario:5").
- Entrega pelo menos uma vez: se o processo cair no meio do envio, a mensagem volta à fila depois
  de 15 minutos; o Message-ID é fixo, então o leitor de e-mail agrupa uma eventual repetição.
- Novas tentativas: 5, 10, 20, 40 e 80 minutos; recusa definitiva do servidor (5xx) não é repetida.
- LGPD: lembretes e alertas podem ser desligados pelo titular ("Minha conta"). Convite, notificação
  formal e aviso de segurança não, porque são atos do processo ou proteção da própria conta.
  O link do convite é apagado da caixa de saída assim que o e-mail é entregue.

Configuração (SMTP do órgão ou serviço contratado):
    CARBONO_SMTP_HOST, CARBONO_SMTP_PORTA (587), CARBONO_SMTP_SEGURANCA (starttls | ssl | nenhuma),
    CARBONO_SMTP_USUARIO, CARBONO_SMTP_SENHA, CARBONO_SMTP_REMETENTE ("CarbonMG <nao-responda@...>")
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import smtplib
import sqlite3
import ssl
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import format_datetime, parseaddr
from typing import Protocol

from .auditoria import registrar
from .banco import ErroNegocio, NaoAutorizado, buscar

log = logging.getLogger("carbonmg")

OPCIONAIS = ("lembrete", "alerta")           # o titular pode desligar
MAX_TENTATIVAS = 6
RESERVA_MINUTOS = 15
CONVITE_ENVIADO = "[link removido após a entrega do e-mail]"


def _agora() -> datetime:
    return datetime.now(timezone.utc)


def _iso(d: datetime) -> str:
    return d.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _nome_plataforma() -> str:
    return os.environ.get("PLATAFORMA_NOME", "CarbonMG")


def url_painel() -> str | None:
    base = os.environ.get("CARBONO_URL_PUBLICA")
    return f"{base.rstrip('/')}/painel/" if base else None


# ---------------------------------------------------------------- fila
def enfileirar(con: sqlite3.Connection, *, chave: str, usuario_id: int, tipo: str, assunto: str,
               texto: str) -> int | None:
    """Grava o e-mail na caixa de saída. Devolve None se o usuário não tem e-mail, está inativo,
    desligou esse tipo de aviso ou se a mensagem (mesma chave) já existe."""
    u = buscar(con, "usuario", usuario_id)
    if not u["email"] or not u["ativo"]:
        return None
    if tipo in OPCIONAIS and not u["avisos_email"]:
        return None
    assunto = " ".join(assunto.split())[:200]       # sem quebra de linha: impede injeção de cabeçalho
    rodape = [f"\n\n—\nMensagem automática do {_nome_plataforma()}. Não responda a este e-mail."]
    if url_painel():
        rodape.append(f"Acesse: {url_painel()}")
    if tipo in OPCIONAIS:
        rodape.append("Para deixar de receber lembretes e alertas por e-mail, desmarque a opção em \"Minha conta\".")
    try:
        return con.execute(
            "INSERT INTO email_saida (chave, usuario_id, para, tipo, assunto, texto) VALUES (?,?,?,?,?,?)",
            (chave, usuario_id, u["email"], tipo, assunto, texto + "\n".join(rodape))).lastrowid
    except sqlite3.IntegrityError:
        return None


def enfileirar_notificacao(con: sqlite3.Connection, notificacao_id: int) -> int:
    """Leva uma notificação do contrato (lembrete, alerta, notificação formal) aos destinatários:
    terceirizada = usuários da empresa; fiscal = fiscais do órgão contratante."""
    n = buscar(con, "notificacao", notificacao_id)
    c = buscar(con, "contrato", n["contrato_id"])
    if n["destinatario"] == "fornecedor":
        sql, org = "SELECT id FROM usuario WHERE organizacao_id=? AND papel='fornecedor' AND ativo=1", c["fornecedor_id"]
    else:
        sql, org = "SELECT id FROM usuario WHERE organizacao_id=? AND papel='fiscal' AND ativo=1", c["orgao_id"]
    texto = f"Contrato {c['numero']} — {c['objeto']}\n\n{n['texto']}"
    if n["prazo"]:
        texto += f"\n\nPrazo: {datetime.fromisoformat(n['prazo']).strftime('%d/%m/%Y')}."
    if n["tipo"] == "notificacao_formal":
        texto = ("Você recebeu uma notificação formal do fiscal do contrato. Ela também está registrada na "
                 "plataforma, na página do contrato.\n\n" + texto)
    total = 0
    for r in con.execute(sql, (org,)).fetchall():
        if enfileirar(con, chave=f"notificacao:{notificacao_id}:usuario:{r['id']}", usuario_id=r["id"],
                      tipo=n["tipo"], assunto=n["assunto"], texto=texto):
            total += 1
    return total


def enfileirar_convite(con: sqlite3.Connection, usuario_id: int, link: str, horas: int) -> int | None:
    u = buscar(con, "usuario", usuario_id)
    # um convite novo torna inútil o e-mail de um convite anterior ainda não enviado
    con.execute("UPDATE email_saida SET situacao='cancelado', texto=? WHERE usuario_id=? AND tipo='convite' "
                "AND situacao IN ('pendente','falhou')", (CONVITE_ENVIADO, usuario_id))
    org = buscar(con, "organizacao", u["organizacao_id"])["nome"] if u["organizacao_id"] else None
    texto = (f"Olá, {u['nome']}.\n\nSeu acesso ao {_nome_plataforma()}"
             + (f" ({org})" if org else "") + " foi criado. Para cadastrar sua senha, abra o link abaixo em até "
             f"{horas} horas. Ele só pode ser usado uma vez.\n\n{link}\n\n"
             "Se você não esperava este convite, ignore esta mensagem.")
    return enfileirar(con, chave=f"convite:{hashlib.sha256(link.encode()).hexdigest()[:24]}", usuario_id=usuario_id,
                      tipo="convite", assunto=f"Seu acesso ao {_nome_plataforma()}", texto=texto)


def enfileirar_seguranca(con: sqlite3.Connection, usuario_id: int, evento: str, descricao: str) -> int | None:
    texto = (f"{descricao} em {_agora().strftime('%d/%m/%Y às %H:%M')} (UTC).\n\n"
             "Se não foi você, avise a administração imediatamente.")
    return enfileirar(con, chave=f"seguranca:{evento}:{usuario_id}:{_iso(_agora())}", usuario_id=usuario_id,
                      tipo="seguranca", assunto=f"{_nome_plataforma()}: {descricao.lower()}", texto=texto)


# ---------------------------------------------------------------- transporte SMTP
class ErroPermanente(Exception):
    """O servidor recusou a mensagem em definitivo (endereço inexistente, por exemplo)."""


class Transporte(Protocol):
    def enviar(self, msg: EmailMessage) -> None: ...
    def fechar(self) -> None: ...


class TransporteSmtp:
    def __init__(self, host: str, porta: int, seguranca: str = "starttls", usuario: str | None = None,
                 senha: str | None = None, tempo: float = 30):
        if seguranca not in ("starttls", "ssl", "nenhuma"):
            raise ValueError("CARBONO_SMTP_SEGURANCA deve ser starttls, ssl ou nenhuma.")
        self.host, self.porta, self.seguranca = host, porta, seguranca
        self.usuario, self.senha, self.tempo = usuario, senha, tempo
        self._smtp: smtplib.SMTP | None = None

    def _abrir(self) -> smtplib.SMTP:
        ctx = ssl.create_default_context()
        if self.seguranca == "ssl":
            s = smtplib.SMTP_SSL(self.host, self.porta, timeout=self.tempo, context=ctx)
        else:
            s = smtplib.SMTP(self.host, self.porta, timeout=self.tempo)
            if self.seguranca == "starttls":
                s.starttls(context=ctx)
        if self.usuario:
            s.login(self.usuario, self.senha or "")
        return s

    def enviar(self, msg: EmailMessage) -> None:
        if self._smtp is None:
            self._smtp = self._abrir()
        try:
            self._smtp.send_message(msg)
        except smtplib.SMTPRecipientsRefused as exc:
            raise ErroPermanente(str(exc)[:300]) from exc
        except smtplib.SMTPResponseException as exc:
            if 500 <= exc.smtp_code < 600 and not isinstance(exc, smtplib.SMTPAuthenticationError):
                raise ErroPermanente(f"{exc.smtp_code} {exc.smtp_error!r}"[:300]) from exc
            raise

    def fechar(self) -> None:
        if self._smtp is not None:
            try:
                self._smtp.quit()
            except (smtplib.SMTPException, OSError):
                pass
            self._smtp = None


def configurado() -> bool:
    return bool(os.environ.get("CARBONO_SMTP_HOST") and os.environ.get("CARBONO_SMTP_REMETENTE"))


def transporte_do_ambiente() -> TransporteSmtp | None:
    if not configurado():
        return None
    seg = os.environ.get("CARBONO_SMTP_SEGURANCA", "starttls")
    return TransporteSmtp(os.environ["CARBONO_SMTP_HOST"],
                          int(os.environ.get("CARBONO_SMTP_PORTA", "465" if seg == "ssl" else "587")), seg,
                          os.environ.get("CARBONO_SMTP_USUARIO") or None, os.environ.get("CARBONO_SMTP_SENHA"))


def montar(e: sqlite3.Row, remetente: str) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = remetente
    msg["To"] = e["para"]
    msg["Subject"] = e["assunto"]
    msg["Date"] = format_datetime(_agora())
    dominio = parseaddr(remetente)[1].rpartition("@")[2] or "carbonmg.local"
    msg["Message-ID"] = f"<carbonmg-{e['id']}-{hashlib.sha256(e['chave'].encode()).hexdigest()[:16]}@{dominio}>"
    msg["Auto-Submitted"] = "auto-generated"       # RFC 3834: evita resposta automática em laço
    msg.set_content(e["texto"])
    return msg


# ---------------------------------------------------------------- despacho
def despachar(con: sqlite3.Connection, transporte: Transporte | None = None, *, agora: datetime | None = None,
              limite: int = 100) -> dict:
    """Envia o que estiver na fila. Rodar a cada minuto (o `servir` já faz isso sozinho)."""
    if transporte is None:
        transporte = transporte_do_ambiente()
        if transporte is None:
            return {"situacao": "nao_configurado", "enviados": 0, "falhas": 0, "adiados": 0}
    agora = agora or _agora()
    remetente = os.environ.get("CARBONO_SMTP_REMETENTE", f"{_nome_plataforma()} <nao-responda@carbonmg.local>")
    # reservas antigas: o processo caiu no meio do envio
    con.execute("UPDATE email_saida SET situacao='pendente' WHERE situacao='enviando' AND reservado_em < ?",
                (_iso(agora - timedelta(minutes=RESERVA_MINUTOS)),))
    r = {"situacao": "ok", "enviados": 0, "falhas": 0, "adiados": 0}
    fila = con.execute("SELECT id FROM email_saida WHERE situacao='pendente' AND proxima_tentativa <= ? "
                       "ORDER BY id LIMIT ?", (_iso(agora), limite)).fetchall()
    try:
        for (eid,) in fila:
            if con.execute("UPDATE email_saida SET situacao='enviando', reservado_em=?, tentativas=tentativas+1 "
                           "WHERE id=? AND situacao='pendente'", (_iso(agora), eid)).rowcount != 1:
                continue        # outro despachante pegou
            e = con.execute("SELECT * FROM email_saida WHERE id=?", (eid,)).fetchone()
            try:
                transporte.enviar(montar(e, remetente))
            except ErroPermanente as exc:
                con.execute("UPDATE email_saida SET situacao='falhou', ultimo_erro=? WHERE id=?", (str(exc), eid))
                log.warning(json.dumps({"evento": "email_recusado", "email_id": eid, "tipo": e["tipo"]}))
                r["falhas"] += 1
            except (smtplib.SMTPException, OSError) as exc:
                erro = f"{type(exc).__name__}: {exc}"[:300]
                if e["tentativas"] >= MAX_TENTATIVAS:
                    con.execute("UPDATE email_saida SET situacao='falhou', ultimo_erro=? WHERE id=?", (erro, eid))
                    r["falhas"] += 1
                else:
                    espera = timedelta(minutes=5 * 2 ** (e["tentativas"] - 1))
                    con.execute("UPDATE email_saida SET situacao='pendente', ultimo_erro=?, proxima_tentativa=? "
                                "WHERE id=?", (erro, _iso(agora + espera), eid))
                    r["adiados"] += 1
                log.warning(json.dumps({"evento": "email_erro", "email_id": eid, "erro": type(exc).__name__}))
                r["situacao"] = "erro_smtp"
                break           # servidor fora do ar: o resto espera a próxima rodada
            else:
                con.execute("UPDATE email_saida SET situacao='enviado', enviado_em=?, ultimo_erro=NULL, "
                            "texto=CASE WHEN tipo='convite' THEN ? ELSE texto END WHERE id=?",
                            (_iso(_agora()), CONVITE_ENVIADO, eid))
                r["enviados"] += 1
    finally:
        transporte.fechar()
    return r


# ---------------------------------------------------------------- administração e titular
def listar(con: sqlite3.Connection, situacao: str | None = None, limite: int = 200) -> list[dict]:
    """Caixa de saída para a administração. Sem o corpo: o convite leva um link de acesso."""
    sql = ("SELECT e.id, e.para, e.tipo, e.assunto, e.situacao, e.tentativas, e.ultimo_erro, e.criado_em, "
           "e.enviado_em, e.proxima_tentativa, u.nome AS usuario FROM email_saida e "
           "LEFT JOIN usuario u ON u.id = e.usuario_id")
    args: tuple = ()
    if situacao:
        sql, args = sql + " WHERE e.situacao=?", (situacao,)
    return [dict(r) for r in con.execute(sql + " ORDER BY e.id DESC LIMIT ?", (*args, limite))]


def resumo(con: sqlite3.Connection) -> dict:
    d = {r["situacao"]: r["n"] for r in con.execute("SELECT situacao, COUNT(*) n FROM email_saida GROUP BY situacao")}
    return {"configurado": configurado(), **{k: d.get(k, 0) for k in ("pendente", "enviando", "enviado", "falhou", "cancelado")}}


def reenviar(con: sqlite3.Connection, registrador_id: int, email_id: int) -> None:
    if buscar(con, "usuario", registrador_id)["papel"] != "registrador":
        raise NaoAutorizado("Só a administração reenvia e-mails.")
    e = buscar(con, "email_saida", email_id)
    if e["situacao"] != "falhou":
        raise ErroNegocio("Só e-mails que falharam podem ser reenviados.")
    u = buscar(con, "usuario", e["usuario_id"])
    if not u["email"] or not u["ativo"]:
        raise ErroNegocio("O usuário não tem e-mail ativo. Corrija o cadastro antes.")
    con.execute("UPDATE email_saida SET situacao='pendente', para=?, tentativas=0, proxima_tentativa=?, "
                "ultimo_erro=NULL WHERE id=?", (u["email"], _iso(_agora()), email_id))
    registrar(con, registrador_id, "reenviar_email", "email_saida", email_id)


def definir_preferencia(con: sqlite3.Connection, usuario_id: int, avisos_email: bool) -> None:
    con.execute("UPDATE usuario SET avisos_email=? WHERE id=?", (int(avisos_email), usuario_id))
    if not avisos_email:
        con.execute("UPDATE email_saida SET situacao='cancelado' WHERE usuario_id=? AND situacao='pendente' "
                    "AND tipo IN ('lembrete','alerta')", (usuario_id,))
    registrar(con, usuario_id, "preferencia_email", "usuario", usuario_id, depois={"avisos_email": avisos_email})


def apagar_do_titular(con: sqlite3.Connection, usuario_id: int) -> None:
    """Anonimização: cancela o que não saiu e apaga endereço e conteúdo do que já saiu."""
    con.execute("UPDATE email_saida SET situacao=CASE WHEN situacao IN ('pendente','enviando','falhou') "
                "THEN 'cancelado' ELSE situacao END, para='anonimizado', texto='[anonimizado]' WHERE usuario_id=?",
                (usuario_id,))
