"""Compra de créditos certificados por órgãos públicos (fase 2).

Regras jurídicas embutidas:
- Vendedores entram por CREDENCIAMENTO ABERTO com critérios objetivos (Lei 14.133/2021, art. 79):
  qualquer empresa que cumpra os critérios pode ofertar. Não há lista fechada de parceiros.
- O pedido segue a ordem da despesa pública (Lei 4.320/1964): instrução do processo com TR e
  justificativa de preço > EMPENHO PRÉVIO (art. 60) > entrega comprovada no registro >
  LIQUIDAÇÃO atestada pelo fiscal (art. 63) > pagamento. Nenhuma etapa pode ser pulada.
- A plataforma NÃO movimenta dinheiro de órgão público: empenho e pagamento são registrados a
  partir do sistema financeiro do ente (nº do empenho, ordem bancária).
- A taxa da plataforma é explícita na simulação, no pedido e na minuta de TR.
- A minuta de TR é neutra: não exige comprar por esta plataforma nem cita fornecedores, e traz a
  pesquisa de preços do catálogo como UMA das fontes do art. 23 da Lei 14.133.
- Só CRVE e créditos voluntários certificados são ofertados (CBE é ativo de operador regulado).
"""
from __future__ import annotations

import html
import json
import os
import sqlite3
import statistics
from datetime import date, timedelta

from .auditoria import registrar
from .banco import ErroNegocio, NaoAutorizado, NaoEncontrado, buscar, transacao
from .registro import abrir_conta, transferir

CRITERIOS = {
    "cnpj": "Comprovante de inscrição e situação cadastral no CNPJ",
    "regularidade_federal": "Certidão de regularidade perante a Fazenda Federal (RFB/PGFN)",
    "fgts": "Certificado de regularidade do FGTS",
    "trabalhista": "Certidão negativa de débitos trabalhistas",
}
CATEGORIAS = {"redd": "REDD+", "restauracao": "Restauração florestal", "energia": "Energia renovável",
              "residuos_biogas": "Resíduos e biogás", "eficiencia": "Eficiência energética", "outros": "Outros"}
ETAPAS = ("rascunho", "instruido", "empenhado", "entregue", "liquidado", "pago")


def taxa_percentual() -> float:
    return float(os.environ.get("CARBONO_TAXA_MARKETPLACE", "10"))


def _u(con, uid):
    return buscar(con, "usuario", uid)


# ---------------------------------------------------------------- credenciamento
def solicitar_credenciamento(con: sqlite3.Connection, usuario_id: int, *, documentos: dict[str, int],
                             declaracao_dupla_contagem: bool) -> int:
    u = _u(con, usuario_id)
    if u["papel"] != "fornecedor" or not u["organizacao_id"]:
        raise NaoAutorizado("Só uma empresa fornecedora solicita credenciamento.")
    faltando = [CRITERIOS[k] for k in CRITERIOS if not documentos.get(k)]
    if faltando:
        raise ErroNegocio("Anexe: " + "; ".join(faltando) + ".")
    if not declaracao_dupla_contagem:
        raise ErroNegocio("É obrigatória a declaração de que os créditos ofertados não foram usados, "
                          "vendidos ou prometidos a terceiros (vedação à dupla contagem).")
    from .anexos import exigir_acesso
    for aid in documentos.values():
        exigir_acesso(con, usuario_id, int(aid))
    if con.execute("SELECT 1 FROM credenciamento WHERE organizacao_id=? AND status='em_analise'",
                   (u["organizacao_id"],)).fetchone():
        raise ErroNegocio("Já existe pedido de credenciamento em análise.")
    cid = con.execute(
        """INSERT INTO credenciamento (organizacao_id, documentos, declaracao_dupla_contagem, solicitado_por)
           VALUES (?,?,1,?)""",
        (u["organizacao_id"], json.dumps({k: int(v) for k, v in documentos.items() if k in CRITERIOS}),
         usuario_id)).lastrowid
    abrir_conta(con, u["organizacao_id"])
    registrar(con, usuario_id, "solicitar", "credenciamento", cid)
    return cid


def analisar_credenciamento(con: sqlite3.Connection, usuario_id: int, credenciamento_id: int, *,
                            criterios: dict[str, bool], parecer: str | None = None,
                            hoje: date | None = None) -> str:
    """Análise objetiva: aprovado se, e somente se, todos os critérios forem atendidos."""
    hoje = hoje or date.today()
    if _u(con, usuario_id)["papel"] != "registrador":
        raise NaoAutorizado("Só a administração analisa credenciamentos.")
    c = buscar(con, "credenciamento", credenciamento_id)
    if c["status"] != "em_analise":
        raise ErroNegocio("Credenciamento já analisado.")
    if set(criterios) != set(CRITERIOS):
        raise ErroNegocio("Avalie todos os critérios.")
    aprovado = all(criterios.values())
    if not aprovado and not (parecer and parecer.strip()):
        raise ErroNegocio("Indeferimento exige parecer indicando o critério não atendido.")
    status = "aprovado" if aprovado else "indeferido"
    valido = (hoje + timedelta(days=365)).isoformat() if aprovado else None
    con.execute("""UPDATE credenciamento SET status=?, criterios=?, parecer=?, analisado_por=?,
                   analisado_em=strftime('%Y-%m-%dT%H:%M:%fZ','now'), valido_ate=? WHERE id=?""",
                (status, json.dumps(criterios), parecer, usuario_id, valido, credenciamento_id))
    registrar(con, usuario_id, "analisar", "credenciamento", credenciamento_id,
              depois={"status": status, "criterios": criterios, "parecer": parecer})
    return status


def credenciado(con: sqlite3.Connection, organizacao_id: int, hoje: date | None = None) -> bool:
    hoje = hoje or date.today()
    return bool(con.execute(
        "SELECT 1 FROM credenciamento WHERE organizacao_id=? AND status='aprovado' AND valido_ate >= ?",
        (organizacao_id, hoje.isoformat())).fetchone())


def credenciamentos(con: sqlite3.Connection, usuario_id: int) -> list[dict]:
    u = _u(con, usuario_id)
    sql = """SELECT c.*, o.nome AS empresa, o.cnpj FROM credenciamento c
             JOIN organizacao o ON o.id=c.organizacao_id"""
    if u["papel"] in ("registrador", "auditor"):
        return [dict(r) for r in con.execute(sql + " ORDER BY c.id DESC")]
    return [dict(r) for r in con.execute(sql + " WHERE c.organizacao_id=? ORDER BY c.id DESC",
                                         (u["organizacao_id"],))]


# ---------------------------------------------------------------- ofertas e catálogo
def publicar_oferta(con: sqlite3.Connection, usuario_id: int, *, lote_id: int, categoria: str, municipio: str,
                    uf: str, preco_unitario: float, quantidade: int, hoje: date | None = None) -> int:
    u = _u(con, usuario_id)
    if u["papel"] != "fornecedor":
        raise NaoAutorizado("Só empresas fornecedoras publicam ofertas.")
    if not credenciado(con, u["organizacao_id"], hoje):
        raise ErroNegocio("Empresa sem credenciamento válido.")
    if categoria not in CATEGORIAS:
        raise ErroNegocio("Categoria inválida.")
    if preco_unitario <= 0 or quantidade <= 0:
        raise ErroNegocio("Preço e quantidade devem ser positivos.")
    lote = buscar(con, "lote", lote_id)
    conta = con.execute("SELECT id FROM conta WHERE organizacao_id=?", (u["organizacao_id"],)).fetchone()
    if not conta or lote["conta_id"] != conta["id"] or lote["status"] != "ativo":
        raise ErroNegocio("O lote precisa estar ativo na conta da empresa.")
    if lote["tipo_ativo"] not in ("CRVE", "VOLUNTARIO"):
        raise ErroNegocio("Só CRVE e créditos voluntários certificados podem ser ofertados.")
    if con.execute("SELECT 1 FROM oferta WHERE lote_id=? AND ativa=1", (lote_id,)).fetchone():
        raise ErroNegocio("Já existe oferta ativa para este lote.")
    if quantidade > lote["quantidade"]:
        raise ErroNegocio(f"Quantidade acima do saldo do lote ({lote['quantidade']}).")
    oid = con.execute(
        """INSERT INTO oferta (fornecedor_id, lote_id, categoria, municipio, uf, preco_unitario,
           quantidade_disponivel) VALUES (?,?,?,?,?,?,?)""",
        (u["organizacao_id"], lote_id, categoria, municipio, uf.upper(), preco_unitario, quantidade)).lastrowid
    registrar(con, usuario_id, "publicar", "oferta", oid,
              depois={"lote_id": lote_id, "preco": preco_unitario, "quantidade": quantidade})
    return oid


def retirar_oferta(con: sqlite3.Connection, usuario_id: int, oferta_id: int) -> None:
    o = buscar(con, "oferta", oferta_id)
    if _u(con, usuario_id)["organizacao_id"] != o["fornecedor_id"]:
        raise NaoAutorizado("Oferta de outra empresa.")
    con.execute("UPDATE oferta SET ativa=0 WHERE id=?", (oferta_id,))
    registrar(con, usuario_id, "retirar", "oferta", oferta_id)


def catalogo(con: sqlite3.Connection, *, uf: str | None = None, categoria: str | None = None,
             hoje: date | None = None) -> list[dict]:
    hoje = hoje or date.today()
    sql = """SELECT o.*, f.nome AS fornecedor, l.serial, l.tipo_ativo, l.certificadora, l.metodologia,
                    l.projeto, l.safra, l.quantidade AS saldo_lote
             FROM oferta o JOIN organizacao f ON f.id=o.fornecedor_id JOIN lote l ON l.id=o.lote_id
             WHERE o.ativa=1 AND o.quantidade_disponivel > 0 AND l.status='ativo'
               AND EXISTS (SELECT 1 FROM credenciamento c WHERE c.organizacao_id=o.fornecedor_id
                           AND c.status='aprovado' AND c.valido_ate >= ?)"""
    args: list = [hoje.isoformat()]
    if uf:
        sql += " AND o.uf=?"; args.append(uf.upper())
    if categoria:
        sql += " AND o.categoria=?"; args.append(categoria)
    out = []
    for r in con.execute(sql + " ORDER BY o.preco_unitario, o.id", args):
        d = dict(r)
        d["disponivel"] = min(d["quantidade_disponivel"], d["saldo_lote"])
        d["categoria_nome"] = CATEGORIAS[d["categoria"]]
        out.append(d)
    return out


def simular(con: sqlite3.Connection, oferta_id: int, quantidade: int) -> dict:
    o = buscar(con, "oferta", oferta_id)
    if quantidade <= 0:
        raise ErroNegocio("Quantidade deve ser positiva.")
    taxa = taxa_percentual()
    creditos = round(quantidade * o["preco_unitario"], 2)
    valor_taxa = round(creditos * taxa / 100, 2)
    return {"oferta_id": oferta_id, "quantidade": quantidade, "preco_unitario": o["preco_unitario"],
            "valor_creditos": creditos, "taxa_percentual": taxa, "valor_taxa": valor_taxa,
            "valor_total": round(creditos + valor_taxa, 2)}


# ---------------------------------------------------------------- pedido (despesa pública)
def _pedido_do_orgao(con, usuario_id: int, pedido_id: int, papeis=("elaborador",)):
    p = buscar(con, "pedido", pedido_id)
    u = _u(con, usuario_id)
    if u["papel"] not in papeis or u["organizacao_id"] != p["orgao_id"]:
        raise NaoAutorizado("Ação restrita ao órgão comprador.")
    return p, u


def _exigir_status(p, *esperados):
    if p["status"] not in esperados:
        raise ErroNegocio(f"Pedido em '{p['status']}': ação não permitida nesta etapa.")


def criar_pedido(con: sqlite3.Connection, usuario_id: int, *, oferta_id: int, quantidade: int,
                 inventario_id: int | None = None, hoje: date | None = None) -> int:
    u = _u(con, usuario_id)
    if u["papel"] != "elaborador" or not u["organizacao_id"]:
        raise NaoAutorizado("Só o gestor do órgão abre pedido de compra.")
    org = buscar(con, "organizacao", u["organizacao_id"])
    if org["tipo"] != "orgao_publico":
        raise ErroNegocio("Este fluxo segue o rito da despesa pública e é exclusivo de órgãos públicos.")
    o = buscar(con, "oferta", oferta_id)
    if not o["ativa"] or not credenciado(con, o["fornecedor_id"], hoje):
        raise ErroNegocio("Oferta indisponível ou fornecedor sem credenciamento válido.")
    if quantidade > o["quantidade_disponivel"]:
        raise ErroNegocio(f"Quantidade acima da disponível ({o['quantidade_disponivel']}).")
    if inventario_id is not None and buscar(con, "inventario", inventario_id)["organizacao_id"] != org["id"]:
        raise ErroNegocio("Inventário de outra organização.")
    s = simular(con, oferta_id, quantidade)
    pid = con.execute(
        """INSERT INTO pedido (orgao_id, oferta_id, inventario_id, quantidade, preco_unitario, taxa_percentual,
           valor_creditos, valor_taxa, valor_total, criado_por) VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (org["id"], oferta_id, inventario_id, quantidade, s["preco_unitario"], s["taxa_percentual"],
         s["valor_creditos"], s["valor_taxa"], s["valor_total"], usuario_id)).lastrowid
    registrar(con, usuario_id, "criar", "pedido", pid, depois=s)
    return pid


def instruir(con: sqlite3.Connection, usuario_id: int, pedido_id: int, *, processo: str) -> None:
    """Registra o processo administrativo em que a minuta de TR e a justificativa foram juntadas."""
    p, _ = _pedido_do_orgao(con, usuario_id, pedido_id)
    _exigir_status(p, "rascunho")
    if not processo.strip():
        raise ErroNegocio("Informe o número do processo administrativo.")
    con.execute("UPDATE pedido SET status='instruido', processo=? WHERE id=?", (processo.strip(), pedido_id))
    registrar(con, usuario_id, "instruir", "pedido", pedido_id, depois={"processo": processo})


def registrar_empenho(con: sqlite3.Connection, usuario_id: int, pedido_id: int, *, numero: str, data: str,
                      valor: float) -> None:
    """Empenho prévio (Lei 4.320/1964, art. 60). Reserva a quantidade na oferta."""
    with transacao(con):
        p, _ = _pedido_do_orgao(con, usuario_id, pedido_id)
        _exigir_status(p, "instruido")
        date.fromisoformat(data)
        if valor + 0.005 < p["valor_total"]:
            raise ErroNegocio(f"Empenho de R$ {valor:,.2f} inferior ao valor total do pedido "
                              f"(R$ {p['valor_total']:,.2f}).")
        o = buscar(con, "oferta", p["oferta_id"])
        if o["quantidade_disponivel"] < p["quantidade"]:
            raise ErroNegocio("A oferta não tem mais a quantidade pedida. Cancele e refaça o pedido.")
        con.execute("UPDATE oferta SET quantidade_disponivel = quantidade_disponivel - ? WHERE id=?",
                    (p["quantidade"], p["oferta_id"]))
        con.execute("""UPDATE pedido SET status='empenhado', empenho_numero=?, empenho_data=?, empenho_valor=?
                       WHERE id=?""", (numero.strip(), data, valor, pedido_id))
        registrar(con, usuario_id, "empenhar", "pedido", pedido_id,
                  depois={"numero": numero, "data": data, "valor": valor})


def entregar(con: sqlite3.Connection, usuario_id: int, pedido_id: int) -> dict:
    """Fornecedor entrega: transfere os créditos para a conta do órgão no registro."""
    p = buscar(con, "pedido", pedido_id)
    o = buscar(con, "oferta", p["oferta_id"])
    u = _u(con, usuario_id)
    if u["papel"] != "fornecedor" or u["organizacao_id"] != o["fornecedor_id"]:
        raise NaoAutorizado("Só o fornecedor da oferta faz a entrega.")
    _exigir_status(p, "empenhado")
    destino = abrir_conta(con, p["orgao_id"])
    r = transferir(con, usuario_id, lote_id=o["lote_id"], quantidade=p["quantidade"], para_conta=destino,
                   chave_idempotencia=f"pedido-{pedido_id}-entrega")
    con.execute("UPDATE pedido SET status='entregue', lote_entregue_id=? WHERE id=? AND status='empenhado'",
                (r["lote_id"], pedido_id))
    registrar(con, usuario_id, "entregar", "pedido", pedido_id, depois={"lote": r["lote_id"]})
    return r


def liquidar(con: sqlite3.Connection, usuario_id: int, pedido_id: int, *, nota_fiscal: str,
             conferido: bool) -> None:
    """Liquidação (Lei 4.320/1964, art. 63): o fiscal confere a entrega no registro e a nota fiscal."""
    p, u = _pedido_do_orgao(con, usuario_id, pedido_id, papeis=("fiscal",))
    _exigir_status(p, "entregue")
    if u["id"] == p["criado_por"]:
        raise NaoAutorizado("Quem pediu não pode atestar a liquidação (segregação de funções).")
    if not conferido:
        raise ErroNegocio("Confirme que conferiu a entrega dos créditos e a nota fiscal.")
    if not nota_fiscal.strip():
        raise ErroNegocio("Informe a nota fiscal.")
    conta = con.execute("SELECT id FROM conta WHERE organizacao_id=?", (p["orgao_id"],)).fetchone()
    entrega = con.execute(
        "SELECT para_conta, quantidade FROM movimento WHERE chave_idempotencia=?",
        (f"pedido-{pedido_id}-entrega",)).fetchone()
    if not entrega or entrega["para_conta"] != conta["id"] or entrega["quantidade"] != p["quantidade"]:
        raise ErroNegocio("A entrega não confere com o registro: liquidação bloqueada.")
    con.execute("UPDATE pedido SET status='liquidado', nota_fiscal=?, liquidado_por=? WHERE id=?",
                (nota_fiscal.strip(), usuario_id, pedido_id))
    registrar(con, usuario_id, "liquidar", "pedido", pedido_id, depois={"nota_fiscal": nota_fiscal})


def registrar_pagamento(con: sqlite3.Connection, usuario_id: int, pedido_id: int, *, referencia: str,
                        data: str) -> None:
    p, _ = _pedido_do_orgao(con, usuario_id, pedido_id)
    _exigir_status(p, "liquidado")
    date.fromisoformat(data)
    if not referencia.strip():
        raise ErroNegocio("Informe a ordem bancária ou o documento de pagamento.")
    con.execute("UPDATE pedido SET status='pago', pagamento_ref=?, pagamento_data=? WHERE id=?",
                (referencia.strip(), data, pedido_id))
    registrar(con, usuario_id, "pagar", "pedido", pedido_id, depois={"referencia": referencia, "data": data})


def cancelar_pedido(con: sqlite3.Connection, usuario_id: int, pedido_id: int, *, motivo: str) -> None:
    """Cancela antes da entrega. Se havia empenho, devolve a reserva (anule o empenho no financeiro)."""
    with transacao(con):
        p, _ = _pedido_do_orgao(con, usuario_id, pedido_id)
        _exigir_status(p, "rascunho", "instruido", "empenhado")
        if not motivo.strip():
            raise ErroNegocio("Informe o motivo do cancelamento.")
        if p["status"] == "empenhado":
            con.execute("UPDATE oferta SET quantidade_disponivel = quantidade_disponivel + ? WHERE id=?",
                        (p["quantidade"], p["oferta_id"]))
        con.execute("UPDATE pedido SET status='cancelado', motivo_cancelamento=? WHERE id=?",
                    (motivo.strip(), pedido_id))
        registrar(con, usuario_id, "cancelar", "pedido", pedido_id, antes={"status": p["status"]},
                  depois={"motivo": motivo})


def _sql_pedidos() -> str:
    return """SELECT p.*, o.nome AS orgao, f.nome AS fornecedor, f.id AS fornecedor_id, of.categoria, of.uf,
                     of.municipio, l.serial, l.projeto, l.tipo_ativo, l.certificadora, l.safra
              FROM pedido p JOIN organizacao o ON o.id=p.orgao_id JOIN oferta of ON of.id=p.oferta_id
              JOIN organizacao f ON f.id=of.fornecedor_id JOIN lote l ON l.id=of.lote_id"""


def listar_pedidos(con: sqlite3.Connection, usuario_id: int) -> list[dict]:
    u = _u(con, usuario_id)
    if u["papel"] in ("registrador", "auditor"):
        return [dict(r) for r in con.execute(_sql_pedidos() + " ORDER BY p.id DESC")]
    if u["papel"] == "fornecedor":
        return [dict(r) for r in con.execute(_sql_pedidos() + " WHERE of.fornecedor_id=? AND p.status<>'rascunho' "
                                             "ORDER BY p.id DESC", (u["organizacao_id"],))]
    return [dict(r) for r in con.execute(_sql_pedidos() + " WHERE p.orgao_id=? ORDER BY p.id DESC",
                                         (u["organizacao_id"],))]


def ver_pedido(con: sqlite3.Connection, usuario_id: int, pedido_id: int) -> dict:
    r = con.execute(_sql_pedidos() + " WHERE p.id=?", (pedido_id,)).fetchone()
    if r is None:
        raise NaoEncontrado("Pedido não encontrado.")
    u = _u(con, usuario_id)
    if not (u["papel"] in ("registrador", "auditor") or u["organizacao_id"] in (r["orgao_id"], r["fornecedor_id"])):
        raise NaoAutorizado("Pedido de outra organização.")
    return dict(r)


# ---------------------------------------------------------------- carteira
def carteira(con: sqlite3.Connection, organizacao_id: int) -> dict:
    """Situação de compensação do órgão: último inventário aprovado, créditos em conta e pedidos."""
    from .mrv import totais
    inv = con.execute("SELECT * FROM inventario WHERE organizacao_id=? AND status='aprovado' ORDER BY ano DESC "
                      "LIMIT 1", (organizacao_id,)).fetchone()
    base = compensado = 0.0
    if inv:
        t = totais(con, inv["id"])
        base = t["escopo1"] + t["escopo2"]
        compensado = con.execute("SELECT COALESCE(SUM(quantidade),0) FROM lote WHERE inventario_id=? AND "
                                 "status='cancelado' AND finalidade='compensacao_voluntaria'", (inv["id"],)).fetchone()[0]
    conta = con.execute("SELECT id FROM conta WHERE organizacao_id=?", (organizacao_id,)).fetchone()
    em_conta = con.execute("SELECT COALESCE(SUM(quantidade),0) FROM lote WHERE conta_id=? AND status='ativo'",
                           (conta["id"],)).fetchone()[0] if conta else 0
    pedidos = {r["status"]: {"quantidade": r["q"], "valor": round(r["v"], 2)} for r in con.execute(
        "SELECT status, SUM(quantidade) q, SUM(valor_total) v FROM pedido WHERE orgao_id=? GROUP BY status",
        (organizacao_id,))}
    a_compensar = max(round(base) - compensado, 0)
    return {"inventario_ano": inv["ano"] if inv else None, "emissoes_escopo1e2": round(base, 6),
            "compensado": compensado, "saldo_a_compensar": a_compensar,
            "percentual_compensado": round(min(compensado / base, 1) * 100, 1) if base else 0.0,
            "creditos_em_conta": em_conta, "pedidos": pedidos,
            "a_comprar": max(a_compensar - em_conta, 0)}


# ---------------------------------------------------------------- minuta de TR e justificativa de preço
def _brl(v: float) -> str:
    return "R$ " + f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def minuta_tr_html(con: sqlite3.Connection, usuario_id: int, pedido_id: int) -> str:
    p = ver_pedido(con, usuario_id, pedido_id)
    e = html.escape
    org = buscar(con, "organizacao", p["orgao_id"])
    precos = [r["preco_unitario"] for r in con.execute(
        "SELECT preco_unitario FROM oferta WHERE categoria=? AND ativa=1", (p["categoria"],))]
    pesquisa = (f"{len(precos)} oferta(s) da mesma categoria no catálogo: mínimo {_brl(min(precos))}, mediana "
                f"{_brl(statistics.median(precos))}, máximo {_brl(max(precos))} por tCO2e.") if precos else \
        "Sem ofertas comparáveis no catálogo."
    justificativa = ""
    if p["inventario_id"]:
        inv = buscar(con, "inventario", p["inventario_id"])
        from .mrv import totais
        t = totais(con, inv["id"])
        justificativa = (f"Compensação das emissões do inventário {inv['ano']} do órgão "
                         f"({t['escopo1'] + t['escopo2']:,.2f} tCO2e nos escopos 1 e 2), após verificação "
                         f"independente e aprovação, sem prejuízo das ações de redução.").replace(",", "X").replace(
            ".", ",").replace("X", ".")
    tipo = "Certificados de Redução ou Remoção Verificada de Emissões (CRVE), registrados no SBCE" \
        if p["tipo_ativo"] == "CRVE" else \
        "créditos de carbono do mercado voluntário certificados por padrão reconhecido internacionalmente"
    return f"""<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
<title>Minuta de Termo de Referência — pedido {p['id']}</title>
<style>body{{font:11pt/1.55 "Public Sans",Arial,sans-serif;color:#172A3A;max-width:52rem;margin:2rem auto;padding:0 1.5rem}}
h1{{font-size:16pt}} h2{{font-size:12pt;margin-top:1.4rem;border-bottom:2px solid #1F6B4F}}
.aviso{{border:2px solid #A3243B;color:#A3243B;padding:.6rem .8rem;font-weight:700}}
table{{border-collapse:collapse;width:100%}} td,th{{border:1px solid #D3DCD8;padding:.3rem .5rem;text-align:left}}
@media print{{.noprint{{display:none}}}}</style></head><body>
<p class="noprint"><button onclick="print()">Imprimir ou salvar em PDF</button></p>
<p class="aviso">MINUTA para instrução do processo. Deve ser revisada pela área demandante e submetida ao
controle prévio de legalidade da assessoria jurídica (Lei 14.133/2021, art. 53) antes de qualquer contratação.</p>
<h1>Termo de Referência — aquisição de créditos de carbono para compensação de emissões</h1>
<p>Órgão: {e(org['nome'])} · CNPJ {e(org['cnpj'])}{(' · Processo ' + e(p['processo'])) if p['processo'] else ''}</p>

<h2>1. Objeto</h2>
<p>Aquisição de {p['quantidade']:,} ({p['quantidade']}) unidades de {tipo}, cada uma equivalente a 1 tCO2e,
com <b>cancelamento (aposentadoria) em nome do órgão</b> no registro do padrão certificador, para fins de
compensação voluntária (Lei 15.042/2024, art. 45).</p>

<h2>2. Justificativa da necessidade</h2>
<p>{e(justificativa) or 'Descrever a necessidade, vinculando-a ao inventário de emissões aprovado e à política climática do órgão.'}
A compensação não substitui as medidas de redução previstas e não autoriza a alegação de neutralidade de
carbono sem lastro integral.</p>

<h2>3. Especificação técnica mínima</h2>
<ul>
<li>Créditos certificados por padrão reconhecido, com metodologia publicada e verificação por terceira parte independente.</li>
<li>Safra (vintage) não anterior a cinco anos da data da contratação.</li>
<li>Registro público e rastreável dos números de série; vedação à dupla contagem (Lei 15.042/2024, art. 2º, IX).</li>
<li>Entrega mediante comprovante de cancelamento/aposentadoria em nome do órgão, com número de registro verificável.</li>
<li>Preferência, sem caráter eliminatório, a projetos localizados no estado (critério a justificar no ETP).</li>
</ul>

<h2>4. Modelo de execução e de pagamento</h2>
<p>Pagamento somente após empenho prévio (Lei 4.320/1964, art. 60), entrega comprovada e liquidação atestada pelo
fiscal do contrato (art. 63). O fornecedor deverá manter as condições de habilitação durante toda a execução.</p>

<h2>5. Estimativa de valor e pesquisa de preços</h2>
<table><tr><th>Quantidade</th><td>{p['quantidade']:,} tCO2e</td></tr>
<tr><th>Preço unitário de referência</th><td>{_brl(p['preco_unitario'])}</td></tr>
<tr><th>Valor dos créditos</th><td>{_brl(p['valor_creditos'])}</td></tr>
<tr><th>Taxa de intermediação (se adquirido via plataforma)</th><td>{p['taxa_percentual']:g}% = {_brl(p['valor_taxa'])}</td></tr>
<tr><th>Valor total estimado</th><td><b>{_brl(p['valor_total'])}</b></td></tr></table>
<p>Fonte 1 (catálogo da plataforma): {e(pesquisa)}</p>
<p>A pesquisa de preços deve ser completada com outras fontes do art. 23, § 1º, da Lei 14.133/2021 (painel de
preços, contratações similares, mídia especializada, cotações diretas). Fonte 2: ______. Fonte 3: ______.</p>

<h2>6. Forma de seleção</h2>
<p>Definir no ETP: licitação, dispensa por valor (art. 75, II, observado o limite atualizado por decreto) ou
outra hipótese legal, com a devida motivação. Este Termo não exige aquisição por plataforma ou fornecedor
específico.</p>

<h2>7. Fundamentação</h2>
<p>Lei 14.133/2021 (arts. 5º, 6º, XXIII, 11, IV, 18, 23, 40 e 53); Lei 4.320/1964 (arts. 60 a 64); Lei 15.042/2024
(arts. 2º, 43 a 45); Lei 12.187/2009.</p>
</body></html>"""
