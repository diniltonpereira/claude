"""Administração da plataforma (X-Admin-Token): provedores e chaves."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import schemas
from ..db import get_db
from ..models import ApiKey, Provider
from ..security import issue_key, require_admin
from ..services.audit import audit

router = APIRouter(prefix="/v1/admin", tags=["admin"], dependencies=[Depends(require_admin)])

AVISO = "Guarde a chave: ela não será exibida novamente."


@router.post("/providers", status_code=201)
def create_provider(body: schemas.ProviderCreate, db: Session = Depends(get_db)):
    if db.scalar(select(Provider).where(Provider.cnpj == body.cnpj)):
        raise HTTPException(409, "CNPJ já cadastrado")
    p = Provider(name=body.name, cnpj=body.cnpj)
    db.add(p); db.flush()
    k, key = issue_key(db, p.id, "inicial")
    audit(db, p.id, "provider.created", {"cnpj": body.cnpj, "key_prefix": k.prefix})
    db.commit()
    return {"provider_id": p.id, "api_key": key, "key_id": k.id, "aviso": AVISO}


@router.get("/providers")
def list_providers(db: Session = Depends(get_db)):
    return [{"id": p.id, "name": p.name, "cnpj": p.cnpj, "active": p.active,
             "created_at": p.created_at} for p in db.scalars(select(Provider).order_by(Provider.id))]


@router.post("/providers/{provider_id}/keys", status_code=201)
def admin_issue_key(provider_id: int, body: schemas.KeyCreate, db: Session = Depends(get_db)):
    if not db.get(Provider, provider_id):
        raise HTTPException(404, "Provedor não encontrado")
    k, key = issue_key(db, provider_id, body.label)
    audit(db, provider_id, "key.issued", {"key_prefix": k.prefix, "by": "admin"})
    db.commit()
    return {"key_id": k.id, "api_key": key, "aviso": AVISO}


@router.post("/providers/{provider_id}/deactivate")
def deactivate_provider(provider_id: int, db: Session = Depends(get_db)):
    p = db.get(Provider, provider_id)
    if not p:
        raise HTTPException(404, "Provedor não encontrado")
    p.active = False
    audit(db, p.id, "provider.deactivated", {})
    db.commit()
    return {"provider_id": p.id, "active": False}


@router.delete("/keys/{key_id}")
def admin_revoke_key(key_id: int, db: Session = Depends(get_db)):
    k = db.get(ApiKey, key_id)
    if not k:
        raise HTTPException(404, "Chave não encontrada")
    if k.revoked_at is None:
        k.revoked_at = datetime.now(timezone.utc)
        audit(db, k.provider_id, "key.revoked", {"key_prefix": k.prefix, "by": "admin"})
        db.commit()
    return {"key_id": k.id, "revoked_at": k.revoked_at}
