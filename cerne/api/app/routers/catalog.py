"""Módulo 1: catálogo e classificação de serviços."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import schemas
from ..db import get_db
from ..models import Provider, Service
from ..security import current_provider
from ..services.audit import audit

router = APIRouter(prefix="/v1/services", tags=["catálogo"])


def _out(s: Service) -> dict:
    return {"code": s.code, "name": s.name, "kind": s.kind, "criteria": s.criteria,
            "contract_ref": s.contract_ref, "rule_version": s.rule_version, "validated": s.validated,
            "validated_by": s.validated_by, "validated_at": s.validated_at, "active": s.active}


def _own(db: Session, p: Provider, code: str) -> Service:
    s = db.scalar(select(Service).where(Service.provider_id == p.id, Service.code == code))
    if not s:
        raise HTTPException(404, "Serviço não encontrado")
    return s


@router.post("", status_code=201)
def create_service(body: schemas.ServiceCreate, p: Provider = Depends(current_provider),
                   db: Session = Depends(get_db)):
    if db.scalar(select(Service).where(Service.provider_id == p.id, Service.code == body.code)):
        raise HTTPException(409, "Código de serviço já existe")
    s = Service(provider_id=p.id, rule_version="v1", **body.model_dump())
    db.add(s)
    audit(db, p.id, "service.created", {**body.model_dump(), "rule_version": "v1"})
    db.commit()
    return _out(s)


@router.get("")
def list_services(p: Provider = Depends(current_provider), db: Session = Depends(get_db)):
    return [_out(s) for s in db.scalars(select(Service).where(Service.provider_id == p.id)
                                        .order_by(Service.code))]


@router.get("/{code}")
def get_service(code: str, p: Provider = Depends(current_provider), db: Session = Depends(get_db)):
    return _out(_own(db, p, code))


@router.put("/{code}")
def update_service(code: str, body: schemas.ServiceUpdate, p: Provider = Depends(current_provider),
                   db: Session = Depends(get_db)):
    s = _own(db, p, code)
    changes = {k: v for k, v in body.model_dump(exclude_unset=True).items() if getattr(s, k) != v}
    if not changes:
        return _out(s)
    for k, v in changes.items():
        setattr(s, k, v)
    if {"kind", "criteria"} & changes.keys():       # mudou a regra: nova versão, nova validação
        s.rule_version = f"v{int(s.rule_version.lstrip('v') or 1) + 1}"
        s.validated_by = s.validated_at = None
    audit(db, p.id, "service.updated", {"code": code, "changes": changes, "rule_version": s.rule_version,
                                        "validated": s.validated})
    db.commit()
    return _out(s)


@router.post("/{code}/validate")
def validate_service(code: str, body: schemas.ServiceValidate,
                     p: Provider = Depends(current_provider), db: Session = Depends(get_db)):
    s = _own(db, p, code)
    s.validated_by, s.validated_at = body.validated_by, datetime.now(timezone.utc)
    audit(db, p.id, "service.validated", {"code": code, "validated_by": body.validated_by,
                                          "kind": s.kind, "rule_version": s.rule_version})
    db.commit()
    return _out(s)


@router.post("/{code}/deactivate")
def deactivate_service(code: str, p: Provider = Depends(current_provider), db: Session = Depends(get_db)):
    s = _own(db, p, code)
    s.active = False
    audit(db, p.id, "service.deactivated", {"code": code})
    db.commit()
    return _out(s)
