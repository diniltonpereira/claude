"""Parâmetros tributários por vigência. Sem exclusão: encerra-se a vigência (fica na trilha)."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import schemas
from ..db import get_db
from ..models import Provider, TaxParameter
from ..security import current_provider
from ..services.audit import audit
from ..services.split import utc

router = APIRouter(prefix="/v1/tax-parameters", tags=["parâmetros tributários"])


def _out(t: TaxParameter) -> dict:
    return {"id": t.id, "tax": t.tax, "applies_to": t.applies_to, "rate": str(t.rate),
            "valid_from": t.valid_from, "valid_to": t.valid_to, "source_note": t.source_note}


def _overlaps(a_from: datetime, a_to: datetime | None, b_from: datetime, b_to: datetime | None) -> bool:
    far = datetime.max.replace(tzinfo=utc(a_from).tzinfo)
    return utc(a_from) < utc(b_to or far) and utc(b_from) < utc(a_to or far)


@router.post("", status_code=201)
def create_tax(body: schemas.TaxParameterCreate, p: Provider = Depends(current_provider),
               db: Session = Depends(get_db)):
    if body.valid_to and utc(body.valid_to) <= utc(body.valid_from):
        raise HTTPException(422, "valid_to deve ser posterior a valid_from")
    same = db.scalars(select(TaxParameter).where(TaxParameter.provider_id == p.id,
                                                 TaxParameter.tax == body.tax,
                                                 TaxParameter.applies_to == body.applies_to)).all()
    if any(_overlaps(body.valid_from, body.valid_to, t.valid_from, t.valid_to) for t in same):
        raise HTTPException(409, "Vigência sobreposta a outro parâmetro do mesmo tributo; encerre o anterior")
    t = TaxParameter(provider_id=p.id, **body.model_dump())
    db.add(t); db.flush()
    audit(db, p.id, "tax_parameter.created", {"id": t.id, **body.model_dump(mode="json")})
    db.commit()
    return _out(t)


@router.get("")
def list_tax(at: datetime | None = None, p: Provider = Depends(current_provider),
             db: Session = Depends(get_db)):
    rows = db.scalars(select(TaxParameter).where(TaxParameter.provider_id == p.id)
                      .order_by(TaxParameter.tax, TaxParameter.applies_to, TaxParameter.valid_from)).all()
    if at:
        rows = [t for t in rows if utc(t.valid_from) <= utc(at) and (t.valid_to is None or utc(at) < utc(t.valid_to))]
    return [_out(t) for t in rows]


@router.post("/{tax_id}/close")
def close_tax(tax_id: int, body: schemas.TaxParameterClose, p: Provider = Depends(current_provider),
              db: Session = Depends(get_db)):
    t = db.get(TaxParameter, tax_id)
    if not t or t.provider_id != p.id:
        raise HTTPException(404, "Parâmetro não encontrado")
    if t.valid_to is not None:
        raise HTTPException(409, "Vigência já encerrada")
    if utc(body.valid_to) <= utc(t.valid_from):
        raise HTTPException(422, "valid_to deve ser posterior a valid_from")
    t.valid_to = body.valid_to
    audit(db, p.id, "tax_parameter.closed", {"id": t.id, "valid_to": body.valid_to.isoformat()})
    db.commit()
    return _out(t)
