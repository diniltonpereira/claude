"""Integração com o Portal Nacional de Contratações Públicas (PNCP), só pelas APIs públicas de consulta.

A publicação no PNCP é feita pelo sistema de compras do próprio órgão (com a credencial dele). A
plataforma usa o PNCP para duas coisas, sem credencial:

1. Vincular o pedido à contratação divulgada: confere que o nº de controle existe e é do próprio
   órgão, e guarda um retrato (objeto, modalidade, situação, valores, data). A divulgação no PNCP é
   condição de eficácia do contrato (Lei 14.133/2021, art. 94); sem o vínculo, o pedido mostra alerta.
2. Pesquisa de preços com contratações similares (art. 23, § 1º, II): a pessoa informa o nº de
   controle de uma compra de créditos de outro ente, a plataforma lista os itens com valores
   estimados e homologados, e o item escolhido vira uma fonte citável no ETP e no TR.

Endpoints (conferidos no OpenAPI "API PNCP CONSULTA" e nas respostas reais):
    GET {base}/consulta/v1/orgaos/{cnpj}/compras/{ano}/{sequencial}
    GET {base}/pncp/v1/orgaos/{cnpj}/compras/{ano}/{sequencial}/itens?pagina=&tamanhoPagina=
    GET {base}/pncp/v1/orgaos/{cnpj}/compras/{ano}/{sequencial}/itens/{numeroItem}/resultados
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone

from .auditoria import registrar
from .banco import ErroNegocio, NaoAutorizado, buscar

_CONTROLE = re.compile(r"(\d{14})-\d-(\d{1,6})/(\d{4})")
_LINK = re.compile(r"pncp\.gov\.br/app/editais/(\d{14})/(\d{4})/(\d{1,6})")
VALIDADE_PESQUISA_DIAS = 365      # fontes mais antigas que um ano saem da estimativa
MAX_ITENS = 500


def _base() -> str:
    return os.environ.get("PNCP_URL", "https://pncp.gov.br/api").rstrip("/")


def interpretar(texto: str) -> tuple[str, int, int]:
    """Aceita o nº de controle (CNPJ-1-SEQUENCIAL/ANO) ou o link do portal. Devolve (cnpj, ano, sequencial)."""
    t = re.sub(r"[.\s]", "", texto or "")
    if m := _CONTROLE.search(t):
        return m.group(1), int(m.group(3)), int(m.group(2))
    if m := _LINK.search(texto or ""):
        return m.group(1), int(m.group(2)), int(m.group(3))
    raise ErroNegocio("Informe o nº de controle PNCP (ex.: 11111111000191-1-000012/2026) ou o link da contratação "
                      "no portal pncp.gov.br.")


def controle(cnpj: str, ano: int, seq: int) -> str:
    return f"{cnpj}-1-{seq:06d}/{ano}"


def link_portal(cnpj: str, ano: int, seq: int) -> str:
    return f"https://pncp.gov.br/app/editais/{cnpj}/{ano}/{seq}"


def _get(http, caminho: str, **params):
    try:
        r = http.get(f"{_base()}{caminho}", params=params or None)
    except Exception as exc:  # rede
        raise ErroNegocio("O PNCP não respondeu. Tente de novo em instantes.") from exc
    if r.status_code in (204, 404):
        return None
    if r.status_code != 200:
        raise ErroNegocio(f"O PNCP respondeu com erro ({r.status_code}). Tente de novo em instantes.")
    return r.json() if r.content else None


def _cliente(cliente):
    if cliente is not None:
        return cliente
    import httpx
    return httpx.Client(timeout=20, headers={"Accept": "application/json"})


def consultar_compra(cnpj: str, ano: int, seq: int, cliente=None) -> dict:
    d = _get(_cliente(cliente), f"/consulta/v1/orgaos/{cnpj}/compras/{ano}/{seq}")
    if not d:
        raise ErroNegocio(f"Contratação {controle(cnpj, ano, seq)} não encontrada no PNCP.")
    return d


def _retrato(d: dict, cnpj: str, ano: int, seq: int) -> dict:
    org = d.get("orgaoEntidade") or {}
    uni = d.get("unidadeOrgao") or {}
    return {"numeroControlePNCP": d.get("numeroControlePNCP") or controle(cnpj, ano, seq),
            "orgao": org.get("razaoSocial"), "cnpj": org.get("cnpj") or cnpj,
            "unidade": uni.get("nomeUnidade"), "municipio": uni.get("municipioNome"), "uf": uni.get("ufSigla"),
            "objeto": " ".join(str(d.get("objetoCompra") or "").split()), "modalidade": d.get("modalidadeNome"),
            "situacao": d.get("situacaoCompraNome"), "processo": d.get("processo"),
            "valor_estimado": d.get("valorTotalEstimado"), "valor_homologado": d.get("valorTotalHomologado"),
            "publicado_em": d.get("dataPublicacaoPncp"), "link": link_portal(cnpj, ano, seq)}


# ---------------------------------------------------------------- 1. vínculo do pedido
def vincular_pedido(con: sqlite3.Connection, usuario_id: int, pedido_id: int, numero: str, cliente=None) -> dict:
    from .marketplace import _pedido_do_orgao
    p, _ = _pedido_do_orgao(con, usuario_id, pedido_id)
    if p["status"] == "cancelado":
        raise ErroNegocio("Pedido cancelado.")
    cnpj, ano, seq = interpretar(numero)
    org = buscar(con, "organizacao", p["orgao_id"])
    if cnpj != re.sub(r"\D", "", org["cnpj"]):
        raise ErroNegocio(f"A contratação {controle(cnpj, ano, seq)} é de outro órgão (CNPJ {cnpj}). Informe a "
                          "contratação divulgada pelo seu órgão.")
    retrato = _retrato(consultar_compra(cnpj, ano, seq, cliente), cnpj, ano, seq)
    agora = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    con.execute("UPDATE pedido SET pncp_controle=?, pncp_dados=?, pncp_conferido_em=? WHERE id=?",
                (retrato["numeroControlePNCP"], json.dumps(retrato, ensure_ascii=False), agora, pedido_id))
    registrar(con, usuario_id, "vincular_pncp", "pedido", pedido_id,
              antes={"pncp_controle": p["pncp_controle"]} if p["pncp_controle"] else None,
              depois={"pncp_controle": retrato["numeroControlePNCP"], "situacao": retrato["situacao"]})
    return retrato


def alerta_pedido(p: dict) -> str | None:
    if p["status"] in ("empenhado", "entregue", "liquidado", "pago") and not p.get("pncp_controle"):
        return ("Contratação ainda não vinculada ao PNCP. A divulgação no PNCP é condição de eficácia do contrato "
                "(Lei 14.133/2021, art. 94): informe o nº de controle publicado pelo sistema de compras do órgão.")
    return None


# ---------------------------------------------------------------- 2. pesquisa de preços
def itens_da_compra(numero: str, cliente=None) -> dict:
    """Lista os itens de uma contratação de outro ente, com valor estimado e, se houver, homologado."""
    cnpj, ano, seq = interpretar(numero)
    http = _cliente(cliente)
    compra = _retrato(consultar_compra(cnpj, ano, seq, http), cnpj, ano, seq)
    itens, pagina = [], 1
    while len(itens) < MAX_ITENS:
        lote = _get(http, f"/pncp/v1/orgaos/{cnpj}/compras/{ano}/{seq}/itens", pagina=pagina, tamanhoPagina=50)
        if not lote:
            break
        itens.extend(lote)
        if len(lote) < 50:
            break
        pagina += 1
    saida = []
    for it in itens:
        homologado = None
        if it.get("temResultado"):
            res = _get(http, f"/pncp/v1/orgaos/{cnpj}/compras/{ano}/{seq}/itens/{it['numeroItem']}/resultados") or []
            res = res if isinstance(res, list) else [res]
            validos = [r for r in res if r.get("valorUnitarioHomologado")]
            if validos:
                homologado = {"valor": validos[0]["valorUnitarioHomologado"], "data": validos[0].get("dataResultado"),
                              "fornecedor": validos[0].get("nomeRazaoSocialFornecedor")}
        saida.append({"numero": it["numeroItem"], "descricao": " ".join(str(it.get("descricao") or "").split()),
                      "unidade": it.get("unidadeMedida") or "", "quantidade": it.get("quantidade"),
                      "valor_estimado": None if it.get("orcamentoSigiloso") else it.get("valorUnitarioEstimado"),
                      "homologado": homologado})
    return {"compra": compra, "itens": saida}


def registrar_fonte(con: sqlite3.Connection, usuario_id: int, *, numero: str, numero_item: int,
                    tco2e_por_unidade: float = 1.0, cliente=None) -> int:
    """Guarda um item de contratação similar como fonte de preço (valor homologado, ou estimado se não houver)."""
    u = buscar(con, "usuario", usuario_id)
    if u["papel"] != "elaborador" or not u["organizacao_id"]:
        raise NaoAutorizado("Só o elaborador do órgão registra fontes da pesquisa de preços.")
    if tco2e_por_unidade <= 0:
        raise ErroNegocio("Informe quantas tCO2e cada unidade do item representa.")
    dados = itens_da_compra(numero, cliente)
    it = next((i for i in dados["itens"] if i["numero"] == numero_item), None)
    if it is None:
        raise ErroNegocio(f"Item {numero_item} não encontrado nesta contratação.")
    c = dados["compra"]
    if it["homologado"]:
        valor, tipo, data = it["homologado"]["valor"], "homologado", it["homologado"]["data"] or c["publicado_em"]
    elif it["valor_estimado"]:
        valor, tipo, data = it["valor_estimado"], "estimado", c["publicado_em"]
    else:
        raise ErroNegocio("O item não tem valor divulgado (orçamento sigiloso ou ainda sem resultado).")
    referencia = f"{c['numeroControlePNCP']}#item{numero_item}"
    try:
        fid = con.execute(
            """INSERT INTO pesquisa_preco (organizacao_id, fonte, referencia, link, orgao_contratante, objeto,
               item_descricao, unidade, tco2e_por_unidade, valor_unitario, tipo_valor, preco_por_tco2e,
               data_referencia, coletado_por) VALUES (?,'pncp',?,?,?,?,?,?,?,?,?,?,?,?)""",
            (u["organizacao_id"], referencia, c["link"], c["orgao"] or c["cnpj"], c["objeto"][:500],
             it["descricao"][:500], it["unidade"][:60], tco2e_por_unidade, valor, tipo,
             round(valor / tco2e_por_unidade, 4), str(data)[:10], usuario_id)).lastrowid
    except sqlite3.IntegrityError as exc:
        raise ErroNegocio("Este item já está na pesquisa de preços do órgão.") from exc
    registrar(con, usuario_id, "registrar_fonte_preco", "pesquisa_preco", fid,
              depois={"referencia": referencia, "preco_por_tco2e": round(valor / tco2e_por_unidade, 4), "tipo": tipo})
    return fid


def descartar_fonte(con: sqlite3.Connection, usuario_id: int, fonte_id: int) -> None:
    f = buscar(con, "pesquisa_preco", fonte_id)
    u = buscar(con, "usuario", usuario_id)
    if u["papel"] != "elaborador" or u["organizacao_id"] != f["organizacao_id"]:
        raise NaoAutorizado("Fonte de outro órgão.")
    agora = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    con.execute("UPDATE pesquisa_preco SET descartado_em=? WHERE id=? AND descartado_em IS NULL", (agora, fonte_id))
    registrar(con, usuario_id, "descartar_fonte_preco", "pesquisa_preco", fonte_id)


def fontes(con: sqlite3.Connection, organizacao_id: int, vigentes: bool = True) -> list[dict]:
    """Fontes do órgão; `vigentes` deixa de fora as descartadas e as com mais de um ano."""
    sql = "SELECT * FROM pesquisa_preco WHERE organizacao_id=?"
    args: tuple = (organizacao_id,)
    if vigentes:
        corte = (datetime.now(timezone.utc) - timedelta(days=VALIDADE_PESQUISA_DIAS)).strftime("%Y-%m-%d")
        sql += " AND descartado_em IS NULL AND data_referencia >= ?"
        args += (corte,)
    return [dict(r) for r in con.execute(sql + " ORDER BY data_referencia DESC, id DESC", args)]
