"""MRV — Mensuração (aferição), Relato e Verificação, com moderação final.

Fluxo:
    rascunho ─submeter─► submetido ─assumir─► em_verificacao ─┬─verificar─► verificado ─aprovar─► aprovado
        ▲                                                      └─devolver──► devolvido
        └──────────────── (correção) ◄── devolvido ◄─devolver── verificado

Segregação de funções (controle interno, Lei 14.133/2021, art. 7º, §1º):
- quem elabora não verifica nem modera;
- o verificador precisa ser OVV credenciado e não pode pertencer à organização inventariada;
- o moderador não pode ter elaborado nem verificado o mesmo inventário.
"""
from __future__ import annotations

import sqlite3

from .banco import ErroNegocio, NaoAutorizado, buscar, transacao
from .calculo import calcular_por_fator, calcular_por_gas

# (status atual, ação) -> (novo status, papel exigido)
TRANSICOES: dict[tuple[str, str], tuple[str, str]] = {
    ("rascunho", "submeter"): ("submetido", "elaborador"),
    ("devolvido", "submeter"): ("submetido", "elaborador"),
    ("submetido", "assumir"): ("em_verificacao", "verificador"),
    ("em_verificacao", "verificar"): ("verificado", "verificador"),
    ("em_verificacao", "devolver"): ("devolvido", "verificador"),
    ("verificado", "aprovar"): ("aprovado", "moderador"),
    ("verificado", "devolver"): ("devolvido", "moderador"),
}
EDITAVEL = ("rascunho", "devolvido")


def criar_inventario(con: sqlite3.Connection, usuario_id: int, organizacao_id: int, ano: int) -> int:
    u = buscar(con, "usuario", usuario_id)
    if u["papel"] != "elaborador" or u["organizacao_id"] != organizacao_id:
        raise NaoAutorizado("Só um elaborador da própria organização cria o inventário.")
    try:
        cur = con.execute(
            "INSERT INTO inventario (organizacao_id, ano, elaborado_por) VALUES (?,?,?)",
            (organizacao_id, ano, usuario_id))
    except sqlite3.IntegrityError as exc:
        raise ErroNegocio(f"Já existe inventário de {ano} para esta organização.") from exc
    return cur.lastrowid


def adicionar_atividade(con: sqlite3.Connection, usuario_id: int, inventario_id: int, *,
                        descricao: str, quantidade: float, fator: str | None = None,
                        gas: str | None = None, escopo: int = 1,
                        fracao_biogenica: float | None = None, evidencia: str | None = None) -> int:
    inv = buscar(con, "inventario", inventario_id)
    u = buscar(con, "usuario", usuario_id)
    if u["papel"] != "elaborador" or u["organizacao_id"] != inv["organizacao_id"]:
        raise NaoAutorizado("Só um elaborador da organização lança atividades.")
    if inv["status"] not in EDITAVEL:
        raise ErroNegocio(f"Inventário em '{inv['status']}' não aceita alterações.")
    if (fator is None) == (gas is None):
        raise ErroNegocio("Informe exatamente um: 'fator' ou 'gas'.")
    try:
        if fator:
            r = calcular_por_fator(fator, quantidade, fracao_biogenica)
            origem = fator
        else:
            r = calcular_por_gas(gas, quantidade, escopo)
            origem = f"gas:{gas}"
    except ValueError as exc:
        raise ErroNegocio(str(exc)) from exc
    cur = con.execute(
        """INSERT INTO atividade (inventario_id, descricao, escopo, origem, quantidade, tco2e,
           tco2_biogenico, memoria, evidencia) VALUES (?,?,?,?,?,?,?,?,?)""",
        (inventario_id, descricao, r.escopo, origem, quantidade, r.tco2e, r.tco2_biogenico,
         r.memoria, evidencia))
    return cur.lastrowid


def totais(con: sqlite3.Connection, inventario_id: int) -> dict:
    buscar(con, "inventario", inventario_id)
    linhas = con.execute(
        """SELECT escopo, COALESCE(SUM(tco2e),0) t, COALESCE(SUM(tco2_biogenico),0) b
           FROM atividade WHERE inventario_id = ? GROUP BY escopo""", (inventario_id,)).fetchall()
    por = {f"escopo{e}": 0.0 for e in (1, 2, 3)}
    bio = 0.0
    for l in linhas:
        por[f"escopo{l['escopo']}"] = round(l["t"], 6)
        bio += l["b"]
    por["total"] = round(sum(por[f"escopo{e}"] for e in (1, 2, 3)), 6)
    por["co2_biogenico"] = round(bio, 6)
    return por


def transitar(con: sqlite3.Connection, usuario_id: int, inventario_id: int, acao: str,
              parecer: str | None = None) -> str:
    with transacao(con):
        inv = buscar(con, "inventario", inventario_id)
        u = buscar(con, "usuario", usuario_id)
        chave = (inv["status"], acao)
        if chave not in TRANSICOES:
            raise ErroNegocio(f"Ação '{acao}' não permitida em '{inv['status']}'.")
        novo, papel = TRANSICOES[chave]
        if u["papel"] != papel:
            raise NaoAutorizado(f"A ação '{acao}' exige o papel '{papel}'.")
        _checar_segregacao(con, u, inv, acao)
        if acao == "devolver" and not (parecer and parecer.strip()):
            raise ErroNegocio("Devolução exige parecer fundamentado.")
        if acao == "submeter":
            n = con.execute("SELECT COUNT(*) FROM atividade WHERE inventario_id=?",
                            (inventario_id,)).fetchone()[0]
            if n == 0:
                raise ErroNegocio("Inventário sem atividades não pode ser submetido.")
        campos = {"assumir": "verificado_por", "aprovar": "moderado_por"}
        if acao in campos:
            con.execute(f"UPDATE inventario SET {campos[acao]}=? WHERE id=?", (usuario_id, inventario_id))
        con.execute("UPDATE inventario SET status=? WHERE id=?", (novo, inventario_id))
        con.execute("""INSERT INTO evento_mrv (inventario_id, de_status, para_status, usuario_id, parecer)
                       VALUES (?,?,?,?,?)""", (inventario_id, inv["status"], novo, usuario_id, parecer))
    return novo


def _checar_segregacao(con, u, inv, acao) -> None:
    if u["papel"] == "elaborador":
        if u["organizacao_id"] != inv["organizacao_id"]:
            raise NaoAutorizado("Elaborador de outra organização.")
        return
    if u["id"] == inv["elaborado_por"]:
        raise NaoAutorizado("Quem elaborou não pode verificar nem moderar.")
    if u["papel"] == "verificador":
        if not u["ovv_credenciado"]:
            raise NaoAutorizado("Verificador precisa ser OVV credenciado.")
        if u["organizacao_id"] == inv["organizacao_id"]:
            raise NaoAutorizado("Verificador não pode pertencer à organização inventariada.")
        if acao != "assumir" and inv["verificado_por"] != u["id"]:
            raise NaoAutorizado("Só o verificador que assumiu o inventário pode concluí-lo.")
    if u["papel"] == "moderador" and u["id"] == inv["verificado_por"]:
        raise NaoAutorizado("Quem verificou não pode moderar.")


def historico(con: sqlite3.Connection, inventario_id: int) -> list[dict]:
    return [dict(r) for r in con.execute(
        "SELECT * FROM evento_mrv WHERE inventario_id=? ORDER BY id", (inventario_id,))]
