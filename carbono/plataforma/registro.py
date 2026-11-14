"""Registro de ativos de carbono (CBE, CRVE e créditos voluntários).

Garantias:
- 1 unidade = 1 tCO2e; saldo nunca fica negativo (CHECK + trava de escrita);
- ativo cancelado não volta a circular (vedação à dupla contagem, art. 2º, IX);
- cada movimento entra num livro-razão somente-inclusão com hash encadeado;
- chave de idempotência evita duplicar uma operação reenviada pela rede.

O registro oficial de CBE/CRVE é o Sistema de Registro Central do SBCE. Esta plataforma
é o registro de gestão do ente público, que deve espelhar (e conciliar com) o oficial.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone

from .banco import ErroNegocio, NaoAutorizado, buscar, transacao

GENESE = "0" * 64


def _agora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _hash(conteudo: dict) -> str:
    return hashlib.sha256(json.dumps(conteudo, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _registrar(con, *, tipo, lote_id, quantidade, de_conta, para_conta, usuario_id, motivo,
               chave) -> int:
    ultimo = con.execute("SELECT hash FROM movimento ORDER BY id DESC LIMIT 1").fetchone()
    anterior = ultimo["hash"] if ultimo else GENESE
    em = _agora()
    conteudo = dict(tipo=tipo, lote_id=lote_id, quantidade=quantidade, de_conta=de_conta,
                    para_conta=para_conta, usuario_id=usuario_id, motivo=motivo, em=em,
                    hash_anterior=anterior)
    cur = con.execute(
        """INSERT INTO movimento (tipo, lote_id, quantidade, de_conta, para_conta, usuario_id,
           motivo, chave_idempotencia, em, hash_anterior, hash) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (tipo, lote_id, quantidade, de_conta, para_conta, usuario_id, motivo, chave, em, anterior,
         _hash(conteudo)))
    return cur.lastrowid


def _ja_processado(con, chave: str | None, usuario_id: int) -> dict | None:
    if not chave:
        return None
    m = con.execute("SELECT * FROM movimento WHERE chave_idempotencia=?", (chave,)).fetchone()
    if m and m["usuario_id"] != usuario_id:
        raise ErroNegocio("Chave de idempotência já usada por outro usuário.")
    return dict(m) if m else None


def abrir_conta(con: sqlite3.Connection, organizacao_id: int) -> int:
    buscar(con, "organizacao", organizacao_id)
    existente = con.execute("SELECT id FROM conta WHERE organizacao_id=?", (organizacao_id,)).fetchone()
    if existente:
        return existente["id"]
    return con.execute("INSERT INTO conta (organizacao_id) VALUES (?)", (organizacao_id,)).lastrowid


def _exigir_registrador(con, usuario_id):
    if buscar(con, "usuario", usuario_id)["papel"] != "registrador":
        raise NaoAutorizado("Operação restrita ao registrador.")


def _exigir_titular(con, usuario_id, conta_id):
    u = buscar(con, "usuario", usuario_id)
    c = buscar(con, "conta", conta_id)
    if u["papel"] != "registrador" and u["organizacao_id"] != c["organizacao_id"]:
        raise NaoAutorizado("Usuário não pertence à organização titular da conta.")


def emitir(con: sqlite3.Connection, usuario_id: int, *, conta_id: int, tipo_ativo: str, projeto: str,
           metodologia: str, safra: int, quantidade: int, serial: str,
           chave_idempotencia: str | None = None) -> dict:
    """Escritura um lote já emitido no registro oficial (ou crédito voluntário verificado)."""
    if quantidade <= 0:
        raise ErroNegocio("Quantidade deve ser positiva.")
    with transacao(con):
        if (m := _ja_processado(con, chave_idempotencia, usuario_id)):
            return {"lote_id": m["lote_id"], "movimento_id": m["id"], "repetido": True}
        _exigir_registrador(con, usuario_id)
        buscar(con, "conta", conta_id)
        try:
            lote_id = con.execute(
                """INSERT INTO lote (serial, tipo_ativo, projeto, metodologia, safra, quantidade,
                   conta_id, status) VALUES (?,?,?,?,?,?,?,'ativo')""",
                (serial, tipo_ativo, projeto, metodologia, safra, quantidade, conta_id)).lastrowid
        except sqlite3.IntegrityError as exc:
            raise ErroNegocio(f"Serial duplicado ou dados inválidos: {exc}") from exc
        mid = _registrar(con, tipo="emissao", lote_id=lote_id, quantidade=quantidade, de_conta=None,
                         para_conta=conta_id, usuario_id=usuario_id, motivo=f"Emissão {serial}",
                         chave=chave_idempotencia)
    return {"lote_id": lote_id, "movimento_id": mid, "repetido": False}


def _separar(con, lote, quantidade: int) -> int:
    """Separa `quantidade` unidades de um lote ativo; devolve o id do lote que se moverá."""
    if lote["status"] != "ativo":
        raise ErroNegocio(f"Lote {lote['serial']} está '{lote['status']}' e não pode ser movimentado.")
    if quantidade <= 0 or quantidade > lote["quantidade"]:
        raise ErroNegocio(f"Saldo insuficiente no lote {lote['serial']} "
                          f"(disponível {lote['quantidade']}, pedido {quantidade}).")
    if quantidade == lote["quantidade"]:
        return lote["id"]
    filhos = con.execute("SELECT COUNT(*) FROM lote WHERE lote_origem_id=?", (lote["id"],)).fetchone()[0]
    con.execute("UPDATE lote SET quantidade = quantidade - ? WHERE id=?", (quantidade, lote["id"]))
    return con.execute(
        """INSERT INTO lote (serial, tipo_ativo, projeto, metodologia, safra, quantidade, conta_id,
           status, lote_origem_id) VALUES (?,?,?,?,?,?,?,'ativo',?)""",
        (f"{lote['serial']}.{filhos + 1}", lote["tipo_ativo"], lote["projeto"], lote["metodologia"],
         lote["safra"], quantidade, lote["conta_id"], lote["id"])).lastrowid


def transferir(con: sqlite3.Connection, usuario_id: int, *, lote_id: int, quantidade: int,
               para_conta: int, chave_idempotencia: str | None = None) -> dict:
    with transacao(con):
        if (m := _ja_processado(con, chave_idempotencia, usuario_id)):
            return {"lote_id": m["lote_id"], "movimento_id": m["id"], "repetido": True}
        lote = buscar(con, "lote", lote_id)
        _exigir_titular(con, usuario_id, lote["conta_id"])
        buscar(con, "conta", para_conta)
        if para_conta == lote["conta_id"]:
            raise ErroNegocio("Conta de destino igual à de origem.")
        movido = _separar(con, lote, quantidade)
        con.execute("UPDATE lote SET conta_id=? WHERE id=?", (para_conta, movido))
        mid = _registrar(con, tipo="transferencia", lote_id=movido, quantidade=quantidade,
                         de_conta=lote["conta_id"], para_conta=para_conta, usuario_id=usuario_id,
                         motivo=None, chave=chave_idempotencia)
    return {"lote_id": movido, "movimento_id": mid, "repetido": False}


def cancelar(con: sqlite3.Connection, usuario_id: int, *, lote_id: int, quantidade: int,
             finalidade: str, beneficiario: str, inventario_id: int | None = None,
             chave_idempotencia: str | None = None, _em_transacao: bool = False) -> dict:
    """Cancela unidades (art. 2º, II; art. 45), retirando-as de circulação. Irreversível.

    Finalidades: compensação voluntária (art. 45), conciliação periódica (art. 34) ou
    invalidação (ex.: reversão de remoções, art. 2º, XXXII, ou erro de escrituração).
    """
    if finalidade not in ("compensacao_voluntaria", "conciliacao_sbce", "invalidacao"):
        raise ErroNegocio("Finalidade inválida.")
    if not beneficiario.strip():
        raise ErroNegocio("Informe o beneficiário do cancelamento.")

    def _executar():
        if (m := _ja_processado(con, chave_idempotencia, usuario_id)):
            return {"lote_id": m["lote_id"], "movimento_id": m["id"], "repetido": True}
        lote = buscar(con, "lote", lote_id)
        _exigir_titular(con, usuario_id, lote["conta_id"])
        movido = _separar(con, lote, quantidade)
        con.execute("""UPDATE lote SET status='cancelado', finalidade=?, beneficiario=?,
                       inventario_id=? WHERE id=?""", (finalidade, beneficiario, inventario_id, movido))
        mid = _registrar(con, tipo="cancelamento", lote_id=movido, quantidade=quantidade,
                         de_conta=lote["conta_id"], para_conta=None, usuario_id=usuario_id,
                         motivo=f"{finalidade}: {beneficiario}", chave=chave_idempotencia)
        return {"lote_id": movido, "movimento_id": mid, "repetido": False}

    if _em_transacao:
        return _executar()
    with transacao(con):
        return _executar()


def saldo(con: sqlite3.Connection, conta_id: int) -> list[dict]:
    buscar(con, "conta", conta_id)
    return [dict(r) for r in con.execute(
        """SELECT id AS lote_id, serial, tipo_ativo, projeto, metodologia, safra, quantidade
           FROM lote WHERE conta_id=? AND status='ativo' ORDER BY safra, id""", (conta_id,))]


def verificar_cadeia(con: sqlite3.Connection) -> dict:
    """Recalcula todos os hashes. Qualquer adulteração direta no banco aparece aqui."""
    anterior = GENESE
    n = 0
    for m in con.execute("SELECT * FROM movimento ORDER BY id"):
        conteudo = dict(tipo=m["tipo"], lote_id=m["lote_id"], quantidade=m["quantidade"],
                        de_conta=m["de_conta"], para_conta=m["para_conta"], usuario_id=m["usuario_id"],
                        motivo=m["motivo"], em=m["em"], hash_anterior=anterior)
        if m["hash_anterior"] != anterior or m["hash"] != _hash(conteudo):
            return {"integra": False, "movimentos": n, "quebra_em": m["id"]}
        anterior = m["hash"]
        n += 1
    return {"integra": True, "movimentos": n, "quebra_em": None}
