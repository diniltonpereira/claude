"""Resumo por competência e Relatório de Evidências em PDF (layout de docs/relatorio-modelo)."""
from __future__ import annotations

import hashlib
import io
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from xml.sax.saxutils import escape

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from .. import __version__, core
from ..models import Activation, AuditEntry, InvoiceSplit, Provider, Service
from .audit import audit, verify
from .split import period_bounds, utc

MAX_ROWS = 200  # linhas por tabela no PDF; o JSON completo fica na API


def latest_splits(db: Session, provider_id: int, period: str) -> list[InvoiceSplit]:
    """Separação vigente de cada fatura do período (a mais recente, se reprocessada)."""
    last_ids = (select(func.max(InvoiceSplit.id))
                .where(InvoiceSplit.provider_id == provider_id, InvoiceSplit.period == period)
                .group_by(InvoiceSplit.invoice_ref))
    return list(db.scalars(select(InvoiceSplit).where(InvoiceSplit.id.in_(last_ids))
                           .order_by(InvoiceSplit.invoice_ref)))


def summary(db: Session, provider_id: int, period: str) -> dict:
    splits = latest_splits(db, provider_id, period)
    alerts: Counter = Counter()
    for s in splits:
        for ln in s.detail.get("lines", []):
            if ln.get("alert"):
                alerts[ln["alert"]] += 1
    tot = lambda attr: sum((getattr(s, attr) for s in splits), Decimal("0.00"))
    return {
        "periodo": period,
        "faturas": len(splits),
        "faturas_com_pendencia": sum(1 for s in splits if s.total_pendente > 0),
        "total_scm": str(tot("total_scm")),
        "total_sva": str(tot("total_sva")),
        "total_pendente": str(tot("total_pendente")),
        "avisos": sum(alerts.values()),
        "avisos_por_tipo": [{"codigo": c, "descricao": core.ALERTAS[c][0], "ocorrencias": n,
                             "providencia": core.ALERTAS[c][1]} for c, n in alerts.most_common()],
        "trilha": verify(db, provider_id),
    }


# ---------------------------------------------------------------- PDF
def _brl(v) -> str:
    s = f"{Decimal(v):,.2f}"
    return "R$ " + s.replace(",", "X").replace(".", ",").replace("X", ".")


def _int(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def _d(dt: datetime | None) -> str:
    return utc(dt).strftime("%d/%m/%Y") if dt else "—"


def evidence_pdf(db: Session, provider: Provider, period: str) -> tuple[bytes, str]:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.pdfbase.pdfmetrics import stringWidth
    from reportlab.pdfgen import canvas as rl_canvas
    from reportlab.platypus import (BaseDocTemplate, Frame, HRFlowable, NextPageTemplate,
                                    PageTemplate, Paragraph, Spacer, Table, TableStyle)

    NAVY = colors.HexColor("#0A1B33"); GOLD = colors.HexColor("#B8893C"); GOLD_L = colors.HexColor("#D9B26A")
    MIST = colors.HexColor("#F5F6F8"); INK = colors.HexColor("#1F2937"); GRAY = colors.HexColor("#5B6573")
    WHITE = colors.HexColor("#F3F5F8"); LINE = colors.HexColor("#DDE1E7")
    W, H = A4; M = 48; HEADER_H = 118; fullw = W - 2 * M

    pid = provider.id
    sm = summary(db, pid, period)
    start, end = period_bounds(period)
    n_reports = db.scalar(select(func.count()).select_from(AuditEntry).where(
        AuditEntry.provider_id == pid, AuditEntry.action == "report.generated")) or 0
    report_id = f"CER-{period}-{pid:04d}-{n_reports + 1:04d}"
    issued = datetime.now(timezone.utc)

    def symbol(c, x, y, size, c1, c2, core_c):
        c.saveState(); c.translate(x, y + size); c.scale(size / 120.0, -size / 120.0)
        for dx, poly, col in ((-1.6, [(-10, -10), (130, -10), (-10, 130)], c1),
                              (1.6, [(130, -10), (130, 130), (-10, 130)], c2)):
            c.saveState(); c.translate(dx, dx)
            p = c.beginPath(); p.moveTo(*poly[0]); p.lineTo(*poly[1]); p.lineTo(*poly[2]); p.close()
            c.clipPath(p, stroke=0, fill=0)
            c.setStrokeColor(col); c.setLineWidth(7)
            for r in (50, 35, 20):
                c.circle(60, 60, r, stroke=1, fill=0)
            c.restoreState()
        c.setFillColor(core_c); c.circle(60, 60, 7, stroke=0, fill=1); c.restoreState()

    def spaced(c, x, y, txt, font, size, sp, color, right=False):
        w = stringWidth(txt, font, size) + sp * len(txt)
        t = c.beginText(x - w + sp if right else x, y)
        t.setFont(font, size); t.setCharSpace(sp); t.setFillColor(color); t.textOut(txt); t.setCharSpace(0)
        c.drawText(t)

    def footer(c):
        c.setStrokeColor(LINE); c.setLineWidth(0.6); c.line(M, 40, W - M, 40)
        c.setFont("Helvetica", 7.5); c.setFillColor(GRAY)
        c.drawString(M, 28, "Cerne · Clareza que se comprova.")
        c.drawCentredString(W / 2, 28, report_id)

    def first_page(c, doc):
        c.saveState()
        c.setFillColor(NAVY); c.rect(0, H - HEADER_H, W, HEADER_H, stroke=0, fill=1)
        symbol(c, M, H - HEADER_H + 31, 56, WHITE, GOLD_L, GOLD_L)
        spaced(c, M + 76, H - HEADER_H + 60, "CERNE", "Helvetica", 28, 7, WHITE)
        spaced(c, M + 76, H - HEADER_H + 40, "API · SCM × SVA", "Helvetica-Bold", 7.5, 2.6, GOLD_L)
        spaced(c, W - M, H - HEADER_H + 64, "RELATÓRIO DE EVIDÊNCIAS", "Helvetica-Bold", 9, 2.2, WHITE, right=True)
        spaced(c, W - M, H - HEADER_H + 48, f"Competência {period}", "Helvetica", 8, 1.2, GOLD_L, right=True)
        footer(c); c.restoreState()

    def later_page(c, doc):
        c.saveState()
        symbol(c, M, H - 52, 24, NAVY, GOLD, NAVY)
        spaced(c, M + 33, H - 41, "CERNE", "Helvetica", 12, 3.2, NAVY)
        c.setFont("Helvetica", 7.5); c.setFillColor(GRAY)
        c.drawRightString(W - M, H - 40, f"Relatório de Evidências · Competência {period}")
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

    body = ParagraphStyle("body", fontName="Helvetica", fontSize=9, leading=13, textColor=INK)
    small = ParagraphStyle("small", parent=body, fontSize=8, leading=11, textColor=GRAY)
    h1 = ParagraphStyle("h1", fontName="Helvetica-Bold", fontSize=19, leading=23, textColor=NAVY, spaceAfter=4)
    h2 = ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=12, leading=15, textColor=NAVY,
                        spaceBefore=13, spaceAfter=2, keepWithNext=1)
    cell = ParagraphStyle("cell", fontName="Helvetica", fontSize=7.8, leading=10, textColor=INK)
    head = ParagraphStyle("head", parent=cell, fontName="Helvetica-Bold", textColor=WHITE)
    kv = ParagraphStyle("kv", fontName="Helvetica-Bold", fontSize=15, leading=18, textColor=NAVY)
    kl = ParagraphStyle("kl", fontName="Helvetica", fontSize=7.5, leading=10, textColor=GRAY)

    def section(title):
        hr = HRFlowable(width=34, thickness=1.6, color=GOLD, hAlign="LEFT", spaceAfter=7)
        hr.keepWithNext = 1
        return [Paragraph(title, h2), hr]

    def table(rows, widths, header=True, raw=()):
        # colunas em `raw` já vêm como marcação segura (escapada na origem); o resto é escapado aqui
        data = [[Paragraph(x if (j in raw and not (header and i == 0)) else escape(str(x)),
                           head if (header and i == 0) else cell)
                 for j, x in enumerate(r)] for i, r in enumerate(rows)]
        t = Table(data, colWidths=widths, repeatRows=1 if header else 0)
        st = [("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("TOPPADDING", (0, 0), (-1, -1), 5),
              ("BOTTOMPADDING", (0, 0), (-1, -1), 5), ("LEFTPADDING", (0, 0), (-1, -1), 6),
              ("RIGHTPADDING", (0, 0), (-1, -1), 6), ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE)]
        if header:
            st += [("BACKGROUND", (0, 0), (-1, 0), NAVY), ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, MIST])]
        t.setStyle(TableStyle(st)); return t

    def kpis(items):
        t = Table([[[Paragraph(v, kv), Paragraph(l, kl)] for v, l in items]],
                  colWidths=[fullw / len(items)] * len(items))
        t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), MIST), ("LINEABOVE", (0, 0), (-1, -1), 1.6, GOLD),
                               ("LINEAFTER", (0, 0), (-2, -1), 6, colors.white), ("TOPPADDING", (0, 0), (-1, -1), 9),
                               ("BOTTOMPADDING", (0, 0), (-1, -1), 9), ("LEFTPADDING", (0, 0), (-1, -1), 10)]))
        return t

    ok = lambda s: f'<font color="#1B6B3A"><b>{escape(s)}</b></font>'
    bad = lambda s: f'<font color="#B42318"><b>{escape(s)}</b></font>'
    trail = sm["trilha"]

    S = [NextPageTemplate("later"),
         Paragraph("Relatório de Evidências SCM × SVA", h1),
         Paragraph("Registro auditável da classificação, da ativação e do uso dos serviços, com a "
                   "separação dos valores da fatura entre SCM e SVA.", body),
         Spacer(1, 12),
         table([["Provedor", f"{provider.name} · CNPJ {provider.cnpj}"],
                ["Competência", period],
                ["Emitido em", issued.strftime("%d/%m/%Y %H:%M UTC")],
                ["Identificação do relatório", report_id],
                ["Gerado por", f"Cerne API v{__version__} · integração com o ERP em modo somente leitura"]],
               [130, fullw - 130], header=False)]

    S += section("1. Resumo do período")
    S.append(kpis([(_brl(sm["total_scm"]), "Total SCM"), (_brl(sm["total_sva"]), "Total SVA"),
                   (_brl(sm["total_pendente"]), "Pendente de revisão"), (_int(sm["faturas"]), "Faturas analisadas")]))
    S.append(Spacer(1, 6))
    S.append(kpis([(_int(sm["faturas_com_pendencia"]), "Faturas com pendência"), (_int(sm["avisos"]), "Avisos emitidos"),
                   (_int(trail["entradas"]), "Entradas na trilha de auditoria"),
                   ("Íntegra" if trail["integra"] else "Inconsistente", "Verificação da trilha")]))
    S.append(Spacer(1, 6))
    S.append(Paragraph("Valores em PENDENTE não foram classificados como SCM nem como SVA: aguardam validação "
                       "ou comprovação e devem ser revistos antes de qualquer enquadramento fiscal.", small))

    h = trail["hash_final"]
    S += section("2. Integridade da trilha de auditoria")
    S.append(table([["Entradas verificadas", _int(trail["entradas"])],
                    ["Resultado", ok("Íntegra") if trail["integra"] else bad("Inconsistente")],
                    ["Primeira inconsistência", str(trail["primeira_inconsistencia"] or "Nenhuma")],
                    ["Hash da última entrada (SHA-256)", f'<font name="Courier" size="6.8">{h[:32]}<br/>{h[32:]}</font>']],
                   [170, fullw - 170], header=False, raw=(1,)))
    S.append(Spacer(1, 5))
    S.append(Paragraph("A trilha é encadeada por hash: qualquer alteração em um registro anterior invalida os "
                       "seguintes e é apontada pela verificação. O mecanismo evidencia adulteração, mas não a impede.", small))

    services = db.scalars(select(Service).where(Service.provider_id == pid).order_by(Service.code)).all()
    S += section("3. Catálogo de serviços e classificação")
    rows = [["Código", "Serviço", "Tipo", "Validação", "Regra"]]
    for s in services[:MAX_ROWS]:
        val = (ok("Validado") + f"<br/>{escape(s.validated_by)} · {_d(s.validated_at)}") if s.validated \
            else bad("Pendente de validação")
        if not s.active:
            val += "<br/>" + bad("Inativo")
        rows.append([s.code, s.name, s.kind, val, s.rule_version])
    if len(rows) == 1:
        rows.append(["—", "Nenhum serviço cadastrado", "", "", ""])
    S.append(table(rows, [64, 120, 36, fullw - 64 - 120 - 36 - 40, 40], raw=(3,)))
    S.append(Spacer(1, 5))
    S.append(Paragraph("O critério de cada classificação fica registrado no cadastro do serviço.", small))

    acts = db.scalars(select(Activation).options(selectinload(Activation.usage)).where(
        Activation.provider_id == pid, Activation.activated_at < end)
        .order_by(Activation.customer_ref, Activation.service_code)).all()
    acts = [a for a in acts if a.canceled_at is None or utc(a.canceled_at) >= start]
    S += section("4. Ativação e uso do SVA por assinante")
    rows = [["Cliente", "Serviço", "Consentimento", "Ativação", "Cancelamento", "Usos no período", "Último uso"]]
    for a in acts[:MAX_ROWS]:
        uses = [utc(u.occurred_at) for u in a.usage if start <= utc(u.occurred_at) < end]
        rows.append([a.customer_ref, a.service_code, a.consent_ref or "—", _d(a.activated_at),
                     _d(a.canceled_at), str(len(uses)), _d(max(uses)) if uses else "—"])
    if len(rows) == 1:
        rows.append(["—", "Nenhuma ativação vigente no período", "", "", "", "", ""])
    S.append(table(rows, [54, 62, 70, 54, 70, 52, fullw - 54 - 62 - 70 - 54 - 70 - 52]))
    S.append(Spacer(1, 5))
    note = "Os assinantes são identificados apenas pelo código interno do ERP. Nome, CPF, endereço e contatos não são lidos nem armazenados."
    if len(acts) > MAX_ROWS:
        note += f" Exibidas {MAX_ROWS} de {_int(len(acts))} ativações; a lista completa está disponível na API."
    S.append(Paragraph(note, small))

    splits = latest_splits(db, pid, period)
    splits.sort(key=lambda s: (s.total_pendente == 0, s.invoice_ref))   # pendências primeiro
    S += section("5. Separação por fatura")
    rows = [["Fatura", "Cliente", "SCM", "SVA", "Pendente", "Aviso"]]
    for s in splits[:MAX_ROWS]:
        warns = s.detail.get("warnings", [])
        rows.append([s.invoice_ref, s.customer_ref, _brl(s.total_scm), _brl(s.total_sva),
                     _brl(s.total_pendente), "; ".join(warns) if warns else "—"])
    if len(rows) == 1:
        rows.append(["—", "Nenhuma fatura separada no período", "", "", "", ""])
    S.append(table(rows, [52, 56, 58, 54, 58, fullw - 52 - 56 - 58 - 54 - 58]))
    if len(splits) > MAX_ROWS:
        S.append(Spacer(1, 5))
        S.append(Paragraph(f"Exibidas {MAX_ROWS} de {_int(len(splits))} faturas (pendências primeiro); "
                           "a lista completa está disponível na API.", small))

    S += section("6. Pendências e avisos do período")
    rows = [["Tipo de aviso", "Ocorrências", "Providência sugerida"]]
    rows += [[a["descricao"], _int(a["ocorrencias"]), a["providencia"]] for a in sm["avisos_por_tipo"]]
    if len(rows) == 1:
        rows.append(["Nenhum aviso no período", "0", "—"])
    S.append(table(rows, [190, 62, fullw - 190 - 62]))

    S += section("7. Ressalvas")
    S.append(Paragraph(
        "Este relatório é gerado por ferramenta de apoio ao registro, à separação e à comprovação. Ele <b>não decide "
        "nem garante</b> a classificação de qualquer serviço como SCM ou SVA, nem qualquer tratamento tributário. "
        "A classificação, o enquadramento fiscal e a emissão dos documentos fiscais são responsabilidade do provedor, "
        "que deve validá-los com contador ou outro profissional habilitado. O documento não constitui consultoria "
        "jurídica, contábil ou tributária. O tratamento de dados pessoais segue o termo firmado com o provedor e a LGPD.",
        body))

    buf = io.BytesIO()
    doc = BaseDocTemplate(buf, pagesize=A4, title=f"Cerne – Relatório de Evidências {period}",
                          author="Cerne API", subject=report_id)
    doc.addPageTemplates([
        PageTemplate(id="first", frames=[Frame(M, 52, fullw, H - HEADER_H - 28 - 52, leftPadding=0, rightPadding=0,
                                               topPadding=0, bottomPadding=0)], onPage=first_page),
        PageTemplate(id="later", frames=[Frame(M, 52, fullw, H - 76 - 52, leftPadding=0, rightPadding=0,
                                               topPadding=0, bottomPadding=0)], onPage=later_page),
    ])
    doc.build(S, canvasmaker=NumberedCanvas)
    pdf = buf.getvalue()
    audit(db, pid, "report.generated", {"report_id": report_id, "period": period,
                                        "sha256": hashlib.sha256(pdf).hexdigest(),
                                        "trail_head": trail["hash_final"]})
    return pdf, report_id
