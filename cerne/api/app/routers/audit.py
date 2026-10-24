"""Módulo 5: trilha de auditoria."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import core
from ..db import get_db
from ..models import AuditEntry, Provider
from ..security import current_provider
from ..services.audit import verify

router = APIRouter(prefix="/v1/audit", tags=["auditoria"])


@router.get("")
def list_audit(limit: int = Query(100, ge=1, le=500), before_id: int | None = None,
               action: str | None = None,
               p: Provider = Depends(current_provider), db: Session = Depends(get_db)):
    q = select(AuditEntry).where(AuditEntry.provider_id == p.id)
    if before_id:
        q = q.where(AuditEntry.id < before_id)
    if action:
        q = q.where(AuditEntry.action == action)
    rows = db.scalars(q.order_by(AuditEntry.id.desc()).limit(limit)).all()
    return [{"id": e.id, "ts": e.ts, "action": e.action, "payload": e.payload,
             "prev_hash": e.prev_hash, "hash": e.hash} for e in rows]


@router.get("/verify")
def verify_audit(p: Provider = Depends(current_provider), db: Session = Depends(get_db)):
    return verify(db, p.id)


@router.get("/head")
def audit_head(p: Provider = Depends(current_provider), db: Session = Depends(get_db)):
    """Cabeça da trilha: hash para carimbo de tempo externo (reforço probatório)."""
    e = db.scalar(select(AuditEntry).where(AuditEntry.provider_id == p.id)
                  .order_by(AuditEntry.id.desc()).limit(1))
    return {"id": e.id if e else None, "ts": e.ts if e else None,
            "hash": e.hash if e else core.GENESIS}
