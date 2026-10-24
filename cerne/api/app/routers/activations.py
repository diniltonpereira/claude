"""Módulo 2: ativação, consentimento e uso do SVA."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from .. import schemas
from ..db import get_db
from ..models import Activation, Provider, Service, UsageEvent
from ..security import current_provider
from ..services.audit import audit

router = APIRouter(prefix="/v1", tags=["ativações"])


def _own_activation(db: Session, p: Provider, activation_id: int) -> Activation:
    a = db.get(Activation, activation_id)
    if not a or a.provider_id != p.id:
        raise HTTPException(404, "Ativação não encontrada")
    return a


@router.post("/activations", status_code=201)
def create_activation(body: schemas.ActivationCreate, p: Provider = Depends(current_provider),
                      db: Session = Depends(get_db)):
    s = db.scalar(select(Service).where(Service.provider_id == p.id, Service.code == body.service_code))
    if not s or s.kind != "SVA":
        raise HTTPException(422, "Ativação só vale para serviço SVA cadastrado")
    a = Activation(provider_id=p.id, **body.model_dump())
    db.add(a); db.flush()
    audit(db, p.id, "activation.created", {"activation_id": a.id, **body.model_dump(mode="json")})
    db.commit()
    return {"activation_id": a.id}


@router.get("/activations")
def list_activations(customer_ref: str | None = None, service_code: str | None = None,
                     limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0),
                     p: Provider = Depends(current_provider), db: Session = Depends(get_db)):
    q = select(Activation).where(Activation.provider_id == p.id)
    if customer_ref:
        q = q.where(Activation.customer_ref == customer_ref)
    if service_code:
        q = q.where(Activation.service_code == service_code)
    rows = db.scalars(q.order_by(Activation.id.desc()).limit(limit).offset(offset)).all()
    counts = dict(db.execute(select(UsageEvent.activation_id, func.count()).where(
        UsageEvent.activation_id.in_([a.id for a in rows])).group_by(UsageEvent.activation_id)).all())
    return [{"id": a.id, "customer_ref": a.customer_ref, "service_code": a.service_code,
             "consent_ref": a.consent_ref, "activated_at": a.activated_at, "canceled_at": a.canceled_at,
             "usage_events": counts.get(a.id, 0)} for a in rows]


@router.post("/activations/{activation_id}/usage", status_code=201)
def add_usage(activation_id: int, body: schemas.UsageCreate,
              p: Provider = Depends(current_provider), db: Session = Depends(get_db)):
    a = _own_activation(db, p, activation_id)
    db.add(UsageEvent(activation_id=a.id, **body.model_dump()))
    db.commit()   # uso é volumoso: entra na evidência, não na trilha encadeada
    return {"ok": True}


@router.post("/activations/{activation_id}/cancel")
def cancel_activation(activation_id: int, p: Provider = Depends(current_provider),
                      db: Session = Depends(get_db)):
    a = _own_activation(db, p, activation_id)
    if a.canceled_at is not None:
        raise HTTPException(409, "Ativação já cancelada")
    a.canceled_at = datetime.now(timezone.utc)
    audit(db, p.id, "activation.canceled", {"activation_id": a.id})
    db.commit()
    return {"canceled_at": a.canceled_at.isoformat()}


@router.get("/evidence/{customer_ref}")
def evidence(customer_ref: str, p: Provider = Depends(current_provider), db: Session = Depends(get_db)):
    acts = db.scalars(select(Activation).options(selectinload(Activation.usage)).where(
        Activation.provider_id == p.id, Activation.customer_ref == customer_ref)
        .order_by(Activation.id)).all()
    return [{"activation_id": a.id, "service_code": a.service_code, "consent_ref": a.consent_ref,
             "activated_at": a.activated_at.isoformat(),
             "canceled_at": a.canceled_at.isoformat() if a.canceled_at else None,
             "usage_events": len(a.usage),
             "first_use": min((u.occurred_at for u in a.usage), default=None),
             "last_use": max((u.occurred_at for u in a.usage), default=None)} for a in acts]
