"""Trilha de auditoria geral (somente inclusão): quem fez, quando, de qual IP, antes e depois.

O IP e o usuário da requisição chegam por variáveis de contexto preenchidas pela API, para que
as regras de negócio não dependam do framework web.
"""
from __future__ import annotations

import json
import sqlite3
from contextvars import ContextVar

ip_atual: ContextVar[str | None] = ContextVar("ip_atual", default=None)


def _json(v) -> str | None:
    if v is None:
        return None
    if isinstance(v, sqlite3.Row):
        v = dict(v)
    return json.dumps(v, ensure_ascii=False, default=str, sort_keys=True)


def registrar(con: sqlite3.Connection, usuario_id: int | None, acao: str, entidade: str,
              entidade_id: int | None, *, antes=None, depois=None) -> None:
    con.execute(
        """INSERT INTO auditoria (usuario_id, ip, acao, entidade, entidade_id, antes, depois)
           VALUES (?,?,?,?,?,?,?)""",
        (usuario_id, ip_atual.get(), acao, entidade, entidade_id, _json(antes), _json(depois)))


def consultar(con: sqlite3.Connection, *, entidade: str | None = None, entidade_id: int | None = None,
              limite: int = 200) -> list[dict]:
    sql, args = "SELECT a.*, u.nome AS usuario_nome FROM auditoria a LEFT JOIN usuario u ON u.id=a.usuario_id", []
    filtros = []
    if entidade:
        filtros.append("a.entidade=?"); args.append(entidade)
    if entidade_id is not None:
        filtros.append("a.entidade_id=?"); args.append(entidade_id)
    if filtros:
        sql += " WHERE " + " AND ".join(filtros)
    sql += " ORDER BY a.id DESC LIMIT ?"
    args.append(min(max(limite, 1), 1000))
    return [dict(r) for r in con.execute(sql, args)]
