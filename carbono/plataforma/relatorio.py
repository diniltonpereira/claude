"""Relatório do inventário: HTML pronto para impressão em PDF e planilha Excel.

Conteúdo: identificação, situação e trilha de aprovação, metodologia (GHG Protocol, ABNT NBR
ISO 14064-1, conjunto de GWP), fatores com versão e fonte, memória de cálculo de cada atividade,
anexos com SHA-256, pendências excluídas, emissões evitadas à parte, enquadramento no SBCE e
declaração. O texto evita "carbono neutro" e "pronto para SBCE": os modelos oficiais do órgão
gestor do SBCE ainda não foram publicados.
"""
from __future__ import annotations

import html
import io
import sqlite3
from datetime import date

from .banco import buscar
from .enquadramento import enquadrar_operador
from .mrv import historico, totais, totais_por_instalacao

e = html.escape


def _dados(con: sqlite3.Connection, inventario_id: int) -> dict:
    inv = buscar(con, "inventario", inventario_id)
    org = buscar(con, "organizacao", inv["organizacao_id"])
    atividades = [dict(r) for r in con.execute(
        """SELECT a.*, i.nome AS unidade, f.codigo AS fator_codigo, f.versao AS fator_versao,
                  f.fonte AS fator_fonte, x.sha256 AS anexo_sha256, x.nome_original AS anexo_nome
           FROM atividade a LEFT JOIN instalacao i ON i.id=a.instalacao_id
           LEFT JOIN fator_emissao f ON f.id=a.fator_id LEFT JOIN anexo x ON x.id=a.anexo_id
           WHERE a.inventario_id=? ORDER BY a.escopo, a.id""", (inventario_id,))]
    fatores_usados = [dict(r) for r in con.execute(
        """SELECT DISTINCT f.codigo, f.versao, f.descricao, f.unidade, f.valor, f.fonte, f.ano_referencia
           FROM atividade a JOIN fator_emissao f ON f.id=a.fator_id WHERE a.inventario_id=?
           ORDER BY f.codigo""", (inventario_id,))]
    enq = enquadrar_operador(org["tipo"], totais_por_instalacao(con, inventario_id), esfera=org["esfera"])
    return {"inv": dict(inv), "org": dict(org), "totais": totais(con, inventario_id),
            "atividades": atividades, "fatores": fatores_usados, "historico": historico(con, inventario_id),
            "enquadramento": enq}


def _n(v) -> str:
    return f"{v:,.3f}".replace(",", "X").replace(".", ",").replace("X", ".")


def html_relatorio(con: sqlite3.Connection, inventario_id: int, base_url: str = "") -> str:
    d = _dados(con, inventario_id)
    inv, org, t = d["inv"], d["org"], d["totais"]
    comprovadas = [a for a in d["atividades"] if a["anexo_id"] or a["contrato_id"]]
    pendentes = [a for a in d["atividades"] if not (a["anexo_id"] or a["contrato_id"])]
    aprovado = inv["status"] == "aprovado"

    def linhas(lista):
        return "".join(
            f"<tr><td>{a['escopo']}</td><td>{e(a['descricao'])}<div class='m'>{e(a['memoria'])}</div>"
            f"{'<div class=m>Anexo: ' + e(a['anexo_nome']) + ' · SHA-256 ' + a['anexo_sha256'] + '</div>' if a['anexo_sha256'] else ''}"
            f"</td><td>{e(a['unidade'] or '')}</td><td>{a['mes'] or ''}</td>"
            f"<td class=n>{_n(a['tco2e'])}</td></tr>" for a in lista)

    enq = d["enquadramento"]
    obrig = "".join(f"<li><b>{e(o['titulo'])}</b> — {e(o['detalhe'])} <i>({e(o['fundamento'])})</i></li>"
                    for o in enq["obrigacoes"])
    trilha = "".join(f"<li>{e(h['em'][:16].replace('T', ' '))} — {e(h['usuario_nome'])}: "
                     f"{e(h['de_status'])} → {e(h['para_status'])}"
                     f"{' — parecer: ' + e(h['parecer']) if h['parecer'] else ''}</li>" for h in d["historico"])
    fat = "".join(f"<tr><td>{e(f['codigo'])} v{f['versao']}</td><td>{e(f['descricao'])}</td>"
                  f"<td class=n>{f['valor']:g} tCO2e/{e(f['unidade'])}</td><td>{e(f['fonte'])} ({f['ano_referencia']})</td></tr>"
                  for f in d["fatores"])
    return f"""<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
<title>Inventário de emissões {inv['ano']} — {e(org['nome'])}</title>
<style>
 body{{font:11pt/1.5 "Public Sans",Arial,sans-serif;color:#172A3A;max-width:60rem;margin:2rem auto;padding:0 1.5rem}}
 h1{{font-size:18pt;margin:0}} h2{{font-size:13pt;margin:1.6rem 0 .4rem;border-bottom:2px solid #1F6B4F;padding-bottom:.2rem}}
 table{{width:100%;border-collapse:collapse;font-size:9.5pt}} th,td{{border-bottom:1px solid #D3DCD8;padding:.3rem;text-align:left;vertical-align:top}}
 .n{{text-align:right;white-space:nowrap}} .m{{color:#4A5D6B;font-size:8.5pt}} .rascunho{{color:#A3243B;font-weight:700}}
 .cifras{{display:flex;gap:2rem;flex-wrap:wrap}} .cifras b{{display:block;font-size:14pt}}
 @media print{{body{{margin:0}} .noprint{{display:none}}}}
</style></head><body>
<p class="noprint">Para salvar em PDF, use a impressão do navegador (Ctrl+P).</p>
<h1>Inventário de emissões de gases de efeito estufa — {inv['ano']}</h1>
<p>{e(org['nome'])} · CNPJ {e(org['cnpj'])} · Situação: <span class="{'' if aprovado else 'rascunho'}">{e(inv['status'])}</span>
{'' if aprovado else ' — <b>documento preliminar, sem validade até a aprovação</b>'}</p>

<h2>Resultados (somente lançamentos comprovados)</h2>
<div class="cifras">
 <div>Escopo 1<b>{_n(t['escopo1'])}</b>tCO2e</div><div>Escopo 2<b>{_n(t['escopo2'])}</b>tCO2e</div>
 <div>Escopo 3<b>{_n(t['escopo3'])}</b>tCO2e</div><div>Total<b>{_n(t['total'])}</b>tCO2e</div>
 <div>Remoções<b>{_n(t['remocoes'])}</b>tCO2e</div><div>CO2 biogênico<b>{_n(t['co2_biogenico'])}</b>tCO2</div>
</div>
<p class="m">Emissões evitadas, relatadas à parte e não abatidas do total: {_n(t['emissoes_evitadas'])} tCO2e.</p>

<h2>Metodologia</h2>
<p>GHG Protocol (Programa Brasileiro GHG Protocol) e ABNT NBR ISO 14064-1. Potenciais de aquecimento global:
IPCC {e(inv['gwp_conjunto'])}, horizonte de 100 anos (Lei 15.042/2024, art. 2º, XXXIII). Escopo 2 pela abordagem
por localização. Emissões de terceirizadas no escopo 3, somente com comprovação. Cada lançamento guarda a
versão exata do fator utilizado.</p>
<table><thead><tr><th>Fator</th><th>Descrição</th><th class=n>Valor</th><th>Fonte</th></tr></thead><tbody>{fat}</tbody></table>

<h2>Atividades e memória de cálculo</h2>
<table><thead><tr><th>Esc.</th><th>Atividade</th><th>Unidade</th><th>Mês</th><th class=n>tCO2e</th></tr></thead>
<tbody>{linhas(comprovadas)}</tbody></table>
{f'<h2>Pendentes de comprovação (fora do total)</h2><table><tbody>{linhas(pendentes)}</tbody></table>' if pendentes else ''}

<h2>Enquadramento no SBCE (Lei 15.042/2024)</h2>
<p>Faixa: <b>{e(enq['faixa_sbce'])}</b> · {e(enq['fase_sbce'])}. Estruturado conforme a lei; os modelos oficiais de
plano de monitoramento e relato serão definidos pelo órgão gestor do SBCE.</p>
<ul>{obrig}</ul>

<h2>Trilha de verificação e aprovação</h2><ul>{trilha or '<li>Sem eventos.</li>'}</ul>

<h2>Declaração</h2>
<p>Declaramos que as informações deste inventário correspondem aos documentos anexados no sistema, cujos
hashes SHA-256 constam acima, e que os cálculos seguem a metodologia descrita. Este documento não afirma
neutralidade de carbono.</p>
<p class="m">Gerado em {date.today():%d/%m/%Y}. Integridade dos anexos verificável pelo SHA-256.
{('Registro público de cancelamentos: ' + e(base_url) + '/painel/#/transparencia') if base_url else ''}</p>
</body></html>"""


def xlsx_relatorio(con: sqlite3.Connection, inventario_id: int) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font

    d = _dados(con, inventario_id)
    wb = Workbook()
    ws = wb.active
    ws.title = "Resumo"
    t = d["totais"]
    ws.append(["Organização", d["org"]["nome"]])
    ws.append(["CNPJ", d["org"]["cnpj"]])
    ws.append(["Ano", d["inv"]["ano"]])
    ws.append(["Situação", d["inv"]["status"]])
    ws.append(["GWP", d["inv"]["gwp_conjunto"]])
    ws.append([])
    for k in ("escopo1", "escopo2", "escopo3", "total", "remocoes", "co2_biogenico", "emissoes_evitadas"):
        ws.append([k, t[k]])
    ws.append(["pendentes (fora do total)", t["pendentes_comprovacao"]["tco2e"]])
    a = wb.create_sheet("Atividades")
    cab = ["id", "escopo", "natureza", "descrição", "unidade", "mês", "quantidade", "fator", "versão", "tCO2e",
           "CO2 biogênico", "comprovada", "anexo", "SHA-256", "memória"]
    a.append(cab)
    for c in a[1]:
        c.font = Font(bold=True)
    for x in d["atividades"]:
        a.append([x["id"], x["escopo"], x["natureza"], x["descricao"], x["unidade"], x["mes"], x["quantidade"],
                  x["fator_codigo"], x["fator_versao"], x["tco2e"], x["tco2_biogenico"],
                  "sim" if (x["anexo_id"] or x["contrato_id"]) else "não", x["anexo_nome"], x["anexo_sha256"],
                  x["memoria"]])
    f = wb.create_sheet("Fatores")
    f.append(["código", "versão", "descrição", "unidade", "valor", "fonte", "ano"])
    for x in d["fatores"]:
        f.append([x["codigo"], x["versao"], x["descricao"], x["unidade"], x["valor"], x["fonte"],
                  x["ano_referencia"]])
    h = wb.create_sheet("Trilha")
    h.append(["em", "usuário", "de", "para", "parecer"])
    for x in d["historico"]:
        h.append([x["em"], x["usuario_nome"], x["de_status"], x["para_status"], x["parecer"]])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
