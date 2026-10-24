"""Conta do provedor: identificação, chaves de API e credenciais do ERP."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import schemas
from ..db import get_db
from ..models import ApiKey, ErpCredential, Provider
from ..security import current_provider, encrypt_json, issue_key
from ..services.audit import audit

router = APIRouter(prefix="/v1", tags=["conta"])


@router.get("/me")
def me(p: Provider = Depends(current_provider)):
    return {"provider_id": p.id, "name": p.name, "cnpj": p.cnpj}


@router.get("/keys")
def list_keys(p: Provider = Depends(current_provider), db: Session = Depends(get_db)):
    rows = db.scalars(select(ApiKey).where(ApiKey.provider_id == p.id).order_by(ApiKey.id)).all()
    return [{"id": k.id, "prefix": k.prefix, "label": k.label, "created_at": k.created_at,
             "last_used_at": k.last_used_at, "revoked_at": k.revoked_at} for k in rows]


@router.post("/keys", status_code=201)
def create_key(body: schemas.KeyCreate, p: Provider = Depends(current_provider),
               db: Session = Depends(get_db)):
    k, key = issue_key(db, p.id, body.label)
    audit(db, p.id, "key.issued", {"key_prefix": k.prefix, "by": "provider"})
    db.commit()
    return {"key_id": k.id, "api_key": key, "aviso": "Guarde a chave: ela não será exibida novamente."}


@router.delete("/keys/{key_id}")
def revoke_key(key_id: int, p: Provider = Depends(current_provider), db: Session = Depends(get_db)):
    k = db.get(ApiKey, key_id)
    if not k or k.provider_id != p.id:
        raise HTTPException(404, "Chave não encontrada")
    active = db.scalars(select(ApiKey).where(ApiKey.provider_id == p.id, ApiKey.revoked_at.is_(None))).all()
    if k.revoked_at is None and len(active) == 1:
        raise HTTPException(409, "Crie outra chave antes de revogar a última ativa")
    if k.revoked_at is None:
        k.revoked_at = datetime.now(timezone.utc)
        audit(db, p.id, "key.revoked", {"key_prefix": k.prefix, "by": "provider"})
        db.commit()
    return {"key_id": k.id, "revoked_at": k.revoked_at}


@router.get("/erp/credentials")
def get_erp_credentials(p: Provider = Depends(current_provider), db: Session = Depends(get_db)):
    c = db.scalar(select(ErpCredential).where(ErpCredential.provider_id == p.id))
    if not c:
        return {"configured": False}
    return {"configured": True, "erp": c.erp, "base_url": c.base_url, "updated_at": c.updated_at}


@router.put("/erp/credentials")
def put_erp_credentials(body: schemas.ErpCredentialIn, p: Provider = Depends(current_provider),
                        db: Session = Depends(get_db)):
    try:
        blob = encrypt_json(body.model_dump(include={"client_id", "client_secret", "username", "password"}))
    except RuntimeError:
        raise HTTPException(503, "Cofre de credenciais não configurado no servidor") from None
    c = db.scalar(select(ErpCredential).where(ErpCredential.provider_id == p.id))
    if not c:
        c = ErpCredential(provider_id=p.id, erp=body.erp, base_url=body.base_url, secret=blob)
        db.add(c)
    else:
        c.erp, c.base_url, c.secret = body.erp, body.base_url, blob
        c.updated_at = datetime.now(timezone.utc)
    audit(db, p.id, "erp.credentials.updated", {"erp": body.erp, "base_url": body.base_url})
    db.commit()
    return {"configured": True, "erp": c.erp, "base_url": c.base_url}


@router.delete("/erp/credentials")
def delete_erp_credentials(p: Provider = Depends(current_provider), db: Session = Depends(get_db)):
    c = db.scalar(select(ErpCredential).where(ErpCredential.provider_id == p.id))
    if c:
        db.delete(c)
        audit(db, p.id, "erp.credentials.deleted", {})
        db.commit()
    return {"configured": False}
