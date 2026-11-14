"""Modelos editáveis em Word (.docx), preenchidos com os dados da plataforma.

- ETP da compra de créditos para compensação (Lei 14.133/2021, art. 18, § 1º);
- Termo de Referência do pedido (Lei 14.133/2021, art. 6º, XXIII, alíneas "a" a "j");
- Relatório de emissões do inventário, para o órgão completar com as ações e publicar.

São MINUTAS: todas trazem o aviso de controle prévio de legalidade (art. 53) e marcam em amarelo,
como [PREENCHER: …], o que só a área demandante sabe. Os números vêm da plataforma e podem ser
conferidos (inventário aprovado, fatores com versão, SHA-256 dos comprovantes).
"""
from __future__ import annotations

import io
import math
import sqlite3
import statistics
from datetime import date

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_COLOR_INDEX
from docx.shared import Pt, RGBColor

from .banco import ErroNegocio, buscar

VERDE, CARMIM = RGBColor(0x1F, 0x6B, 0x4F), RGBColor(0xA3, 0x24, 0x3B)
AVISO_RELATORIO = ("MINUTA de relatório gerada pela plataforma. Complete os trechos em amarelo, revise o texto e "
                   "publique só depois da aprovação do inventário. Os números vêm do inventário na plataforma.")
AVISO = ("MINUTA gerada pela plataforma para instruir o processo. Deve ser revisada pela área demandante "
         "e submetida ao controle prévio de legalidade da assessoria jurídica (Lei 14.133/2021, art. 53). "
         "Confira a redação dos dispositivos citados na versão vigente da lei. Os trechos em amarelo "
         "precisam ser preenchidos.")


# ---------------------------------------------------------------- formatação
def _num(v: float, casas: int = 2) -> str:
    return f"{v:,.{casas}f}".translate(str.maketrans(",.", ".,"))


def _fator(v: float) -> str:
    """Fator sem notação científica: 3.85e-05 -> '0,0000385'."""
    return f"{v:.10f}".rstrip("0").rstrip(".").replace(".", ",")


def _brl(v: float) -> str:
    return "R$ " + _num(v)


class _Doc:
    """Pequena camada sobre o python-docx com o padrão visual dos modelos."""

    def __init__(self, titulo: str, subtitulo: str, aviso: str = AVISO):
        self.d = Document()
        estilo = self.d.styles["Normal"]
        estilo.font.name, estilo.font.size = "Arial", Pt(11)
        for nome in ("Heading 1", "Heading 2"):
            self.d.styles[nome].font.name = "Arial"
            self.d.styles[nome].font.color.rgb = VERDE
        p = self.d.add_paragraph()
        r = p.add_run(aviso)
        r.bold, r.font.size, r.font.color.rgb = True, Pt(9), CARMIM
        self.d.add_heading(titulo, level=1)
        self.d.add_paragraph(subtitulo)
        rodape = self.d.sections[0].footer.paragraphs[0]
        rodape.text = f"Minuta gerada pelo CarbonMG em {date.today():%d/%m/%Y}. Documento editável."
        rodape.runs[0].font.size = Pt(8)

    def secao(self, titulo: str) -> None:
        self.d.add_heading(titulo, level=2)

    def par(self, *partes) -> None:
        """Parágrafo com trechos: str = texto; ("preencher", dica) = campo destacado; ("b", texto) = negrito."""
        p = self.d.add_paragraph()
        for parte in partes:
            if isinstance(parte, str):
                p.add_run(parte)
            elif parte[0] == "preencher":
                r = p.add_run(f"[PREENCHER: {parte[1]}]")
                r.font.highlight_color = WD_COLOR_INDEX.YELLOW
            elif parte[0] == "b":
                p.add_run(parte[1]).bold = True

    def itens(self, linhas: list[str]) -> None:
        for linha in linhas:
            self.d.add_paragraph(linha, style="List Bullet")

    def tabela(self, cabecalho: list[str], linhas: list[list[str]]) -> None:
        t = self.d.add_table(rows=1, cols=len(cabecalho))
        t.style, t.alignment = "Light Grid Accent 1", WD_TABLE_ALIGNMENT.CENTER
        for cel, txt in zip(t.rows[0].cells, cabecalho):
            cel.text = txt
            cel.paragraphs[0].runs[0].bold = True
        for linha in linhas:
            for cel, txt in zip(t.add_row().cells, linha):
                cel.text = str(txt)
        self.d.add_paragraph()

    def bytes(self) -> bytes:
        b = io.BytesIO()
        self.d.save(b)
        return b.getvalue()


def _precos_catalogo(con: sqlite3.Connection, categoria: str | None = None) -> dict | None:
    sql, args = "SELECT preco_unitario FROM oferta WHERE ativa=1", ()
    if categoria:
        sql, args = sql + " AND categoria=?", (categoria,)
    precos = [r[0] for r in con.execute(sql, args)]
    if not precos:
        return None
    return {"n": len(precos), "min": min(precos), "mediana": statistics.median(precos), "max": max(precos)}


def _fontes_pncp(d: "_Doc", con: sqlite3.Connection, organizacao_id: int) -> list[float]:
    """Tabela das contratações similares do PNCP (art. 23, § 1º, II). Devolve os preços por tCO2e."""
    from .pncp import fontes
    fs = fontes(con, organizacao_id)
    if not fs:
        d.par("Contratações similares no PNCP (art. 23, § 1º, II): ",
              ("preencher", "nenhuma registrada na plataforma; registre em \"Pesquisa de preços\" ou descreva"))
        return []
    d.par(f"Contratações similares divulgadas no PNCP (art. 23, § 1º, II), coletadas nos últimos 12 meses ({len(fs)}):")
    d.tabela(["Órgão contratante", "Nº de controle e item", "Item", "Valor", "R$/tCO2e", "Data"],
             [[f["orgao_contratante"], f["referencia"], f["item_descricao"][:80], f["tipo_valor"],
               _brl(f["preco_por_tco2e"]), f["data_referencia"]] for f in fs])
    return [f["preco_por_tco2e"] for f in fs]


# ---------------------------------------------------------------- ETP
def etp_compensacao(con: sqlite3.Connection, inventario_id: int) -> bytes:
    """ETP da aquisição de créditos para compensar as emissões do inventário aprovado (escopos 1 e 2)."""
    from .compensacao import ja_compensado
    from .marketplace import CATEGORIAS, taxa_percentual
    from .mrv import totais
    inv = buscar(con, "inventario", inventario_id)
    if inv["status"] != "aprovado":
        raise ErroNegocio("O ETP de compensação parte do inventário aprovado (verificado e moderado).")
    org = buscar(con, "organizacao", inv["organizacao_id"])
    t = totais(con, inventario_id)
    base = t["escopo1"] + t["escopo2"]
    feito = ja_compensado(con, inventario_id, "compensacao_voluntaria")
    qtd = max(math.ceil(base - feito), 0)
    precos = _precos_catalogo(con)
    taxa = taxa_percentual()

    d = _Doc("Estudo Técnico Preliminar — aquisição de créditos de carbono para compensação de emissões",
             f"{org['nome']} · CNPJ {org['cnpj']} · inventário de emissões {inv['ano']}")
    d.par("Processo: ", ("preencher", "nº do processo administrativo"), " · Área demandante: ",
          ("preencher", "unidade e responsável"))

    d.secao("I. Descrição da necessidade (art. 18, § 1º, I)")
    d.par(f"O inventário de emissões de gases de efeito estufa de {inv['ano']} do órgão, elaborado conforme o "
          "GHG Protocol e a ABNT NBR ISO 14064-1, verificado por terceira parte e aprovado, apurou ",
          ("b", f"{_num(t['escopo1'])} tCO2e no escopo 1 e {_num(t['escopo2'])} tCO2e no escopo 2"),
          ". A necessidade é compensar essas emissões por meio do cancelamento de créditos de carbono "
          "certificados (Lei 15.042/2024, art. 45), como etapa complementar — e não substituta — das medidas de "
          "redução previstas em ", ("preencher", "plano ou política climática do órgão"), ".")
    if feito:
        d.par(f"Já foram compensadas {feito:,} tCO2e deste inventário; a necessidade remanescente é a diferença.".replace(",", "."))

    d.secao("II. Previsão no plano de contratações anual (art. 18, § 1º, II)")
    d.par(("preencher", "item do plano de contratações anual, ou justificativa da ausência"))

    d.secao("III. Requisitos da contratação (art. 18, § 1º, III)")
    d.itens(["Créditos certificados por padrão reconhecido, com metodologia publicada e verificação por terceira "
             "parte independente; ou Certificados de Redução ou Remoção Verificada de Emissões (CRVE) do SBCE.",
             "Registro público com número de série rastreável; vedação à dupla contagem.",
             "Safra (ano de geração) não posterior ao ano do inventário e não anterior a cinco anos.",
             "Entrega mediante comprovante de cancelamento (aposentadoria) em nome do órgão, verificável no "
             "registro do padrão certificador.",
             "Regularidade fiscal e trabalhista do fornecedor durante toda a execução."])

    d.secao("IV. Estimativa das quantidades e memória de cálculo (art. 18, § 1º, IV)")
    d.tabela(["Item", "tCO2e"], [
        ["Escopo 1 (emissões diretas)", _num(t["escopo1"])],
        ["Escopo 2 (energia adquirida)", _num(t["escopo2"])],
        ["Base da compensação (escopos 1 e 2)", _num(base)],
        ["Já compensado", _num(feito, 0)],
        ["Quantidade a adquirir (arredondada para cima)", _num(qtd, 0)],
    ])
    d.par(f"Somente as atividades com comprovante entram no total. O escopo 3 ({_num(t['escopo3'])} tCO2e) e as "
          "emissões evitadas não compõem a base, salvo decisão motivada do órgão: ",
          ("preencher", "incluir ou não o escopo 3, com a justificativa"), ".")

    d.secao("V. Levantamento de mercado e escolha da solução (art. 18, § 1º, V)")
    d.tabela(["Alternativa", "Análise"], [
        ["Somente ações de redução", "Necessárias e prioritárias, mas não eliminam as emissões residuais do período."],
        ["Créditos do mercado voluntário", "Disponíveis hoje; exigem padrão reconhecido e cancelamento em nome do órgão."],
        ["CRVE do SBCE", "Preferíveis quando houver oferta; o mercado regulado ainda está em implantação (art. 50)."],
        ["Compra direta de projetos próprios", "Possível no longo prazo (fase de projetos); não atende o período."],
    ])
    if precos:
        d.par(f"Pesquisa no catálogo da plataforma (uma das fontes do art. 23): {precos['n']} oferta(s) ativa(s), "
              f"mínimo {_brl(precos['min'])}, mediana {_brl(precos['mediana'])} e máximo {_brl(precos['max'])} por tCO2e. "
              "Por categoria:")
        linhas = []
        for cod, nome in CATEGORIAS.items():
            pc = _precos_catalogo(con, cod)
            if pc:
                linhas.append([nome, str(pc["n"]), _brl(pc["min"]), _brl(pc["mediana"]), _brl(pc["max"])])
        d.tabela(["Categoria", "Ofertas", "Mínimo", "Mediana", "Máximo"], linhas)
    precos_pncp = _fontes_pncp(d, con, org["id"])
    d.par("Outras fontes do art. 23, § 1º: ", ("preencher", "painel de preços, cotações diretas, mídia especializada"),
          ". Solução escolhida e justificativa técnica e econômica: ", ("preencher", "tipo de crédito e categoria"), ".")

    d.secao("VI. Estimativa do valor (art. 18, § 1º, VI)")
    if precos:
        creditos = qtd * precos["mediana"]
        d.tabela(["Item", "Valor"], [
            ["Quantidade", f"{_num(qtd, 0)} tCO2e"], ["Preço unitário de referência (mediana do catálogo)", _brl(precos["mediana"])],
            ["Valor estimado dos créditos", _brl(creditos)],
            [f"Taxa de intermediação, se adquirido pela plataforma ({taxa:g}%)", _brl(creditos * taxa / 100)],
            ["Valor total estimado", _brl(creditos * (1 + taxa / 100))]])
    if precos_pncp:
        todas = precos_pncp + [r[0] for r in con.execute("SELECT preco_unitario FROM oferta WHERE ativa=1")]
        d.par(f"Mediana de todas as fontes vigentes (catálogo e PNCP, {len(todas)} preços): "
              f"{_brl(statistics.median(todas))} por tCO2e; para {_num(qtd, 0)} tCO2e, {_brl(qtd * statistics.median(todas))}.")
    d.par("O preço de referência final deve resultar da pesquisa completa (art. 23): ",
          ("preencher", "valor de referência adotado e memória"), ".")

    d.secao("VII. Descrição da solução como um todo (art. 18, § 1º, VII)")
    d.par("Aquisição e cancelamento de créditos em nome do órgão, com publicação do certificado de cancelamento "
          "no painel de transparência e vinculação ao inventário compensado, sem alegação de neutralidade além do "
          "efetivamente compensado.")

    d.secao("VIII. Parcelamento (art. 18, § 1º, VIII)")
    d.par(("preencher", "parcelar por lote/categoria ou não, com a justificativa (art. 40, V, b)"))

    d.secao("IX a XII. Resultados, providências, contratações correlatas e impactos ambientais")
    d.par("Resultados pretendidos: compensação de ", ("b", f"{_num(qtd, 0)} tCO2e"),
          " do inventário de ", str(inv["ano"]), ". Providências prévias: ",
          ("preencher", "designação de fiscal, dotação orçamentária, capacitação"),
          ". Contratações correlatas: ", ("preencher", "verificação do inventário, se contratada"),
          ". Impactos ambientais: positivos; exigir projetos com salvaguardas socioambientais e, em territórios "
          "de povos e comunidades tradicionais, a consulta livre, prévia e informada (Lei 15.042/2024, art. 47).")

    d.secao("XIII. Posicionamento conclusivo (art. 18, § 1º, XIII)")
    d.par(("preencher", "conclusão sobre a viabilidade e a adequação da contratação"))
    d.par("Fundamentação: Lei 14.133/2021 (arts. 5º, 11, IV, 18, 23 e 53); Lei 15.042/2024 (arts. 2º, 45 a 47); "
          "Lei 12.187/2009; Lei 4.320/1964 (arts. 60 a 64).")
    d.par("Local e data: ", ("preencher", "local, data, nome e assinatura dos responsáveis"))
    return d.bytes()


# ---------------------------------------------------------------- TR
def tr_pedido(con: sqlite3.Connection, usuario_id: int, pedido_id: int) -> bytes:
    """Termo de Referência com as alíneas do art. 6º, XXIII, a partir do pedido do catálogo."""
    from .marketplace import CATEGORIAS, ver_pedido
    p = ver_pedido(con, usuario_id, pedido_id)
    org = buscar(con, "organizacao", p["orgao_id"])
    precos = _precos_catalogo(con, p["categoria"])
    tipo = ("Certificados de Redução ou Remoção Verificada de Emissões (CRVE), registrados no SBCE"
            if p["tipo_ativo"] == "CRVE" else
            "créditos de carbono do mercado voluntário certificados por padrão reconhecido internacionalmente")
    d = _Doc("Termo de Referência — aquisição de créditos de carbono para compensação de emissões",
             f"{org['nome']} · CNPJ {org['cnpj']}" + (f" · Processo {p['processo']}" if p["processo"] else ""))

    d.secao("a) Definição do objeto")
    d.par(f"Aquisição de {_num(p['quantidade'], 0)} unidades de {tipo}, categoria "
          f"{CATEGORIAS.get(p['categoria'], p['categoria'])}, cada uma equivalente a 1 tCO2e, com ",
          ("b", "cancelamento (aposentadoria) em nome do órgão"), " no registro do padrão certificador. Prazo de "
          "entrega: ", ("preencher", "dias após o empenho"), ". Sem prorrogação, por se tratar de entrega única.")
    d.secao("b) Fundamentação da contratação")
    d.par("Estudo Técnico Preliminar nº ", ("preencher", "nº do ETP"), ", que demonstra a necessidade de compensar as "
          "emissões do inventário aprovado do órgão (Lei 15.042/2024, art. 45).")
    d.secao("c) Descrição da solução como um todo")
    d.par("Aquisição com cancelamento em nome do órgão e publicação do comprovante no painel de transparência, "
          "vinculado ao inventário compensado.")
    d.secao("d) Requisitos da contratação")
    d.itens(["Padrão certificador reconhecido, metodologia publicada e verificação independente.",
             "Safra não anterior a cinco anos e não posterior ao ano do inventário compensado.",
             "Registro público com número de série; vedação à dupla contagem.",
             "Regularidade fiscal (federal e FGTS) e trabalhista durante a execução."])
    d.secao("e) Modelo de execução")
    d.par("O fornecedor cancela as unidades em nome do órgão e entrega o comprovante com o número de registro, "
          "que o fiscal confere no registro público do padrão.")
    d.secao("f) Modelo de gestão do contrato")
    d.par("Fiscal: ", ("preencher", "nome e matrícula do fiscal designado"),
          ". A conferência do cancelamento é condição para o atesto.")
    d.secao("g) Critérios de medição e de pagamento")
    d.par("Pagamento único, após empenho prévio (Lei 4.320/1964, art. 60), entrega comprovada e liquidação atestada "
          "pelo fiscal (art. 63), no prazo de ", ("preencher", "dias"), ".")
    d.secao("h) Forma e critérios de seleção do fornecedor")
    d.par(("preencher", "licitação, dispensa por valor (art. 75, II) ou outra hipótese, com a motivação"),
          ". Este termo não exige aquisição por plataforma ou fornecedor específico.")
    d.secao("i) Estimativa do valor da contratação")
    d.tabela(["Item", "Valor"], [
        ["Quantidade", f"{_num(p['quantidade'], 0)} tCO2e"], ["Preço unitário de referência", _brl(p["preco_unitario"])],
        ["Valor dos créditos", _brl(p["valor_creditos"])],
        [f"Taxa de intermediação, se adquirido pela plataforma ({p['taxa_percentual']:g}%)", _brl(p["valor_taxa"])],
        ["Valor total estimado", _brl(p["valor_total"])]])
    if precos:
        d.par(f"Fonte 1 (catálogo da plataforma, mesma categoria): {precos['n']} oferta(s), mínimo {_brl(precos['min'])}, "
              f"mediana {_brl(precos['mediana'])}, máximo {_brl(precos['max'])} por tCO2e.")
    _fontes_pncp(d, con, p["orgao_id"])
    d.par("Demais fontes (art. 23, § 1º): ", ("preencher", "painel de preços, cotações diretas ou mídia especializada"))
    d.secao("j) Adequação orçamentária")
    d.par("Dotação: ", ("preencher", "programa de trabalho, natureza da despesa e fonte de recursos"))
    d.par("Local e data: ", ("preencher", "local, data, nome e assinatura"))
    return d.bytes()


# ---------------------------------------------------------------- relatório de emissões
def relatorio_inventario(con: sqlite3.Connection, inventario_id: int) -> bytes:
    """Relatório de emissões editável: números da plataforma + seções para o órgão completar."""
    from .mrv import totais
    from .relatorio import _dados
    dados = _dados(con, inventario_id)
    inv, org, t = dados["inv"], dados["org"], dados["totais"]
    anterior = con.execute("SELECT id FROM inventario WHERE organizacao_id=? AND ano=? AND status='aprovado'",
                           (org["id"], inv["ano"] - 1)).fetchone()
    d = _Doc(f"Relatório de emissões de gases de efeito estufa — {inv['ano']}",
             f"{org['nome']} · CNPJ {org['cnpj']} · situação do inventário: {inv['status']}", AVISO_RELATORIO)
    if inv["status"] != "aprovado":
        d.par(("b", "Inventário ainda não aprovado: os números podem mudar até a aprovação."))
    d.secao("1. Apresentação")
    d.par(("preencher", "contexto do órgão, compromissos climáticos e objetivo do relatório"))
    d.secao("2. Metodologia")
    d.par(f"GHG Protocol e ABNT NBR ISO 14064-1, abordagem de controle operacional, potenciais de aquecimento "
          f"global {inv['gwp_conjunto']}. Só entram no total as atividades com comprovante. Fatores de emissão "
          "utilizados (com versão e fonte):")
    d.tabela(["Fator", "Versão", "Valor", "Fonte"],
             [[f["descricao"], str(f["versao"]), f"{_fator(f['valor'])} tCO2e/{f['unidade']}", f"{f['fonte']} ({f['ano_referencia']})"]
              for f in dados["fatores"]])
    d.secao("3. Resultados")
    linhas = [["Escopo 1 — emissões diretas", _num(t["escopo1"])], ["Escopo 2 — energia adquirida", _num(t["escopo2"])],
              ["Escopo 3 — outras emissões indiretas", _num(t["escopo3"])], ["Total", _num(t["total"])],
              ["Remoções", _num(t["remocoes"])], ["CO2 biogênico (informativo)", _num(t["co2_biogenico"])]]
    if anterior:
        ta = totais(con, anterior["id"])
        var = (t["total"] - ta["total"]) / ta["total"] * 100 if ta["total"] else 0
        linhas.append([f"Total de {inv['ano'] - 1} (inventário aprovado)", _num(ta["total"])])
        linhas.append(["Variação em relação ao ano anterior", f"{_num(var, 1)}%"])
    d.tabela(["Indicador", "tCO2e"], linhas)
    pend = t["pendentes_comprovacao"]
    if pend["quantidade"]:
        d.par(f"Ficaram fora do total {pend['quantidade']} lançamento(s) sem comprovante, somando "
              f"{_num(pend['tco2e'])} tCO2e.")
    if t["emissoes_evitadas"]:
        d.par(f"Emissões evitadas, reportadas à parte e não abatidas do total: {_num(t['emissoes_evitadas'])} tCO2e.")
    d.secao("4. Enquadramento no SBCE (Lei 15.042/2024)")
    enq = dados["enquadramento"]
    rotulo = {"nao_regulado": "abaixo dos patamares (sem obrigações no SBCE)",
              "regulado_monitoramento": "obrigação de monitorar e relatar (art. 30, I)",
              "regulado_conciliacao": "obrigação de monitorar, relatar e conciliar (art. 30, II)"}
    d.par("Faixa do operador: ", ("b", rotulo.get(enq["faixa_sbce"], enq["faixa_sbce"])), f". {enq['fase_sbce']}.")
    if enq.get("obrigacoes"):
        d.itens([f"{o['titulo']} — {o['detalhe']} ({o['fundamento']})" for o in enq["obrigacoes"]])
    if enq.get("alertas"):
        d.itens(enq["alertas"])
    d.secao("5. Ações de redução e próximos passos")
    d.par(("preencher", "ações executadas no ano, metas e cronograma"))
    d.secao("6. Compensação")
    d.par(("preencher", "créditos cancelados, com os certificados verificáveis no painel de transparência"),
          ". Não use a expressão \"carbono neutro\" sem compensação integral comprovada.")
    d.secao("7. Verificação e aprovação")
    if dados["historico"]:
        d.tabela(["Data", "De", "Para", "Responsável", "Parecer"],
                 [[h["em"][:10], h["de_status"], h["para_status"], f"{h['usuario_nome']} ({h['usuario_papel']})",
                   h["parecer"] or ""] for h in dados["historico"]])
    else:
        d.par("Inventário ainda sem etapas de verificação registradas.")
    return d.bytes()
