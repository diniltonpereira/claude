"""Relatórios por competência."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Path
from fastapi.responses import Response
from sqlalchemy.orm import Session

from .. import schemas
from ..db import get_db
from ..models import Provider
from ..security import current_provider
from ..services import reports

router = APIRouter(prefix="/v1/reports", tags=["relatórios"])


@router.get("/{period}/summary")
def report_summary(period: str = Path(pattern=schemas.PERIOD), p: Provider = Depends(current_provider),
                   db: Session = Depends(get_db)):
    return reports.summary(db, p.id, period)


@router.get("/{period}/evidence.pdf")
def report_pdf(period: str = Path(pattern=schemas.PERIOD), p: Provider = Depends(current_provider),
               db: Session = Depends(get_db)):
    pdf, report_id = reports.evidence_pdf(db, p, period)
    db.commit()   # grava o evento report.generated com o hash do PDF
    return Response(pdf, media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="{report_id}.pdf"', "X-Report-Id": report_id})
