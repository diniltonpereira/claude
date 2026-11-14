"""Compensação de emissões e conciliação SBCE.

Regras de elegibilidade aplicadas antes de aposentar qualquer unidade:
- o inventário precisa estar APROVADO (verificado e moderado);
- não se compensa mais do que o total do inventário (nem o mesmo inventário duas vezes);
- safra (vintage) não pode ser posterior ao ano do inventário nem mais antiga que
  `idade_maxima_safra` anos;
- compensação voluntária: CRVE e créditos voluntários; CBE não é usada para isso;
- conciliação SBCE: CBE e, no máximo, `limite_crve` (fração) em CRVE — o percentual
  real será fixado pelo órgão gestor do SBCE; aqui é parâmetro.
Escolha dos lotes: safras mais antigas primeiro (FIFO), dentro dos tipos elegíveis.
"""
from __future__ import annotations

import math
import sqlite3

from .banco import ErroNegocio, NaoAutorizado, buscar, transacao
from .mrv import totais
from .registro import aposentar

TIPOS_POR_FINALIDADE = {
    "compensacao_voluntaria": ("CRVE", "VOLUNTARIO"),
    "conciliacao_sbce": ("CBE", "CRVE"),
}


def ja_compensado(con: sqlite3.Connection, inventario_id: int, finalidade: str) -> int:
    return con.execute("SELECT COALESCE(SUM(quantidade),0) FROM lote WHERE inventario_id=? "
                       "AND status='aposentado' AND finalidade=?",
                       (inventario_id, finalidade)).fetchone()[0]


def compensar(con: sqlite3.Connection, usuario_id: int, inventario_id: int, *,
              finalidade: str = "compensacao_voluntaria", percentual: float = 100.0,
              escopos: tuple[int, ...] = (1, 2), idade_maxima_safra: int = 5,
              limite_crve: float = 0.0, simular: bool = False,
              chave_idempotencia: str | None = None) -> dict:
    if finalidade not in TIPOS_POR_FINALIDADE:
        raise ErroNegocio("Finalidade deve ser 'compensacao_voluntaria' ou 'conciliacao_sbce'.")
    if not 0 < percentual <= 100:
        raise ErroNegocio("Percentual deve estar entre 0 e 100.")
    if not escopos or any(e not in (1, 2, 3) for e in escopos):
        raise ErroNegocio("Escopos inválidos.")
    if finalidade == "conciliacao_sbce":
        escopos = (1,)  # o SBCE regula emissões diretas das instalações

    with transacao(con):
        if chave_idempotencia and not simular:
            anteriores = con.execute(
                "SELECT lote_id, quantidade, id FROM movimento WHERE substr(chave_idempotencia,1,?)=? "
                "AND usuario_id=? ORDER BY id",
                (len(chave_idempotencia) + 1, f"{chave_idempotencia}:", usuario_id)).fetchall()
            if anteriores:
                return {"inventario_id": inventario_id, "repetido": True,
                        "movimentos": [dict(a) for a in anteriores]}
        inv = buscar(con, "inventario", inventario_id)
        org = buscar(con, "organizacao", inv["organizacao_id"])
        u = buscar(con, "usuario", usuario_id)
        if u["organizacao_id"] != org["id"]:
            raise NaoAutorizado("Só a própria organização compensa seu inventário.")
        if inv["status"] != "aprovado":
            raise ErroNegocio("Só inventários aprovados (verificados e moderados) podem ser compensados.")
        conta = con.execute("SELECT id FROM conta WHERE organizacao_id=?", (org["id"],)).fetchone()
        if conta is None:
            raise ErroNegocio("Organização sem conta no registro.")

        t = totais(con, inventario_id)
        base = sum(t[f"escopo{e}"] for e in escopos)
        meta = math.ceil(base * percentual / 100 - 1e-9)
        restante = meta - ja_compensado(con, inventario_id, finalidade)
        if restante <= 0:
            raise ErroNegocio("Inventário já compensado nesse nível.")

        tipos = TIPOS_POR_FINALIDADE[finalidade]
        ph = ",".join("?" * len(tipos))
        lotes = con.execute(
            f"""SELECT * FROM lote WHERE conta_id=? AND status='ativo' AND tipo_ativo IN ({ph})
                AND safra <= ? AND safra >= ?
                ORDER BY CASE tipo_ativo WHEN 'CBE' THEN 0 ELSE 1 END, safra, id""",
            (conta["id"], *tipos, inv["ano"], inv["ano"] - idade_maxima_safra)).fetchall()

        teto_crve = math.floor(restante * limite_crve) if finalidade == "conciliacao_sbce" else restante
        plano, falta, usado_crve = [], restante, 0
        for l in lotes:
            if falta == 0:
                break
            pode = min(l["quantidade"], falta)
            if finalidade == "conciliacao_sbce" and l["tipo_ativo"] == "CRVE":
                pode = min(pode, teto_crve - usado_crve)
                if pode <= 0:
                    continue
                usado_crve += pode
            plano.append({"lote_id": l["id"], "serial": l["serial"], "tipo_ativo": l["tipo_ativo"],
                          "safra": l["safra"], "quantidade": pode})
            falta -= pode

        resultado = {"inventario_id": inventario_id, "finalidade": finalidade, "escopos": list(escopos),
                     "emissoes_base_tco2e": round(base, 6), "meta_unidades": meta,
                     "a_compensar": restante, "plano": plano, "deficit": falta,
                     "simulacao": simular}
        if simular:
            return resultado
        if falta > 0:
            raise ErroNegocio(f"Saldo elegível insuficiente: faltam {falta} unidades. "
                              "Use simular=true para ver o plano.")
        beneficiario = f"{org['nome']} — inventário {inv['ano']}"
        for i, p in enumerate(plano):
            chave = f"{chave_idempotencia}:{i}" if chave_idempotencia else None
            r = aposentar(con, usuario_id, lote_id=p["lote_id"], quantidade=p["quantidade"],
                          finalidade=finalidade, beneficiario=beneficiario,
                          inventario_id=inventario_id, chave_idempotencia=chave, _em_transacao=True)
            p["lote_aposentado_id"] = r["lote_id"]
            p["movimento_id"] = r["movimento_id"]
        return resultado
