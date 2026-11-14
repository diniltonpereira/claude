"""Anexos de comprovação (NF, fatura, laudo, foto, planilha).

O arquivo é gravado fora do banco, num diretório configurável (CARBONO_ANEXOS), com o nome
igual ao seu SHA-256: o mesmo conteúdo nunca é duplicado e qualquer alteração no disco é
detectável comparando o hash. Em produção, troque o diretório por armazenamento S3 compatível
com criptografia em repouso, mantendo a mesma interface (salvar/abrir).
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
from pathlib import Path

from .auditoria import registrar
from .banco import ErroNegocio, NaoAutorizado, buscar

TAMANHO_MAXIMO = 10 * 1024 * 1024  # 10 MB
TIPOS_ACEITOS = {
    "application/pdf": b"%PDF",
    "image/jpeg": b"\xff\xd8\xff",
    "image/png": b"\x89PNG",
    "text/csv": None,
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": b"PK",
}


def diretorio() -> Path:
    d = Path(os.environ.get("CARBONO_ANEXOS", "anexos"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def salvar(con: sqlite3.Connection, usuario_id: int, nome: str, tipo_mime: str, conteudo: bytes) -> dict:
    u = buscar(con, "usuario", usuario_id)
    if u["papel"] == "auditor":
        raise NaoAutorizado("Auditor tem acesso somente de leitura.")
    if not conteudo:
        raise ErroNegocio("Arquivo vazio.")
    if len(conteudo) > TAMANHO_MAXIMO:
        raise ErroNegocio("Arquivo acima de 10 MB.")
    if tipo_mime not in TIPOS_ACEITOS:
        raise ErroNegocio("Tipo não aceito. Envie PDF, JPG, PNG, CSV ou XLSX.")
    assinatura = TIPOS_ACEITOS[tipo_mime]
    if assinatura and not conteudo.startswith(assinatura):
        raise ErroNegocio("O conteúdo do arquivo não corresponde ao tipo informado.")
    sha = hashlib.sha256(conteudo).hexdigest()
    destino = diretorio() / sha
    if not destino.exists():
        destino.write_bytes(conteudo)
    nome_limpo = Path(nome).name[:200] or "anexo"
    aid = con.execute(
        """INSERT INTO anexo (organizacao_id, sha256, nome_original, tipo_mime, tamanho, enviado_por)
           VALUES (?,?,?,?,?,?)""",
        (u["organizacao_id"], sha, nome_limpo, tipo_mime, len(conteudo), usuario_id)).lastrowid
    registrar(con, usuario_id, "enviar", "anexo", aid, depois={"sha256": sha, "nome": nome_limpo})
    return {"id": aid, "sha256": sha, "nome": nome_limpo, "tamanho": len(conteudo)}


def exigir_acesso(con: sqlite3.Connection, usuario_id: int, anexo_id: int) -> sqlite3.Row:
    """Quem pode usar ou baixar um anexo: a organização que o enviou, o órgão contratante de um
    contrato que o usa, e os papéis de controle (verificador, moderador, registrador, auditor)."""
    a = buscar(con, "anexo", anexo_id)
    u = buscar(con, "usuario", usuario_id)
    if u["papel"] in ("verificador", "moderador", "registrador", "auditor"):
        return a
    if a["organizacao_id"] is not None and a["organizacao_id"] == u["organizacao_id"]:
        return a
    ligado = con.execute(
        """SELECT 1 FROM lancamento_fornecedor l JOIN contrato c ON c.id=l.contrato_id
           WHERE l.anexo_id=? AND (c.orgao_id=? OR c.fornecedor_id=?) LIMIT 1""",
        (anexo_id, u["organizacao_id"], u["organizacao_id"])).fetchone()
    if ligado:
        return a
    raise NaoAutorizado("Anexo de outra organização.")


def abrir(con: sqlite3.Connection, usuario_id: int, anexo_id: int) -> tuple[sqlite3.Row, bytes]:
    a = exigir_acesso(con, usuario_id, anexo_id)
    caminho = diretorio() / a["sha256"]
    conteudo = caminho.read_bytes()
    if hashlib.sha256(conteudo).hexdigest() != a["sha256"]:
        raise ErroNegocio("O arquivo armazenado não confere com o hash registrado: possível adulteração.")
    return a, conteudo
