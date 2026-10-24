from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .. import core
from ..db import is_postgres
from ..models import AuditEntry

_LOCK_NS = 7341  # namespace do advisory lock da trilha


def audit(db: Session, provider_id: int, action: str, payload: dict) -> AuditEntry:
    """Acrescenta à trilha encadeada do provedor.

    No Postgres, pg_advisory_xact_lock serializa as gravações do mesmo provedor até o
    commit, evitando dois registros com o mesmo prev_hash. A restrição única
    (provider_id, prev_hash) é a segunda barreira. No SQLite as escritas já são serializadas.
    """
    if is_postgres():
        db.execute(text("SELECT pg_advisory_xact_lock(:ns, :pid)"), {"ns": _LOCK_NS, "pid": provider_id})
    last = db.scalar(select(AuditEntry).where(AuditEntry.provider_id == provider_id)
                     .order_by(AuditEntry.id.desc()).limit(1))
    prev = last.hash if last else core.GENESIS
    ts = datetime.now(timezone.utc).isoformat()
    e = AuditEntry(provider_id=provider_id, ts=ts, action=action, payload=payload,
                   prev_hash=prev, hash=core.chain_hash(prev, payload, ts))
    db.add(e)
    db.flush()
    return e


def verify(db: Session, provider_id: int) -> dict:
    rows = db.execute(select(AuditEntry.id, AuditEntry.prev_hash, AuditEntry.payload,
                             AuditEntry.ts, AuditEntry.hash)
                      .where(AuditEntry.provider_id == provider_id).order_by(AuditEntry.id)).all()
    ok, idx = core.verify_chain([{"prev_hash": r.prev_hash, "payload": r.payload,
                                  "ts": r.ts, "hash": r.hash} for r in rows])
    return {"integra": ok, "primeira_inconsistencia": rows[idx].id if idx is not None else None,
            "entradas": len(rows), "hash_final": rows[-1].hash if rows else core.GENESIS}
