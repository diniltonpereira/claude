"""Acesso em produção: gov.br (OIDC), e-mail + senha + TOTP, sessões, CSRF, convites e LGPD.

Decisões:
- Senha com scrypt (biblioteca padrão), mínimo de 12 caracteres. Bloqueio de 15 minutos após 5 falhas.
- TOTP (RFC 6238, 30 s, 6 dígitos), obrigatório para a administração (registrador); o mesmo código
  não é aceito duas vezes.
- Sessão: token aleatório num cookie HttpOnly/Secure/SameSite=Lax; no banco fica só o SHA-256.
  Validade de 8 horas, encerrada após 1 hora sem uso. Todo pedido que altera dados exige o cabeçalho
  X-CSRF-Token igual ao da sessão.
- gov.br: fluxo authorization code com PKCE e state/nonce; o CPF (claim `sub`) vem do endpoint
  userinfo pela conexão TLS e é comparado por HMAC: o sistema nunca guarda o CPF em claro.
  O servidor precisa ter sido cadastrado antes pela administração (sem autocadastro).
- Convite: link de uso único, válido por 72 horas, para o usuário definir a senha.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import sqlite3
import struct
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from .auditoria import registrar
from .banco import ErroNegocio, NaoAutorizado, NaoEncontrado, buscar

SESSAO_HORAS = 8
SESSAO_OCIOSA_MIN = 60
MAX_FALHAS = 5
BLOQUEIO_MIN = 15
CONVITE_HORAS = 72
TERMOS_VERSAO = os.environ.get("CARBONO_TERMOS_VERSAO", "2026-1")


class SessaoInvalida(Exception):
    """Sessão ausente, expirada ou revogada (HTTP 401)."""


def _agora() -> datetime:
    return datetime.now(timezone.utc)


def _iso(d: datetime) -> str:
    return d.isoformat(timespec="seconds")


def _sha(texto: str) -> str:
    return hashlib.sha256(texto.encode()).hexdigest()


def segredo_servidor() -> bytes:
    s = os.environ.get("CARBONO_SEGREDO", "")
    if len(s) < 32:
        raise RuntimeError("Defina CARBONO_SEGREDO com pelo menos 32 caracteres aleatórios (modo produção).")
    return s.encode()


def cpf_hmac(cpf: str) -> str:
    digitos = "".join(ch for ch in cpf if ch.isdigit())
    if len(digitos) != 11:
        raise ErroNegocio("CPF deve ter 11 dígitos.")
    return hmac.new(segredo_servidor(), digitos.encode(), hashlib.sha256).hexdigest()


# ---------------------------------------------------------------- senha
def gerar_hash_senha(senha: str) -> str:
    if len(senha) < 12:
        raise ErroNegocio("A senha deve ter pelo menos 12 caracteres.")
    sal = secrets.token_bytes(16)
    h = hashlib.scrypt(senha.encode(), salt=sal, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt$16384$8$1${sal.hex()}${h.hex()}"


def conferir_senha(senha: str, armazenado: str | None) -> bool:
    if not armazenado:
        return False
    try:
        _, n, r, p, sal, h = armazenado.split("$")
        calc = hashlib.scrypt(senha.encode(), salt=bytes.fromhex(sal), n=int(n), r=int(r), p=int(p), dklen=32)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(calc.hex(), h)


# ---------------------------------------------------------------- TOTP (RFC 6238)
def novo_segredo_totp() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _hotp(segredo: str, contador: int) -> str:
    chave = base64.b32decode(segredo + "=" * (-len(segredo) % 8))
    d = hmac.new(chave, struct.pack(">Q", contador), hashlib.sha1).digest()
    o = d[-1] & 0x0F
    return f"{(struct.unpack('>I', d[o:o + 4])[0] & 0x7FFFFFFF) % 1_000_000:06d}"


def codigo_totp(segredo: str, instante: float | None = None) -> str:
    return _hotp(segredo, int((instante if instante is not None else time.time()) // 30))


def _conferir_totp(con, u: sqlite3.Row, codigo: str, instante: float | None = None) -> bool:
    if not u["totp_segredo"] or not codigo or not codigo.isdigit():
        return False
    passo = int((instante if instante is not None else time.time()) // 30)
    for p in (passo - 1, passo, passo + 1):
        if hmac.compare_digest(_hotp(u["totp_segredo"], p), codigo):
            if u["totp_ultimo_passo"] is not None and p <= u["totp_ultimo_passo"]:
                return False  # código já usado
            con.execute("UPDATE usuario SET totp_ultimo_passo=? WHERE id=?", (p, u["id"]))
            return True
    return False


def uri_totp(segredo: str, email: str, emissor: str = "CarbonMG") -> str:
    return f"otpauth://totp/{emissor}:{email}?" + urlencode({"secret": segredo, "issuer": emissor})


# ---------------------------------------------------------------- sessões
def _criar_sessao(con, usuario_id: int, ip: str | None, pendente_2fa: bool = False) -> tuple[str, str]:
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
    agora = _agora()
    validade = timedelta(minutes=5) if pendente_2fa else timedelta(hours=SESSAO_HORAS)
    con.execute("""INSERT INTO sessao (token_hash, usuario_id, csrf, pendente_2fa, criada_em, expira_em, ultimo_uso, ip)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (_sha(token), usuario_id, csrf, int(pendente_2fa), _iso(agora), _iso(agora + validade), _iso(agora), ip))
    return token, csrf


def sessao_por_token(con: sqlite3.Connection, token: str | None) -> sqlite3.Row:
    if not token:
        raise SessaoInvalida("Sessão ausente.")
    s = con.execute("SELECT * FROM sessao WHERE token_hash=?", (_sha(token),)).fetchone()
    agora = _agora()
    if s is None or s["pendente_2fa"]:
        raise SessaoInvalida("Sessão inválida.")
    if datetime.fromisoformat(s["expira_em"]) < agora or \
            datetime.fromisoformat(s["ultimo_uso"]) + timedelta(minutes=SESSAO_OCIOSA_MIN) < agora:
        con.execute("DELETE FROM sessao WHERE id=?", (s["id"],))
        raise SessaoInvalida("Sessão expirada. Entre novamente.")
    u = buscar(con, "usuario", s["usuario_id"])
    if not u["ativo"]:
        raise SessaoInvalida("Usuário desativado.")
    con.execute("UPDATE sessao SET ultimo_uso=? WHERE id=?", (_iso(agora), s["id"]))
    return s


def encerrar(con: sqlite3.Connection, token: str | None) -> None:
    if token:
        con.execute("DELETE FROM sessao WHERE token_hash=?", (_sha(token),))


# ---------------------------------------------------------------- login por senha
def _bloqueado(u) -> bool:
    return bool(u["bloqueado_ate"]) and datetime.fromisoformat(u["bloqueado_ate"]) > _agora()


def login_senha(con: sqlite3.Connection, email: str, senha: str, ip: str | None = None) -> dict:
    """Primeiro fator. Se o usuário tem TOTP, devolve um desafio de 5 minutos para o segundo fator."""
    u = con.execute("SELECT * FROM usuario WHERE email=? AND ativo=1", (email.strip().lower(),)).fetchone()
    erro = NaoAutorizado("E-mail ou senha incorretos.")
    if u is None:
        conferir_senha(senha, "scrypt$16384$8$1$00$00")  # tempo semelhante, sem revelar se o e-mail existe
        raise erro
    if _bloqueado(u):
        raise NaoAutorizado(f"Acesso bloqueado por excesso de tentativas. Tente após {BLOQUEIO_MIN} minutos.")
    if not conferir_senha(senha, u["senha_hash"]):
        falhas = u["tentativas_falhas"] + 1
        bloqueio = _iso(_agora() + timedelta(minutes=BLOQUEIO_MIN)) if falhas >= MAX_FALHAS else None
        con.execute("UPDATE usuario SET tentativas_falhas=?, bloqueado_ate=? WHERE id=?",
                    (0 if bloqueio else falhas, bloqueio, u["id"]))
        registrar(con, u["id"], "login_falhou", "usuario", u["id"], depois={"bloqueado": bool(bloqueio)})
        raise erro
    con.execute("UPDATE usuario SET tentativas_falhas=0, bloqueado_ate=NULL WHERE id=?", (u["id"],))
    if u["papel"] == "registrador" and not u["totp_ativo"]:
        raise NaoAutorizado("A administração exige verificação em duas etapas. Configure-a pelo comando "
                            "'python -m plataforma criar-admin'.")
    if u["totp_ativo"]:
        desafio, _ = _criar_sessao(con, u["id"], ip, pendente_2fa=True)
        return {"segundo_fator": True, "desafio": desafio}
    token, csrf = _criar_sessao(con, u["id"], ip)
    registrar(con, u["id"], "login", "usuario", u["id"], depois={"metodo": "senha"})
    return {"segundo_fator": False, "token": token, "csrf": csrf}


def login_segundo_fator(con: sqlite3.Connection, desafio: str, codigo: str, ip: str | None = None) -> dict:
    s = con.execute("SELECT * FROM sessao WHERE token_hash=? AND pendente_2fa=1", (_sha(desafio),)).fetchone()
    if s is None or datetime.fromisoformat(s["expira_em"]) < _agora():
        raise NaoAutorizado("Desafio expirado. Entre novamente.")
    u = buscar(con, "usuario", s["usuario_id"])
    if not _conferir_totp(con, u, codigo.strip()):
        falhas = u["tentativas_falhas"] + 1
        bloqueio = _iso(_agora() + timedelta(minutes=BLOQUEIO_MIN)) if falhas >= MAX_FALHAS else None
        con.execute("UPDATE usuario SET tentativas_falhas=?, bloqueado_ate=? WHERE id=?",
                    (0 if bloqueio else falhas, bloqueio, u["id"]))
        if bloqueio:
            con.execute("DELETE FROM sessao WHERE id=?", (s["id"],))
        raise NaoAutorizado("Código inválido.")
    con.execute("DELETE FROM sessao WHERE id=?", (s["id"],))
    con.execute("UPDATE usuario SET tentativas_falhas=0 WHERE id=?", (u["id"],))
    token, csrf = _criar_sessao(con, u["id"], ip)
    registrar(con, u["id"], "login", "usuario", u["id"], depois={"metodo": "senha+totp"})
    return {"token": token, "csrf": csrf}


# ---------------------------------------------------------------- TOTP: cadastro pelo próprio usuário
def iniciar_totp(con: sqlite3.Connection, usuario_id: int) -> dict:
    u = buscar(con, "usuario", usuario_id)
    if u["totp_ativo"]:
        raise ErroNegocio("A verificação em duas etapas já está ativa.")
    seg = novo_segredo_totp()
    con.execute("UPDATE usuario SET totp_segredo=? WHERE id=?", (seg, usuario_id))
    return {"segredo": seg, "uri": uri_totp(seg, u["email"] or f"usuario{usuario_id}")}


def confirmar_totp(con: sqlite3.Connection, usuario_id: int, codigo: str) -> None:
    u = buscar(con, "usuario", usuario_id)
    if not _conferir_totp(con, u, codigo.strip()):
        raise ErroNegocio("Código inválido. Confira o relógio do celular e tente de novo.")
    con.execute("UPDATE usuario SET totp_ativo=1 WHERE id=?", (usuario_id,))
    registrar(con, usuario_id, "ativar_2fa", "usuario", usuario_id)


# ---------------------------------------------------------------- convites
def gerar_convite(con: sqlite3.Connection, registrador_id: int, usuario_id: int) -> str:
    if buscar(con, "usuario", registrador_id)["papel"] != "registrador":
        raise NaoAutorizado("Só a administração gera convites.")
    u = buscar(con, "usuario", usuario_id)
    if not u["email"]:
        raise ErroNegocio("Cadastre o e-mail do usuário antes de gerar o convite.")
    token = secrets.token_urlsafe(32)
    con.execute("INSERT INTO convite (token_hash, usuario_id, expira_em) VALUES (?,?,?)",
                (_sha(token), usuario_id, _iso(_agora() + timedelta(hours=CONVITE_HORAS))))
    registrar(con, registrador_id, "gerar_convite", "usuario", usuario_id)
    return token


def definir_senha(con: sqlite3.Connection, token: str, senha: str) -> None:
    c = con.execute("SELECT * FROM convite WHERE token_hash=?", (_sha(token),)).fetchone()
    if c is None or c["usado_em"] or datetime.fromisoformat(c["expira_em"]) < _agora():
        raise NaoAutorizado("Convite inválido, usado ou expirado. Peça outro à administração.")
    con.execute("UPDATE usuario SET senha_hash=?, tentativas_falhas=0, bloqueado_ate=NULL WHERE id=?",
                (gerar_hash_senha(senha), c["usuario_id"]))
    con.execute("UPDATE convite SET usado_em=? WHERE id=?", (_iso(_agora()), c["id"]))
    con.execute("DELETE FROM sessao WHERE usuario_id=?", (c["usuario_id"],))
    registrar(con, c["usuario_id"], "definir_senha", "usuario", c["usuario_id"])


# ---------------------------------------------------------------- gov.br (OpenID Connect)
def config_govbr() -> dict | None:
    cid, seg, red = (os.environ.get(k) for k in ("GOVBR_CLIENT_ID", "GOVBR_CLIENT_SECRET", "GOVBR_REDIRECT_URI"))
    if not (cid and seg and red):
        return None
    base = os.environ.get("GOVBR_URL", "https://sso.staging.acesso.gov.br").rstrip("/")
    return {"client_id": cid, "client_secret": seg, "redirect_uri": red, "authorize": f"{base}/authorize",
            "token": f"{base}/token", "userinfo": f"{base}/userinfo/"}


def govbr_url_login(con: sqlite3.Connection) -> str:
    cfg = config_govbr()
    if not cfg:
        raise ErroNegocio("Login gov.br não configurado.")
    state, nonce, verificador = (secrets.token_urlsafe(32) for _ in range(3))
    desafio = base64.urlsafe_b64encode(hashlib.sha256(verificador.encode()).digest()).decode().rstrip("=")
    con.execute("DELETE FROM oidc_estado WHERE criado_em < ?", (_iso(_agora() - timedelta(minutes=10)),))
    con.execute("INSERT INTO oidc_estado (state, nonce, verificador, criado_em) VALUES (?,?,?,?)",
                (state, nonce, verificador, _iso(_agora())))
    return cfg["authorize"] + "?" + urlencode({
        "response_type": "code", "client_id": cfg["client_id"], "scope": "openid email profile",
        "redirect_uri": cfg["redirect_uri"], "state": state, "nonce": nonce,
        "code_challenge": desafio, "code_challenge_method": "S256"})


def govbr_callback(con: sqlite3.Connection, code: str, state: str, ip: str | None = None, cliente=None) -> dict:
    """Troca o código pelo token, lê o CPF no userinfo e abre a sessão do servidor já cadastrado."""
    import httpx
    cfg = config_govbr()
    if not cfg:
        raise ErroNegocio("Login gov.br não configurado.")
    e = con.execute("SELECT * FROM oidc_estado WHERE state=?", (state,)).fetchone()
    con.execute("DELETE FROM oidc_estado WHERE state=?", (state,))
    if e is None or datetime.fromisoformat(e["criado_em"]) < _agora() - timedelta(minutes=10):
        raise NaoAutorizado("Login expirado ou inválido. Tente de novo.")
    http = cliente or httpx.Client(timeout=15)
    r = http.post(cfg["token"], data={"grant_type": "authorization_code", "code": code,
                                      "redirect_uri": cfg["redirect_uri"], "code_verifier": e["verificador"]},
                  auth=(cfg["client_id"], cfg["client_secret"]))
    if r.status_code != 200:
        raise NaoAutorizado("O gov.br recusou a autenticação.")
    acesso = r.json().get("access_token")
    info = http.get(cfg["userinfo"], headers={"Authorization": f"Bearer {acesso}"})
    if info.status_code != 200 or not info.json().get("sub"):
        raise NaoAutorizado("Não foi possível obter a identificação no gov.br.")
    u = con.execute("SELECT * FROM usuario WHERE cpf_hmac=? AND ativo=1", (cpf_hmac(info.json()["sub"]),)).fetchone()
    if u is None:
        raise NaoAutorizado("CPF não cadastrado nesta plataforma. Peça o cadastro à administração do órgão.")
    token, csrf = _criar_sessao(con, u["id"], ip)
    registrar(con, u["id"], "login", "usuario", u["id"], depois={"metodo": "govbr"})
    return {"token": token, "csrf": csrf}


# ---------------------------------------------------------------- LGPD
def aceitar_termos(con: sqlite3.Connection, usuario_id: int, versao: str) -> None:
    if versao != TERMOS_VERSAO:
        raise ErroNegocio("Versão dos termos desatualizada. Recarregue a página.")
    con.execute("UPDATE usuario SET termos_versao=?, termos_aceitos_em=? WHERE id=?",
                (versao, _iso(_agora()), usuario_id))
    registrar(con, usuario_id, "aceitar_termos", "usuario", usuario_id, depois={"versao": versao})


def termos_pendentes(u: sqlite3.Row) -> bool:
    return u["termos_versao"] != TERMOS_VERSAO


def meus_dados(con: sqlite3.Connection, usuario_id: int) -> dict:
    """Dados pessoais do titular (LGPD, art. 18, II e V). O CPF não é exportado porque não é guardado."""
    u = buscar(con, "usuario", usuario_id)
    return {
        "usuario": {k: u[k] for k in ("id", "nome", "email", "papel", "organizacao_id", "totp_ativo",
                                      "termos_versao", "termos_aceitos_em")},
        "cpf": "não armazenado (apenas código HMAC para conferência no login gov.br)" if u["cpf_hmac"] else None,
        "sessoes": [dict(r) for r in con.execute(
            "SELECT criada_em, ultimo_uso, expira_em, ip FROM sessao WHERE usuario_id=? AND pendente_2fa=0",
            (usuario_id,))],
        "registros_de_acesso": [dict(r) for r in con.execute(
            "SELECT em, ip, acao FROM auditoria WHERE usuario_id=? AND entidade='usuario' ORDER BY id DESC LIMIT 100",
            (usuario_id,))],
    }


def anonimizar(con: sqlite3.Connection, registrador_id: int, usuario_id: int) -> None:
    """Remove dados pessoais (LGPD, art. 18, IV e VI). Os atos praticados continuam na trilha de auditoria
    sob o id do usuário, pois a guarda decorre de obrigação legal (LGPD, art. 16, I)."""
    if buscar(con, "usuario", registrador_id)["papel"] != "registrador":
        raise NaoAutorizado("Só a administração anonimiza usuários.")
    if registrador_id == usuario_id:
        raise ErroNegocio("Não é possível anonimizar a si mesmo.")
    buscar(con, "usuario", usuario_id)
    con.execute("""UPDATE usuario SET nome=?, email=NULL, cpf_hmac=NULL, senha_hash=NULL, totp_segredo=NULL,
                   totp_ativo=0, ativo=0, anonimizado_em=? WHERE id=?""",
                (f"Usuário anonimizado nº {usuario_id}", _iso(_agora()), usuario_id))
    con.execute("DELETE FROM sessao WHERE usuario_id=?", (usuario_id,))
    con.execute("DELETE FROM convite WHERE usuario_id=?", (usuario_id,))
    registrar(con, registrador_id, "anonimizar", "usuario", usuario_id)


def politica_privacidade() -> dict:
    return {
        "versao_termos": TERMOS_VERSAO,
        "controlador": os.environ.get("CARBONO_CONTROLADOR", "Órgão público contratante (definir no deploy)"),
        "encarregado": os.environ.get("CARBONO_ENCARREGADO", "Definir o encarregado (DPO) e o canal de contato"),
        "finalidades": [
            "Identificar quem lança, verifica e aprova informações (controle interno e prestação de contas).",
            "Cumprir obrigações legais de transparência e de guarda de documentos públicos.",
        ],
        "dados": ["nome", "e-mail", "papel e organização", "CPF apenas como código HMAC (login gov.br)",
                  "endereço IP e data dos atos (trilha de auditoria)"],
        "bases_legais": ["LGPD, art. 7º, II e III, e art. 23 (execução de políticas públicas e obrigação legal)"],
        "direitos": "Acesso e exportação em 'Minha conta'; correção e anonimização pela administração "
                    "(LGPD, art. 18). Atos já praticados permanecem na trilha de auditoria (art. 16, I).",
        "compartilhamento": "Não há compartilhamento com terceiros além das publicações exigidas pela Lei 12.527/2011.",
    }
