from __future__ import annotations

import logging
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from . import __version__
from .config import get_settings
from .db import Base, SessionLocal, engine
from .routers import account, activations, admin, audit, catalog, invoices, reports, tax

settings = get_settings()
logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("cerne.http")

if settings.auto_create_schema:
    Base.metadata.create_all(engine)

app = FastAPI(
    title="Cerne API · Separação e Comprovação SCM × SVA", version=__version__,
    docs_url="/docs" if settings.docs_enabled else None,
    redoc_url=None, openapi_url="/openapi.json" if settings.docs_enabled else None,
)


@app.middleware("http")
async def access_log(request: Request, call_next):
    """Log de acesso sem cabeçalhos, corpo ou query string (evita credenciais e dados pessoais)."""
    rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
    t0 = time.perf_counter()
    try:
        resp = await call_next(request)
    except Exception:
        log.exception("rid=%s %s %s erro", rid, request.method, request.url.path)
        return JSONResponse({"detail": "Erro interno", "request_id": rid}, status_code=500)
    resp.headers["X-Request-Id"] = rid
    log.info("rid=%s %s %s %s %.1fms", rid, request.method, request.url.path, resp.status_code,
             (time.perf_counter() - t0) * 1000)
    return resp


@app.get("/healthz", tags=["infra"])
def healthz():
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
    except Exception:
        return JSONResponse({"status": "erro", "db": False}, status_code=503)
    return {"status": "ok", "db": True, "version": __version__}


for r in (admin, account, catalog, activations, invoices, audit, tax, reports):
    app.include_router(r.router)
