"""Painel do gestor: totais por mês, escopo e unidade, comparação anual, metas e alertas.

Só entram atividades comprovadas (com anexo). Emissões evitadas e remoções aparecem à parte.
A soma por unidade sobe pela hierarquia (prédio > secretaria > órgão).
"""
from __future__ import annotations

import sqlite3

from .auditoria import registrar
from .banco import ErroNegocio, NaoAutorizado, buscar
from .mrv import COMPROVADA, totais

LIMIAR_ALERTA = 0.30  # variação de 30% numa unidade dispara alerta


def _inventario(con, organizacao_id: int, ano: int) -> sqlite3.Row | None:
    return con.execute("SELECT * FROM inventario WHERE organizacao_id=? AND ano=?",
                       (organizacao_id, ano)).fetchone()


def _por_unidade(con, organizacao_id: int, inventario_id: int) -> dict[int | None, float]:
    """Emissões comprovadas por unidade de topo da hierarquia (None = sem unidade)."""
    pais = {r["id"]: r["pai_id"] for r in con.execute(
        "SELECT id, pai_id FROM instalacao WHERE organizacao_id=?", (organizacao_id,))}

    def topo(i):
        vistos = set()
        while i is not None and pais.get(i) is not None and i not in vistos:
            vistos.add(i)
            i = pais[i]
        return i

    out: dict[int | None, float] = {}
    for r in con.execute(
            f"""SELECT a.instalacao_id, SUM(a.tco2e) t FROM atividade a
                WHERE a.inventario_id=? AND a.natureza='emissao' AND {COMPROVADA}
                GROUP BY a.instalacao_id""", (inventario_id,)):
        k = topo(r["instalacao_id"])
        out[k] = out.get(k, 0.0) + r["t"]
    return out


def definir_meta(con: sqlite3.Connection, usuario_id: int, organizacao_id: int, *, ano_base: int,
                 ano_alvo: int, reducao_percentual: float) -> int:
    u = buscar(con, "usuario", usuario_id)
    if u["papel"] != "elaborador" or u["organizacao_id"] != organizacao_id:
        raise NaoAutorizado("Só o gestor da organização define metas.")
    if ano_alvo <= ano_base:
        raise ErroNegocio("O ano-alvo deve ser posterior ao ano-base.")
    if not 0 < reducao_percentual <= 100:
        raise ErroNegocio("A redução deve estar entre 0 e 100%.")
    mid = con.execute(
        """INSERT INTO meta (organizacao_id, ano_base, ano_alvo, reducao_percentual) VALUES (?,?,?,?)
           ON CONFLICT (organizacao_id, ano_alvo) DO UPDATE SET ano_base=excluded.ano_base,
           reducao_percentual=excluded.reducao_percentual""",
        (organizacao_id, ano_base, ano_alvo, reducao_percentual)).lastrowid
    registrar(con, usuario_id, "definir_meta", "organizacao", organizacao_id,
              depois={"ano_base": ano_base, "ano_alvo": ano_alvo, "reducao": reducao_percentual})
    return mid


def painel(con: sqlite3.Connection, organizacao_id: int, ano: int) -> dict:
    org = buscar(con, "organizacao", organizacao_id)
    inv = _inventario(con, organizacao_id, ano)
    if inv is None:
        return {"organizacao": org["nome"], "ano": ano, "inventario": None}
    t = totais(con, inv["id"])
    por_mes = {m: 0.0 for m in range(1, 13)}
    sem_mes = 0.0
    for r in con.execute(
            f"""SELECT a.mes, SUM(a.tco2e) t FROM atividade a WHERE a.inventario_id=? AND a.natureza='emissao'
                AND {COMPROVADA} GROUP BY a.mes""", (inv["id"],)):
        if r["mes"]:
            por_mes[r["mes"]] = round(r["t"], 6)
        else:
            sem_mes += r["t"]
    nomes = {r["id"]: r["nome"] for r in con.execute(
        "SELECT id, nome FROM instalacao WHERE organizacao_id=?", (organizacao_id,))}
    atual = _por_unidade(con, organizacao_id, inv["id"])
    anterior_inv = _inventario(con, organizacao_id, ano - 1)
    anterior = _por_unidade(con, organizacao_id, anterior_inv["id"]) if anterior_inv else {}
    ranking = sorted(({"unidade": nomes.get(k, "Sem unidade"), "tco2e": round(v, 6),
                       "ano_anterior": round(anterior.get(k, 0.0), 6)} for k, v in atual.items()),
                     key=lambda x: -x["tco2e"])
    alertas = []
    for k, v in atual.items():
        antes = anterior.get(k)
        if antes and abs(v - antes) / antes >= LIMIAR_ALERTA:
            sentido = "subiu" if v > antes else "caiu"
            alertas.append(f"{nomes.get(k, 'Sem unidade')} {sentido} {abs(v - antes) / antes:.0%} "
                           f"em relação a {ano - 1}: verifique os lançamentos e a frota.")
    if t["pendentes_comprovacao"]["quantidade"]:
        p = t["pendentes_comprovacao"]
        alertas.append(f"{p['quantidade']} lançamento(s) sem anexo ({p['tco2e']:g} tCO2e) estão fora do total.")

    total_anterior = totais(con, anterior_inv["id"])["total"] if anterior_inv else None
    meta = con.execute("SELECT * FROM meta WHERE organizacao_id=? AND ano_alvo>=? ORDER BY ano_alvo LIMIT 1",
                       (organizacao_id, ano)).fetchone()
    meta_info = None
    if meta:
        base_inv = _inventario(con, organizacao_id, meta["ano_base"])
        base = totais(con, base_inv["id"])["total"] if base_inv else None
        if base:
            alvo = base * (1 - meta["reducao_percentual"] / 100)
            reduzido = (base - t["total"]) / base
            meta_info = {"ano_base": meta["ano_base"], "ano_alvo": meta["ano_alvo"],
                         "reducao_meta": meta["reducao_percentual"], "base_tco2e": round(base, 6),
                         "alvo_tco2e": round(alvo, 6), "reducao_atual_percentual": round(reduzido * 100, 2),
                         "progresso_percentual": round(max(min(reduzido / (meta["reducao_percentual"] / 100), 1),
                                                           0) * 100, 1)}
        else:
            meta_info = {"ano_base": meta["ano_base"], "ano_alvo": meta["ano_alvo"],
                         "reducao_meta": meta["reducao_percentual"],
                         "aviso": f"Sem inventário de {meta['ano_base']} para servir de base."}
    compensado = con.execute(
        "SELECT COALESCE(SUM(quantidade),0) FROM lote WHERE inventario_id=? AND status='cancelado' "
        "AND finalidade='compensacao_voluntaria'", (inv["id"],)).fetchone()[0]
    return {
        "organizacao": org["nome"], "ano": ano, "inventario": {"id": inv["id"], "status": inv["status"]},
        "totais": t, "por_mes": por_mes, "sem_mes": round(sem_mes, 6), "ranking_unidades": ranking,
        "ano_anterior_total": total_anterior,
        "variacao_anual_percentual": round((t["total"] - total_anterior) / total_anterior * 100, 1)
        if total_anterior else None,
        "meta": meta_info, "alertas": alertas, "compensado": compensado,
        "percentual_compensado": round(min(compensado / t["total"], 1) * 100, 1) if t["total"] else 0.0,
    }


def transparencia_organizacao(con: sqlite3.Connection, organizacao_id: int) -> dict:
    """Dados públicos do órgão (Lei 12.527/2011): último inventário APROVADO e compensação."""
    org = buscar(con, "organizacao", organizacao_id)
    inv = con.execute("SELECT * FROM inventario WHERE organizacao_id=? AND status='aprovado' "
                      "ORDER BY ano DESC LIMIT 1", (organizacao_id,)).fetchone()
    if inv is None:
        return {"organizacao": org["nome"], "inventario_aprovado": None}
    t = totais(con, inv["id"])
    compensado = con.execute(
        "SELECT COALESCE(SUM(quantidade),0) FROM lote WHERE inventario_id=? AND status='cancelado' "
        "AND finalidade='compensacao_voluntaria'", (inv["id"],)).fetchone()[0]
    return {"organizacao": org["nome"], "tipo": org["tipo"], "ano": inv["ano"],
            "emissoes_tco2e": t["total"], "escopo1": t["escopo1"], "escopo2": t["escopo2"],
            "escopo3": t["escopo3"], "emissoes_evitadas": t["emissoes_evitadas"],
            "compensado": compensado,
            "percentual_compensado": round(min(compensado / t["total"], 1) * 100, 1) if t["total"] else 0.0,
            "aviso": "Percentual compensado não equivale a 'carbono neutro': só vale com verificação "
                     "independente e cancelamento comprovado de 100% das emissões do escopo declarado."}
