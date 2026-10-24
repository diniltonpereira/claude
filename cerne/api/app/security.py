"""Autenticação por chave de API, limite de requisições e cifragem de segredos."""
from __future__ import annotations

import hashlib
import json
import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timezone

from fastapi import Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_db
from .models import ApiKey, Provider

KEY_PREFIX = "cerne_"


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def new_api_key() -> tuple[str, str]:
    """Devolve (chave completa, prefixo). Formato: cerne_<8 hex>_<segredo>."""
    prefix = secrets.token_hex(4)
    return f"{KEY_PREFIX}{prefix}_{secrets.token_urlsafe(32)}", prefix


def issue_key(db: Session, provider_id: int, label: str | None = None) -> tuple[ApiKey, str]:
    key, prefix = new_api_key()
    k = ApiKey(provider_id=provider_id, prefix=prefix, key_hash=hash_key(key), label=label)
    db.add(k)
    db.flush()
    return k, key


class RateLimiter:
    """Janela deslizante de 60 s em memória, por chave. Vale por processo (workers não compartilham)."""

    def __init__(self):
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, ident: str, limit: int) -> bool:
        if limit <= 0:
            return True
        now = time.monotonic()
        with self._lock:
            q = self._hits[ident]
            while q and now - q[0] > 60:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            return True


rate_limiter = RateLimiter()


def current_provider(x_api_key: str = Header(..., description="Chave de API do provedor"),
                     db: Session = Depends(get_db)) -> Provider:
    k = db.scalar(select(ApiKey).where(ApiKey.key_hash == hash_key(x_api_key)))
    if not k or k.revoked_at is not None or not k.provider.active:
        raise HTTPException(401, "API key inválida ou revogada")
    if not rate_limiter.allow(k.prefix, get_settings().rate_limit_per_min):
        raise HTTPException(429, "Limite de requisições excedido; tente em instantes",
                            headers={"Retry-After": "60"})
    now = datetime.now(timezone.utc)
    last = k.last_used_at
    if last is None or (now - (last if last.tzinfo else last.replace(tzinfo=timezone.utc))).total_seconds() > 300:
        k.last_used_at = now          # grava no máximo a cada 5 min por chave
        db.commit()
    return k.provider


def require_admin(x_admin_token: str = Header(...)) -> None:
    token = get_settings().admin_token
    if not token or not secrets.compare_digest(x_admin_token, token):
        raise HTTPException(403, "Não autorizado")


# --- Cifragem de credenciais de ERP (Fernet). Em produção a chave mestra vem de cofre/KMS. ---

def _fernet():
    from cryptography.fernet import Fernet
    mk = get_settings().master_key
    if not mk:
        raise RuntimeError("CERNE_MASTER_KEY não configurada")
    return Fernet(mk.encode())


def encrypt_json(data: dict) -> bytes:
    return _fernet().encrypt(json.dumps(data).encode())


def decrypt_json(blob: bytes) -> dict:
    return json.loads(_fernet().decrypt(bytes(blob)))
