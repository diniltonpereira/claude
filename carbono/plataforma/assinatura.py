"""Assinatura eletrônica avançada gov.br (Lei 14.063/2020, art. 4º, II) dos documentos do projeto.

Fluxo, conforme o manual da API de assinatura do ITI e o roteiro do Login Único:
1. A parte clica em "Assinar com gov.br". Mesmo quem entrou com e-mail e senha passa pelo Login Único:
   o id_token traz o CPF e o nível da conta. Conta bronze é barrada ANTES da tela de autorização do
   ITI, com o link oficial para elevar o nível.
2. O titular da conta autoriza o uso do certificado gov.br (escopo "sign": um token, uma assinatura,
   válido por 10 minutos).
3. A plataforma envia o SHA-256 do PDF para /assinarPKCS7 e recebe o pacote PKCS#7 destacado (.p7s).
4. Antes de guardar, a plataforma confere que o pacote assina exatamente aquele hash, que a assinatura
   confere com a chave do certificado e que o CPF do certificado é o do Login Único.
5. Quando todas as partes assinam, o documento é concluído e a parceria é marcada como assinada.

A validação jurídica completa (cadeia do certificado até a raiz do ITI) é feita no validador oficial:
validar.iti.gov.br (produção) ou validar.staging.iti.br (homologação), com o PDF e os .p7s.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from urllib.parse import urlencode

from . import acesso
from .auditoria import registrar
from .banco import ErroNegocio, NaoAutorizado, buscar, transacao

TIPO = "govbr_avancada"


class ContaBronze(ErroNegocio):
    """Conta gov.br sem nível prata ou ouro: não pode assinar."""


# ---------------------------------------------------------------- configuração
def config_iti() -> dict | None:
    cid, seg, red = (os.environ.get(k) for k in ("ITI_CLIENT_ID", "ITI_CLIENT_SECRET", "ITI_REDIRECT_URI"))
    if not (cid and seg and red):
        return None
    oauth = os.environ.get("ITI_OAUTH_URL", "https://cas.staging.iti.br/oauth2.0").rstrip("/")
    api = os.environ.get("ITI_API_URL", "https://assinatura-api.staging.iti.br/externo/v2").rstrip("/")
    validador = os.environ.get("ITI_VALIDADOR_URL", "https://validar.staging.iti.br" if "staging" in oauth
                               else "https://validar.iti.gov.br")
    return {"client_id": cid, "client_secret": seg, "redirect_uri": red, "authorize": f"{oauth}/authorize",
            "token": f"{oauth}/token", "assinar": f"{api}/assinarPKCS7", "validador": validador}


def disponivel() -> bool:
    return bool(acesso.config_govbr() and config_iti())


# ---------------------------------------------------------------- documentos
def criar_documento(con: sqlite3.Connection, usuario_id: int, projeto_id: int, *, anexo_id: int, titulo: str) -> int:
    """O titular põe o contrato de parceria (PDF) para assinatura das partes."""
    from .anexos import exigir_acesso
    from .projetos import _titular
    p = buscar(con, "projeto", projeto_id)
    _titular(con, usuario_id, p)
    par = con.execute("SELECT * FROM parceria WHERE projeto_id=?", (projeto_id,)).fetchone()
    if not par:
        raise ErroNegocio("Registre a parceria antes de pôr o contrato para assinatura.")
    if par["assinatura"] == "assinado":
        raise ErroNegocio("A parceria já está assinada.")
    exigir_acesso(con, usuario_id, anexo_id)
    a = buscar(con, "anexo", anexo_id)
    if a["tipo_mime"] != "application/pdf":
        raise ErroNegocio("O documento para assinatura deve ser um PDF.")
    with transacao(con):
        aberto = con.execute("SELECT id FROM documento_assinatura WHERE projeto_id=? AND situacao='aguardando'",
                             (projeto_id,)).fetchone()
        if aberto:
            if con.execute("SELECT 1 FROM assinatura_eletronica WHERE documento_id=?", (aberto["id"],)).fetchone():
                raise ErroNegocio("Já há um documento com assinaturas em andamento. Cancele-o antes de enviar outro.")
            con.execute("UPDATE documento_assinatura SET situacao='cancelado' WHERE id=?", (aberto["id"],))
        did = con.execute("INSERT INTO documento_assinatura (projeto_id, titulo, anexo_id, sha256, criado_por) "
                          "VALUES (?,?,?,?,?)", (projeto_id, titulo.strip()[:200], anexo_id, a["sha256"],
                                                 usuario_id)).lastrowid
        con.executemany("INSERT INTO documento_signatario (documento_id, organizacao_id, parte) VALUES (?,?,?)",
                        [(did, p["titular_id"], "titular"), (did, par["desenvolvedor_id"], "desenvolvedor")])
        registrar(con, usuario_id, "documento_para_assinatura", "projeto", projeto_id,
                  depois={"documento_id": did, "sha256": a["sha256"], "titulo": titulo})
    return did


def cancelar_documento(con: sqlite3.Connection, usuario_id: int, documento_id: int) -> None:
    from .projetos import _titular
    d = buscar(con, "documento_assinatura", documento_id)
    _titular(con, usuario_id, buscar(con, "projeto", d["projeto_id"]))
    if d["situacao"] != "aguardando":
        raise ErroNegocio("Só documentos aguardando assinatura podem ser cancelados.")
    con.execute("UPDATE documento_assinatura SET situacao='cancelado' WHERE id=?", (documento_id,))
    registrar(con, usuario_id, "cancelar_documento", "documento_assinatura", documento_id)


def situacao(con: sqlite3.Connection, projeto_id: int, usuario_id: int | None = None) -> dict | None:
    d = con.execute("SELECT * FROM documento_assinatura WHERE projeto_id=? AND situacao <> 'cancelado' "
                    "ORDER BY id DESC LIMIT 1", (projeto_id,)).fetchone()
    if not d:
        return None
    u = buscar(con, "usuario", usuario_id) if usuario_id else None
    partes = []
    for s in con.execute("""SELECT ds.*, o.nome AS organizacao, a.id AS assinatura_id, a.signatario_nome, a.assinado_em,
                                   a.certificado_emissor
                            FROM documento_signatario ds JOIN organizacao o ON o.id = ds.organizacao_id
                            LEFT JOIN assinatura_eletronica a ON a.documento_id = ds.documento_id
                                 AND a.organizacao_id = ds.organizacao_id
                            WHERE ds.documento_id=? ORDER BY ds.parte DESC""", (d["id"],)):
        partes.append({**dict(s), "pode_assinar": bool(u and d["situacao"] == "aguardando" and not s["assinatura_id"]
                                                        and u["organizacao_id"] == s["organizacao_id"]
                                                        and u["papel"] in ("elaborador", "fornecedor"))})
    cfg = config_iti()
    return {**{k: d[k] for k in ("id", "titulo", "anexo_id", "sha256", "situacao", "criado_em", "concluido_em")},
            "partes": partes, "assinatura_disponivel": disponivel(),
            "validador": cfg["validador"] if cfg else "https://validar.iti.gov.br"}


# ---------------------------------------------------------------- fluxo de assinatura
def _parte_do_usuario(con, usuario_id: int, documento_id: int) -> sqlite3.Row:
    d = buscar(con, "documento_assinatura", documento_id)
    if d["situacao"] != "aguardando":
        raise ErroNegocio("Este documento não está aguardando assinaturas.")
    u = buscar(con, "usuario", usuario_id)
    s = con.execute("SELECT * FROM documento_signatario WHERE documento_id=? AND organizacao_id=?",
                    (documento_id, u["organizacao_id"])).fetchone()
    if not s or u["papel"] not in ("elaborador", "fornecedor"):
        raise NaoAutorizado("Você não representa nenhuma das partes deste documento.")
    if con.execute("SELECT 1 FROM assinatura_eletronica WHERE documento_id=? AND organizacao_id=?",
                   (documento_id, u["organizacao_id"])).fetchone():
        raise ErroNegocio("Sua organização já assinou este documento.")
    return s


def iniciar(con: sqlite3.Connection, usuario_id: int, documento_id: int) -> str:
    """Passo 1: identidade e nível da conta no Login Único (obrigatório antes da autorização do ITI)."""
    if not disponivel():
        raise ErroNegocio("A assinatura gov.br ainda não foi configurada nesta instalação (credenciais do órgão).")
    _parte_do_usuario(con, usuario_id, documento_id)
    return acesso.govbr_url(con, "assinatura_identidade", usuario_id, {"documento_id": documento_id})


def apos_identidade(con: sqlite3.Connection, usuario_id: int, code: str, state: str, cliente=None) -> str:
    """Passo 2: confere a conta e encaminha para a autorização do certificado no ITI."""
    e, info = acesso.govbr_trocar_codigo(con, code, state, "assinatura_identidade", cliente)
    if e["usuario_id"] != usuario_id:
        raise NaoAutorizado("Este pedido de assinatura foi iniciado por outra pessoa.")
    documento_id = json.loads(e["dados"])["documento_id"]
    _parte_do_usuario(con, usuario_id, documento_id)
    if info["nivel"] not in acesso.NIVEIS_ASSINATURA:
        registrar(con, usuario_id, "assinatura_negada_bronze", "documento_assinatura", documento_id)
        raise ContaBronze("É necessário possuir conta gov.br nível prata ou ouro para utilizar o serviço de assinatura.")
    u = buscar(con, "usuario", usuario_id)
    cpf_h = acesso.cpf_hmac(info["cpf"])
    if u["cpf_hmac"] and u["cpf_hmac"] != cpf_h:
        raise NaoAutorizado("A conta gov.br usada não é a do CPF cadastrado para você nesta plataforma.")
    cfg = config_iti()
    state, nonce, _ = acesso.novo_estado(con, "assinatura_iti", usuario_id,
                                         {"documento_id": documento_id, "cpf_hmac": cpf_h, "nome": info["nome"]})
    return cfg["authorize"] + "?" + urlencode({
        "response_type": "code", "client_id": cfg["client_id"], "scope": "sign govbr",
        "redirect_uri": cfg["redirect_uri"], "state": state, "nonce": nonce})


def apos_autorizacao(con: sqlite3.Connection, usuario_id: int, code: str, state: str, cliente=None) -> dict:
    """Passo 3: token do ITI, assinatura do hash, conferência e registro."""
    import httpx
    e = acesso.consumir_estado(con, state, "assinatura_iti")
    if e["usuario_id"] != usuario_id:
        raise NaoAutorizado("Este pedido de assinatura foi iniciado por outra pessoa.")
    dados = json.loads(e["dados"])
    documento_id = dados["documento_id"]
    s = _parte_do_usuario(con, usuario_id, documento_id)
    d = buscar(con, "documento_assinatura", documento_id)
    cfg = config_iti()
    http = cliente or httpx.Client(timeout=30)
    r = http.post(cfg["token"], data={"code": code, "client_id": cfg["client_id"], "grant_type": "authorization_code",
                                      "client_secret": cfg["client_secret"], "redirect_uri": cfg["redirect_uri"]})
    if r.status_code != 200 or not r.json().get("access_token"):
        raise NaoAutorizado("O ITI não autorizou o uso do certificado. Tente de novo.")
    digest = bytes.fromhex(d["sha256"])
    r = http.post(cfg["assinar"], json={"hashBase64": base64.b64encode(digest).decode()},
                  headers={"Authorization": f"Bearer {r.json()['access_token']}"})
    if r.status_code == 403:
        raise ContaBronze(r.text.strip()[:300] or "O ITI recusou a assinatura para esta conta.")
    if r.status_code != 200 or not r.content:
        raise ErroNegocio("O serviço de assinatura do ITI não respondeu. Tente de novo em instantes.")
    cert = verificar_p7s(r.content, digest)
    if cert["cpf"] and acesso.cpf_hmac(cert["cpf"]) != dados["cpf_hmac"]:
        raise NaoAutorizado("O certificado devolvido não é do CPF autenticado no gov.br.")
    with transacao(con):
        u = buscar(con, "usuario", usuario_id)
        if not u["cpf_hmac"]:  # primeira assinatura vincula o CPF ao usuário (as próximas precisam bater)
            con.execute("UPDATE usuario SET cpf_hmac=? WHERE id=?", (dados["cpf_hmac"], usuario_id))
        aid = con.execute("""INSERT INTO assinatura_eletronica (documento_id, organizacao_id, usuario_id, tipo, p7s,
                             p7s_sha256, signatario_nome, certificado_serial, certificado_emissor)
                             VALUES (?,?,?,?,?,?,?,?,?)""",
                          (documento_id, s["organizacao_id"], usuario_id, TIPO, r.content,
                           hashlib.sha256(r.content).hexdigest(), cert["nome"] or dados["nome"], cert["serial"],
                           cert["emissor"])).lastrowid
        registrar(con, usuario_id, "assinar", "documento_assinatura", documento_id,
                  depois={"assinatura_id": aid, "parte": s["parte"], "certificado_serial": cert["serial"],
                          "p7s_sha256": hashlib.sha256(r.content).hexdigest()})
        concluido = _concluir_se_completo(con, d, usuario_id)
    return {"documento_id": documento_id, "projeto_id": d["projeto_id"], "concluido": concluido}


def _concluir_se_completo(con, d: sqlite3.Row, usuario_id: int) -> bool:
    faltam = con.execute("""SELECT COUNT(*) FROM documento_signatario ds WHERE ds.documento_id=? AND NOT EXISTS
                            (SELECT 1 FROM assinatura_eletronica a WHERE a.documento_id=ds.documento_id
                             AND a.organizacao_id=ds.organizacao_id)""", (d["id"],)).fetchone()[0]
    if faltam:
        return False
    agora = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    con.execute("UPDATE documento_assinatura SET situacao='concluido', concluido_em=? WHERE id=?", (agora, d["id"]))
    con.execute("UPDATE parceria SET assinatura='assinado', assinatura_ref=? WHERE projeto_id=?",
                (f"Assinatura eletrônica avançada gov.br — documento nº {d['id']}, SHA-256 {d['sha256'][:16]}…",
                 d["projeto_id"]))
    registrar(con, usuario_id, "assinatura_concluida", "projeto", d["projeto_id"], depois={"documento_id": d["id"]})
    return True


def p7s(con: sqlite3.Connection, usuario_id: int, assinatura_id: int) -> tuple[bytes, str]:
    from .projetos import ver
    a = buscar(con, "assinatura_eletronica", assinatura_id)
    d = buscar(con, "documento_assinatura", a["documento_id"])
    ver(con, usuario_id, d["projeto_id"])  # mesmo controle de acesso do projeto
    return a["p7s"], f"documento-{d['id']}-assinatura-{assinatura_id}.p7s"


# ---------------------------------------------------------------- conferência do PKCS#7
def verificar_p7s(conteudo: bytes, digest: bytes) -> dict:
    """Confere o pacote CMS/PKCS#7 destacado: o atributo messageDigest é o hash enviado e a assinatura
    confere com a chave pública do certificado do signatário. Devolve dados do certificado.
    Não valida a cadeia até a raiz do ITI (isso é feito no validador oficial)."""
    from asn1crypto import cms
    from cryptography import x509
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
    try:
        info = cms.ContentInfo.load(conteudo)
        sd = info["content"]
        signer = sd["signer_infos"][0]
        if signer["digest_algorithm"]["algorithm"].native != "sha256":
            raise ErroNegocio("Assinatura com algoritmo de resumo diferente de SHA-256.")
        attrs = signer["signed_attrs"]
        md = next(a["values"][0].native for a in attrs if a["type"].native == "message_digest")
        certs = [c.chosen for c in sd["certificates"]]
        if signer["sid"].name == "issuer_and_serial_number":
            serial = signer["sid"].chosen["serial_number"].native
            cert_asn1 = next(c for c in certs if c.serial_number == serial)
        else:
            cert_asn1 = certs[0]
        assinado = b"\x31" + attrs.dump()[1:]           # [0] IMPLICIT -> SET OF, como foi assinado
        assinatura = signer["signature"].native
    except ErroNegocio:
        raise
    except Exception as exc:
        raise ErroNegocio("O pacote de assinatura devolvido é inválido.") from exc
    if md != digest:
        raise ErroNegocio("A assinatura devolvida não corresponde a este documento.")
    cert = x509.load_der_x509_certificate(cert_asn1.dump())
    chave = cert.public_key()
    try:
        if isinstance(chave, rsa.RSAPublicKey):
            chave.verify(assinatura, assinado, padding.PKCS1v15(), hashes.SHA256())
        elif isinstance(chave, ec.EllipticCurvePublicKey):
            chave.verify(assinatura, assinado, ec.ECDSA(hashes.SHA256()))
        else:
            raise ErroNegocio("Tipo de chave do certificado não suportado.")
    except InvalidSignature as exc:
        raise ErroNegocio("A assinatura devolvida não confere com o certificado.") from exc
    agora = datetime.now(timezone.utc)
    if not (cert.not_valid_before_utc <= agora <= cert.not_valid_after_utc):
        raise ErroNegocio("O certificado da assinatura está fora da validade.")
    nome_cn = next((a.value for a in cert.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)), "")
    cpf = re.search(r"(?<!\d)(\d{11})(?!\d)", cert.subject.rfc4514_string())
    emissor = next((a.value for a in cert.issuer.get_attributes_for_oid(x509.NameOID.COMMON_NAME)), cert.issuer.rfc4514_string())
    return {"nome": re.sub(r"[:\s]*\d{11}$", "", str(nome_cn)).strip(), "cpf": cpf.group(1) if cpf else None,
            "serial": format(cert.serial_number, "x"), "emissor": str(emissor)[:200]}
