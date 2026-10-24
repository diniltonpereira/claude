from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .. import core, schemas
from ..models import Activation, InvoiceSplit, Service
from .audit import audit


def utc(dt: datetime) -> datetime:
    """SQLite devolve datas sem fuso; normaliza para UTC antes de comparar."""
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def period_bounds(period: str) -> tuple[datetime, datetime]:
    y, m = map(int, period.split("-"))
    start = datetime(y, m, 1, tzinfo=timezone.utc)
    end = datetime(y + (m == 12), 1 if m == 12 else m + 1, 1, tzinfo=timezone.utc)
    return start, end


def run_split(db: Session, provider_id: int, body: schemas.InvoiceSplitIn) -> schemas.InvoiceSplitOut:
    start, end = period_bounds(body.period)

    rules = {s.code: core.ServiceRule(s.code, s.kind, s.validated and s.active, s.rule_version)
             for s in db.scalars(select(Service).where(Service.provider_id == provider_id))}

    acts = db.scalars(select(Activation).options(selectinload(Activation.usage)).where(
        Activation.provider_id == provider_id, Activation.customer_ref == body.customer_ref,
        Activation.activated_at < end)).all()
    active = {a.service_code for a in acts if a.canceled_at is None or utc(a.canceled_at) >= start}
    used = {a.service_code for a in acts if any(start <= utc(u.occurred_at) < end for u in a.usage)}

    res = core.split_invoice(
        [core.InvoiceLine(l.service_code, l.amount, l.description) for l in body.lines],
        rules, active, used)

    out = schemas.InvoiceSplitOut(
        invoice_ref=body.invoice_ref, total_scm=str(res.total_scm), total_sva=str(res.total_sva),
        total_pendente=str(res.total_pendente), lines=res.lines, warnings=res.warnings)
    db.add(InvoiceSplit(provider_id=provider_id, invoice_ref=body.invoice_ref,
                        customer_ref=body.customer_ref, period=body.period,
                        total_scm=res.total_scm, total_sva=res.total_sva,
                        total_pendente=res.total_pendente,
                        detail={"lines": res.lines, "warnings": res.warnings}))
    audit(db, provider_id, "invoice.split", {"customer_ref": body.customer_ref,
                                             "period": body.period, **out.model_dump()})
    return out
