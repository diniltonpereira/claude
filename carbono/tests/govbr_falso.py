"""gov.br (Login Único) e ITI (API de assinatura) simulados, conforme os formatos dos manuais oficiais.

- Login Único: /token devolve access_token e id_token RS256 com sub/preferred_username (CPF), nonce e
  reliability_info.level; /jwk publica a chave.
- ITI: /oauth2.0/token devolve access_token de uso único (escopo "sign"); /assinarPKCS7 recebe
  {"hashBase64"} e devolve um PKCS#7 destacado (SHA256-RSA) com o certificado do cidadão.
"""
from __future__ import annotations

import base64
import json
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs

import httpx
import jwt
from asn1crypto import algos, cms, core, x509 as ax509
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import NameOID

SSO = "https://sso.staging.acesso.gov.br"
CHAVE_SSO = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def certificado(nome: str, cpf: str, chave, validade_dias: int = 365) -> x509.Certificate:
    """Certificado no estilo gov.br: CN "NOME:CPF", emitido por uma AC de teste."""
    agora = datetime.now(timezone.utc)
    return (x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"{nome}:{cpf}")]))
            .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "AC Final do Governo Federal do Brasil v1 (teste)")]))
            .public_key(chave.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(agora - timedelta(days=1)).not_valid_after(agora + timedelta(days=validade_dias))
            .sign(CHAVE_SSO, hashes.SHA256()))


def pkcs7_do_hash(digest: bytes, chave, cert: x509.Certificate) -> bytes:
    """Monta o CMS SignedData destacado que o ITI devolve: atributos assinados com o messageDigest."""
    cert_asn1 = ax509.Certificate.load(cert.public_bytes(serialization.Encoding.DER))
    attrs = cms.CMSAttributes([
        cms.CMSAttribute({"type": "content_type", "values": ["data"]}),
        cms.CMSAttribute({"type": "message_digest", "values": [digest]}),
        cms.CMSAttribute({"type": "signing_time", "values": [cms.Time({"utc_time": core.UTCTime(datetime.now(timezone.utc))})]}),
    ])
    assinatura = chave.sign(attrs.dump(), padding.PKCS1v15(), hashes.SHA256())
    signer = cms.SignerInfo({
        "version": "v1",
        "sid": cms.SignerIdentifier({"issuer_and_serial_number": cms.IssuerAndSerialNumber(
            {"issuer": cert_asn1.issuer, "serial_number": cert_asn1.serial_number})}),
        "digest_algorithm": algos.DigestAlgorithm({"algorithm": "sha256"}),
        "signed_attrs": attrs,
        "signature_algorithm": algos.SignedDigestAlgorithm({"algorithm": "sha256_rsa"}),
        "signature": assinatura,
    })
    sd = cms.SignedData({
        "version": "v1", "digest_algorithms": [algos.DigestAlgorithm({"algorithm": "sha256"})],
        "encap_content_info": {"content_type": "data"},
        "certificates": [cms.CertificateChoices({"certificate": cert_asn1})], "signer_infos": [signer],
    })
    return cms.ContentInfo({"content_type": "signed_data", "content": sd}).dump()


class GovBrFalso:
    """Transporte httpx que responde como o Login Único e a API do ITI."""

    def __init__(self, cpf="12345678909", nome="Maria da Silva", nivel="silver", client_id="cid"):
        self.cpf, self.nome, self.nivel, self.client_id = cpf, nome, nivel, client_id
        self.chave_cidadao = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.cert = certificado(nome.upper(), cpf, self.chave_cidadao)
        self.nonce_por_code: dict[str, str] = {}
        self.tokens_iti: set[str] = set()
        self.chamadas: list[tuple[str, str]] = []
        self.adulterar_hash = False
        self.cliente = httpx.Client(transport=httpx.MockTransport(self._responder))

    def autorizar(self, url_authorize: str) -> tuple[str, str]:
        """Simula o navegador passando pelo authorize: devolve (code, state)."""
        q = parse_qs(url_authorize.split("?", 1)[1])
        code = f"code-{len(self.nonce_por_code)}"
        self.nonce_por_code[code] = q.get("nonce", [""])[0]
        return code, q["state"][0]

    def _responder(self, req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        self.chamadas.append((req.method, url.split("?")[0]))
        if url.startswith(f"{SSO}/jwk"):
            pub = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(CHAVE_SSO.public_key()))
            return httpx.Response(200, json={"keys": [{**pub, "kid": "rsa1", "alg": "RS256"}]})
        if url.startswith(f"{SSO}/token"):
            code = parse_qs(req.content.decode())["code"][0]
            agora = int(time.time())
            id_token = jwt.encode({"sub": self.cpf, "preferred_username": self.cpf, "name": self.nome,
                                   "iss": f"{SSO}/", "aud": self.client_id, "iat": agora, "exp": agora + 60,
                                   "nonce": self.nonce_por_code.pop(code, ""),
                                   "reliability_info": {"level": self.nivel, "reliabilities": []}},
                                  CHAVE_SSO, algorithm="RS256", headers={"kid": "rsa1"})
            return httpx.Response(200, json={"access_token": "at-sso", "token_type": "Bearer", "expires_in": 3599,
                                             "id_token": id_token})
        if "/oauth2.0/token" in url:
            tok = f"AT-{len(self.tokens_iti) + 1}"
            self.tokens_iti.add(tok)
            return httpx.Response(200, json={"access_token": tok, "token_type": "bearer", "expires_in": 600})
        if url.endswith("/assinarPKCS7"):
            tok = req.headers.get("authorization", "").removeprefix("Bearer ")
            if tok not in self.tokens_iti:
                return httpx.Response(401, text="token inválido ou já usado")
            self.tokens_iti.discard(tok)  # escopo "sign": uma assinatura por token
            if self.nivel == "bronze":
                return httpx.Response(403, text="Cidadão não possui a identidade (Prata ou Ouro) necessária para uso "
                                                "da assinatura eletrônica digital.")
            digest = base64.b64decode(json.loads(req.content)["hashBase64"])
            if self.adulterar_hash:
                digest = bytes(32)
            return httpx.Response(200, content=pkcs7_do_hash(digest, self.chave_cidadao, self.cert))
        return httpx.Response(404)
