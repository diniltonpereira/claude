"""Compensação voluntária e conciliação periódica (Lei 15.042/2024).

Regras aplicadas antes de cancelar qualquer unidade:
- o inventário precisa estar APROVADO (verificado e moderado);
- não se cancela mais do que a base do inventário, nem o mesmo inventário duas vezes;
- safra não pode ser posterior ao ano do inventário nem mais antiga que `idade_maxima_safra`.

Compensação voluntária (art. 45): aceita créditos de carbono do mercado voluntário (art. 2º,
VII e XIX) e ativos do SBCE (CBE e CRVE), sempre com cancelamento. O ente pode restringir
os tipos por política própria (`tipos_aceitos`). Ordem: CRVE, voluntário, CBE.

Conciliação periódica (arts. 34 e 2º, V): só ativos do SBCE (art. 10), em quantidade igual
às emissões LÍQUIDAS do escopo 1. A CRVE fica limitada ao percentual máximo do PNA
(arts. 12, I, e 21, IV), aqui o parâmetro `limite_crve`. A CBE vale no próprio período de
compromisso, salvo autorização do PNA (art. 11, § 2º). Ordem: CBE, depois CRVE.
"""
from __future__ import annotations

import math
import sqlite3

from .banco import ErroNegocio, NaoAutorizado, buscar, transacao
from .mrv import totais
from .registro import cancelar

TIPOS_POR_FINALIDADE = {
    "compensacao_voluntaria": ("CRVE", "VOLUNTARIO", "CBE"),
    "conciliacao_sbce": ("CBE", "CRVE"),
}


def ja_compensado(con: sqlite3.Connection, inventario_id: int, finalidade: str,
                  tipo_ativo: str | None = None) -> int:
    sql = ("SELECT COALESCE(SUM(quantidade),0) FROM lote WHERE inventario_id=? "
           "AND status='cancelado' AND finalidade=?")
    args: tuple = (inventario_id, finalidade)
    if tipo_ativo:
        sql += " AND tipo_ativo=?"
        args += (tipo_ativo,)
    return con.execute(sql, args).fetchone()[0]


def compensar(con: sqlite3.Connection, usuario_id: int, inventario_id: int, *,
              finalidade: str = "compensacao_voluntaria", percentual: float = 100.0,
              escopos: tuple[int, ...] = (1, 2), idade_maxima_safra: int = 5,
              tipos_aceitos: tuple[str, ...] | None = None,
              limite_crve: float = 0.0, periodo_compromisso: tuple[int, int] | None = None,
              cbe_outros_periodos: bool = False, simular: bool = False,
              chave_idempotencia: str | None = None) -> dict:
    if finalidade not in TIPOS_POR_FINALIDADE:
        raise ErroNegocio("Finalidade deve ser 'compensacao_voluntaria' ou 'conciliacao_sbce'.")
    if not 0 < percentual <= 100:
        raise ErroNegocio("Percentual deve estar entre 0 e 100.")
    if not escopos or any(e not in (1, 2, 3) for e in escopos):
        raise ErroNegocio("Escopos inválidos.")
    if not 0 <= limite_crve <= 1:
        raise ErroNegocio("limite_crve deve ser uma fração entre 0 e 1.")
    tipos = TIPOS_POR_FINALIDADE[finalidade]
    if tipos_aceitos is not None:
        invalidos = set(tipos_aceitos) - set(tipos)
        if invalidos:
            raise ErroNegocio(f"Tipos não admitidos para {finalidade}: {sorted(invalidos)} "
                              "(conciliação só aceita ativos do SBCE, art. 10).")
        tipos = tuple(t for t in tipos if t in tipos_aceitos)
    conciliacao = finalidade == "conciliacao_sbce"
    if conciliacao:
        escopos = (1,)       # o SBCE regula emissões das instalações e fontes do operador
        percentual = 100.0   # art. 34: quantidade equivalente às emissões do período

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
        if conciliacao:
            base = t["escopo1_liquido"]           # emissões líquidas (art. 2º, V e XI)
        else:
            base = sum(t[f"escopo{e}"] for e in escopos)
        meta = math.ceil(base * percentual / 100 - 1e-9)
        restante = meta - ja_compensado(con, inventario_id, finalidade)
        if restante <= 0:
            raise ErroNegocio("Inventário já compensado nesse nível.")

        ini, fim = periodo_compromisso or (inv["ano"], inv["ano"])
        ordem = {tp: i for i, tp in enumerate(tipos)}
        ph = ",".join("?" * len(tipos))
        lotes = sorted(con.execute(
            f"""SELECT * FROM lote WHERE conta_id=? AND status='ativo' AND tipo_ativo IN ({ph})
                AND safra <= ? AND safra >= ?""",
            (conta["id"], *tipos, inv["ano"], inv["ano"] - idade_maxima_safra)).fetchall(),
            key=lambda l: (ordem[l["tipo_ativo"]], l["safra"], l["id"]))

        teto_crve = (math.floor(meta * limite_crve)
                     - ja_compensado(con, inventario_id, finalidade, "CRVE")) if conciliacao else restante
        plano, falta, usado_crve, ignorados = [], restante, 0, []
        for l in lotes:
            if falta == 0:
                break
            if conciliacao and l["tipo_ativo"] == "CBE" and not cbe_outros_periodos \
                    and not ini <= l["safra"] <= fim:
                ignorados.append(f"{l['serial']}: CBE de outro período de compromisso (art. 11, § 2º)")
                continue
            pode = min(l["quantidade"], falta)
            if conciliacao and l["tipo_ativo"] == "CRVE":
                pode = min(pode, teto_crve - usado_crve)
                if pode <= 0:
                    ignorados.append(f"{l['serial']}: limite de CRVE do PNA atingido (art. 21, IV)")
                    continue
                usado_crve += pode
            plano.append({"lote_id": l["id"], "serial": l["serial"], "tipo_ativo": l["tipo_ativo"],
                          "safra": l["safra"], "quantidade": pode})
            falta -= pode

        resultado = {"inventario_id": inventario_id, "finalidade": finalidade, "escopos": list(escopos),
                     "emissoes_base_tco2e": round(base, 6), "remocoes_tco2e": t["remocoes"],
                     "meta_unidades": meta, "a_compensar": restante, "plano": plano,
                     "deficit": falta, "lotes_ignorados": ignorados, "simulacao": simular}
        if simular:
            return resultado
        if falta > 0:
            raise ErroNegocio(f"Saldo elegível insuficiente: faltam {falta} unidades. "
                              "Use simular=true para ver o plano.")
        beneficiario = f"{org['nome']} — inventário {inv['ano']}"
        for i, p in enumerate(plano):
            chave = f"{chave_idempotencia}:{i}" if chave_idempotencia else None
            r = cancelar(con, usuario_id, lote_id=p["lote_id"], quantidade=p["quantidade"],
                         finalidade=finalidade, beneficiario=beneficiario,
                         inventario_id=inventario_id, chave_idempotencia=chave, _em_transacao=True)
            p["lote_cancelado_id"] = r["lote_id"]
            p["movimento_id"] = r["movimento_id"]
        return resultado
