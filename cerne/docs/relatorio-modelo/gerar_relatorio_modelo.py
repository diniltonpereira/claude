import hashlib
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.platypus import (BaseDocTemplate, PageTemplate, Frame, Paragraph, Spacer,
                                Table, TableStyle, NextPageTemplate, KeepTogether, HRFlowable)
from reportlab.pdfgen import canvas as rl_canvas

NAVY = colors.HexColor("#0A1B33"); GOLD = colors.HexColor("#B8893C"); GOLD_L = colors.HexColor("#D9B26A")
GOLD_D = colors.HexColor("#8A6420"); MIST = colors.HexColor("#F5F6F8"); INK = colors.HexColor("#1F2937")
GRAY = colors.HexColor("#5B6573"); RED = colors.HexColor("#B42318"); WHITE = colors.HexColor("#F3F5F8")
LINE = colors.HexColor("#DDE1E7")
W, H = A4; M = 48
HEADER_H = 118
COMPETENCIA = "2026-10"

# ---------- marca ----------
def symbol(c, x, y, size, c1, c2, core):
    c.saveState()
    c.translate(x, y + size); c.scale(size / 120.0, -size / 120.0)
    for dx, poly, col in ((-1.6, [(-10, -10), (130, -10), (-10, 130)], c1),
                          (1.6, [(130, -10), (130, 130), (-10, 130)], c2)):
        c.saveState(); c.translate(dx, dx)
        p = c.beginPath(); p.moveTo(*poly[0]); p.lineTo(*poly[1]); p.lineTo(*poly[2]); p.close()
        c.clipPath(p, stroke=0, fill=0)
        c.setStrokeColor(col); c.setLineWidth(7)
        for r in (50, 35, 20):
            c.circle(60, 60, r, stroke=1, fill=0)
        c.restoreState()
    c.setFillColor(core); c.circle(60, 60, 7, stroke=0, fill=1)
    c.restoreState()

def spaced(c, x, y, text, font, size, sp, color, right=False):
    w = stringWidth(text, font, size) + sp * len(text)
    t = c.beginText(x - w + sp if right else x, y)
    t.setFont(font, size); t.setCharSpace(sp); t.setFillColor(color); t.textOut(text); t.setCharSpace(0)
    c.drawText(t)

def footer(c):
    c.setStrokeColor(LINE); c.setLineWidth(0.6); c.line(M, 40, W - M, 40)
    c.setFont("Helvetica", 7.5); c.setFillColor(GRAY)
    c.drawString(M, 28, "Cerne · Clareza que se comprova.")
    c.drawCentredString(W / 2, 28, "Modelo com dados ilustrativos")

def first_page(c, doc):
    c.saveState()
    c.setFillColor(NAVY); c.rect(0, H - HEADER_H, W, HEADER_H, stroke=0, fill=1)
    symbol(c, M, H - HEADER_H + 31, 56, WHITE, GOLD_L, GOLD_L)
    spaced(c, M + 76, H - HEADER_H + 60, "CERNE", "Helvetica", 28, 7, WHITE)
    spaced(c, M + 76, H - HEADER_H + 40, "API · SCM × SVA", "Helvetica-Bold", 7.5, 2.6, GOLD_L)
    spaced(c, W - M, H - HEADER_H + 64, "RELATÓRIO DE EVIDÊNCIAS", "Helvetica-Bold", 9, 2.2, WHITE, right=True)
    spaced(c, W - M, H - HEADER_H + 48, f"Competência {COMPETENCIA}", "Helvetica", 8, 1.2, GOLD_L, right=True)
    c.setStrokeColor(GOLD_L); c.setLineWidth(0.8)
    tag = "MODELO · DADOS ILUSTRATIVOS"; tw = stringWidth(tag, "Helvetica-Bold", 6.5) + 1.6 * len(tag) + 14
    c.roundRect(W - M - tw, H - HEADER_H + 22, tw, 15, 7.5, stroke=1, fill=0)
    spaced(c, W - M - 7, H - HEADER_H + 27.3, tag, "Helvetica-Bold", 6.5, 1.6, GOLD_L, right=True)
    footer(c); c.restoreState()

def later_page(c, doc):
    c.saveState()
    symbol(c, M, H - 52, 24, NAVY, GOLD, NAVY)
    spaced(c, M + 33, H - 41, "CERNE", "Helvetica", 12, 3.2, NAVY)
    c.setFont("Helvetica", 7.5); c.setFillColor(GRAY)
    c.drawRightString(W - M, H - 40, f"Relatório de Evidências · Competência {COMPETENCIA}")
    c.setStrokeColor(GOLD); c.setLineWidth(0.8); c.line(M, H - 62, W - M, H - 62)
    footer(c); c.restoreState()

class NumberedCanvas(rl_canvas.Canvas):
    def __init__(self, *a, **k):
        super().__init__(*a, **k); self._saved = []
    def showPage(self):
        self._saved.append(dict(self.__dict__)); self._startPage()
    def save(self):
        n = len(self._saved)
        for st in self._saved:
            self.__dict__.update(st)
            self.setFont("Helvetica", 7.5); self.setFillColor(GRAY)
            self.drawRightString(W - M, 28, f"Página {self._pageNumber} de {n}")
            super().showPage()
        super().save()

# ---------- estilos ----------
body = ParagraphStyle("body", fontName="Helvetica", fontSize=9, leading=13, textColor=INK)
small = ParagraphStyle("small", parent=body, fontSize=8, leading=11, textColor=GRAY)
h1 = ParagraphStyle("h1", fontName="Helvetica-Bold", fontSize=19, leading=23, textColor=NAVY, spaceAfter=4)
h2 = ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=12, leading=15, textColor=NAVY, spaceBefore=13, spaceAfter=2, keepWithNext=1)
cell = ParagraphStyle("cell", fontName="Helvetica", fontSize=7.8, leading=10, textColor=INK)
cellb = ParagraphStyle("cellb", parent=cell, fontName="Helvetica-Bold")
head = ParagraphStyle("head", parent=cell, fontName="Helvetica-Bold", textColor=WHITE)
kv = ParagraphStyle("kv", fontName="Helvetica-Bold", fontSize=15, leading=18, textColor=NAVY)
kl = ParagraphStyle("kl", fontName="Helvetica", fontSize=7.5, leading=10, textColor=GRAY)
mono = ParagraphStyle("mono", fontName="Courier", fontSize=7.6, leading=10, textColor=INK)

def section(title):
    hr = HRFlowable(width=34, thickness=1.6, color=GOLD, hAlign="LEFT", spaceAfter=7)
    hr.keepWithNext = 1
    return [Paragraph(title, h2), hr]

def table(rows, widths, header=True, align_right=()):
    data = [[Paragraph(str(x), head if (header and i == 0) else cell) for x in r] for i, r in enumerate(rows)]
    t = Table(data, colWidths=widths, repeatRows=1 if header else 0)
    st = [("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
          ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
          ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE)]
    if header:
        st += [("BACKGROUND", (0, 0), (-1, 0), NAVY), ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, MIST])]
    t.setStyle(TableStyle(st)); return t

def kpis(items):
    row = [[Paragraph(v, kv), Paragraph(l, kl)] for v, l in items]
    t = Table([[ [a, b] for a, b in row ]], colWidths=[(W - 2 * M) / len(items)] * len(items))
    st = [("BACKGROUND", (0, 0), (-1, -1), MIST), ("LINEABOVE", (0, 0), (-1, -1), 1.6, GOLD),
          ("LINEAFTER", (0, 0), (-2, -1), 6, colors.white), ("TOPPADDING", (0, 0), (-1, -1), 9),
          ("BOTTOMPADDING", (0, 0), (-1, -1), 9), ("LEFTPADDING", (0, 0), (-1, -1), 10)]
    t.setStyle(TableStyle(st)); return t

ok = lambda s: f'<font color="#1B6B3A"><b>{s}</b></font>'
bad = lambda s: f'<font color="#B42318"><b>{s}</b></font>'
fullw = W - 2 * M
last_hash = hashlib.sha256(b"cerne-modelo-ilustrativo").hexdigest()

S = [NextPageTemplate("later"),
     Paragraph("Relatório de Evidências SCM × SVA", h1),
     Paragraph("Registro auditável da classificação, da ativação e do uso dos serviços, com a separação dos valores da fatura entre SCM e SVA.", body),
     Spacer(1, 12)]

S.append(table([
    ["Provedor", "Provedor Exemplo Ltda. · CNPJ 00.000.000/0001-00"],
    ["Competência", COMPETENCIA],
    ["Emitido em", "20/10/2026"],
    ["Identificação do relatório", "CER-2026-10-000001"],
    ["Gerado por", "Cerne API v0.1 · integração com o ERP em modo somente leitura"],
], [130, fullw - 130], header=False))

S += section("1. Resumo do período")
S.append(kpis([("R$ 118.450,00", "Total SCM"), ("R$ 9.870,00", "Total SVA"), ("R$ 1.230,00", "Pendente de revisão"), ("1.240", "Faturas analisadas")]))
S.append(Spacer(1, 6))
S.append(kpis([("17", "Faturas com pendência"), ("23", "Avisos emitidos"), ("4.812", "Entradas na trilha de auditoria"), ("Íntegra", "Verificação da trilha")]))
S.append(Spacer(1, 6))
S.append(Paragraph("Valores em PENDENTE não foram classificados como SCM nem como SVA: aguardam validação ou comprovação e devem ser revistos antes de qualquer enquadramento fiscal.", small))

S += section("2. Integridade da trilha de auditoria")
S.append(table([
    ["Entradas verificadas", "4.812"],
    ["Resultado", ok("Íntegra")],
    ["Primeira inconsistência", "Nenhuma"],
    ["Hash da última entrada (SHA-256)", f'<font name="Courier" size="6.8">{last_hash[:32]}<br/>{last_hash[32:]}</font>'],
], [170, fullw - 170], header=False))
S.append(Spacer(1, 5))
S.append(Paragraph("A trilha é encadeada por hash: qualquer alteração em um registro anterior invalida os seguintes e é apontada pela verificação. O mecanismo evidencia adulteração, mas não a impede.", small))

S += section("3. Catálogo de serviços e classificação")
S.append(table([
    ["Código", "Serviço", "Tipo", "Validação", "Regra"],
    ["INTERNET", "Banda larga fibra", "SCM", ok("Validado") + "<br/>Contador Exemplo · CRC/MG 000000 · 30/09/2026", "v1"],
    ["ANTIVIRUS", "Antivírus e segurança digital", "SVA", ok("Validado") + "<br/>Contador Exemplo · CRC/MG 000000 · 30/09/2026", "v1"],
    ["CURSOS", "Cursos online", "SVA", bad("Pendente de validação"), "v1"],
], [64, 120, 36, fullw - 64 - 120 - 36 - 40, 40]))
S.append(Spacer(1, 5))
S.append(Paragraph("O critério de cada classificação fica registrado no cadastro do serviço.", small))

S += section("4. Ativação e uso do SVA por assinante")
S.append(table([
    ["Cliente", "Serviço", "Consentimento", "Ativação", "Cancelamento", "Usos", "Último uso"],
    ["C-000123", "ANTIVIRUS", "CONS-8841", "14/08/2026", "—", "37", "18/10/2026"],
    ["C-000457", "ANTIVIRUS", "CONS-9120", "02/09/2026", "05/10/2026", "12", "04/10/2026"],
    ["C-000981", "CURSOS", "—", "10/10/2026", "—", "0", "—"],
], [58, 64, 74, 62, 70, 36, fullw - 58 - 64 - 74 - 62 - 70 - 36]))
S.append(Spacer(1, 5))
S.append(Paragraph("Os assinantes são identificados apenas pelo código interno do ERP. Nome, CPF, endereço e contatos não são lidos nem armazenados.", small))

S += section("5. Separação por fatura (amostra)")
S.append(table([
    ["Fatura", "Cliente", "SCM", "SVA", "Pendente", "Aviso"],
    ["F-90011", "C-000123", "R$ 99,90", "R$ 9,90", "R$ 0,00", "—"],
    ["F-90012", "C-000457", "R$ 99,90", "R$ 9,90", "R$ 0,00", "—"],
    ["F-90013", "C-000981", "R$ 99,90", "R$ 0,00", "R$ 20,00", "Regra do serviço CURSOS ainda não validada"],
], [52, 56, 54, 50, 54, fullw - 52 - 56 - 54 - 50 - 54]))

S += section("6. Pendências e avisos do período")
S.append(table([
    ["Tipo de aviso", "Ocorrências", "Providência sugerida"],
    ["Serviço sem classificação validada", "1", "Validar a classificação com o contador e registrar no cadastro."],
    ["SVA sem ativação registrada para o cliente", "9", "Localizar o aceite do assinante ou rever a cobrança do item."],
    ["SVA ativo sem registro de uso no período", "13", "Verificar se o serviço está de fato disponível e sendo usado."],
], [190, 62, fullw - 190 - 62]))

S += section("7. Ressalvas")
S.append(Paragraph(
    "Este relatório é gerado por ferramenta de apoio ao registro, à separação e à comprovação. Ele <b>não decide nem garante</b> a classificação de qualquer serviço como SCM ou SVA, nem qualquer economia ou tratamento tributário. "
    "A classificação, o enquadramento fiscal e a emissão dos documentos fiscais são responsabilidade do provedor, que deve validá-los com contador ou outro profissional habilitado. "
    "O documento não constitui consultoria jurídica, contábil ou tributária. O tratamento de dados pessoais segue o termo firmado com o provedor e a LGPD.", body))

doc = BaseDocTemplate("Cerne-Relatorio-de-Evidencias-modelo.pdf", pagesize=A4,
                      title="Cerne – Relatório de Evidências SCM × SVA (modelo)", author="Cerne API",
                      subject="Modelo com dados ilustrativos")
doc.addPageTemplates([
    PageTemplate(id="first", frames=[Frame(M, 52, fullw, H - HEADER_H - 28 - 52, id="f1", leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)], onPage=first_page),
    PageTemplate(id="later", frames=[Frame(M, 52, fullw, H - 76 - 52, id="f2", leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)], onPage=later_page),
])
doc.build(S, canvasmaker=NumberedCanvas)
print("ok")
