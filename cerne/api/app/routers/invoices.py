"""Módulo 3: separação dos valores da fatura."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import schemas
from ..db import get_db
from ..models import InvoiceSplit, Provider
from ..security import current_provider
from ..services.reports import latest_splits
from ..services.split import run_split

router = APIRouter(prefix="/v1/invoices", tags=["faturas"])


def _out(s: InvoiceSplit, detail: bool = False) -> dict:
    d = {"id": s.id, "invoice_ref": s.invoice_ref, "customer_ref": s.customer_ref, "period": s.period,
         "total_scm": str(s.total_scm), "total_sva": str(s.total_sva),
         "total_pendente": str(s.total_pendente), "warnings": s.detail.get("warnings", []),
         "created_at": s.created_at}
    if detail:
        d["lines"] = s.detail.get("lines", [])
    return d


@router.post("/split", response_model=schemas.InvoiceSplitOut)
def split(body: schemas.InvoiceSplitIn, p: Provider = Depends(current_provider),
          db: Session = Depends(get_db)):
    out = run_split(db, p.id, body)
    db.commit()
    return out


@router.get("")
def list_splits(period: str = Query(pattern=schemas.PERIOD), only_pending: bool = False,
                limit: int = Query(200, ge=1, le=1000), offset: int = Query(0, ge=0),
                p: Provider = Depends(current_provider), db: Session = Depends(get_db)):
    rows = latest_splits(db, p.id, period)
    if only_pending:
        rows = [s for s in rows if s.total_pendente > 0]
    return {"total": len(rows), "items": [_out(s) for s in rows[offset:offset + limit]]}


@router.get("/{invoice_ref}")
def get_split(invoice_ref: str, period: str = Query(pattern=schemas.PERIOD),
              p: Provider = Depends(current_provider), db: Session = Depends(get_db)):
    rows = db.scalars(select(InvoiceSplit).where(
        InvoiceSplit.provider_id == p.id, InvoiceSplit.period == period,
        InvoiceSplit.invoice_ref == invoice_ref).order_by(InvoiceSplit.id.desc())).all()
    if not rows:
        raise HTTPException(404, "Fatura não separada neste período")
    return {**_out(rows[0], detail=True), "historico": [_out(s) for s in rows[1:]]}
