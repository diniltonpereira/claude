"""Job de sincronização Hubsoft -> separação SCM x SVA (somente leitura no ERP).

Uso:
  python -m app.sync --inspect --provider-id 1          # mostra só a ESTRUTURA da API
  python -m app.sync --provider-id 1 --period 2026-10 --dry-run
  python -m app.sync --provider-id 1 --period 2026-10
  python -m app.sync --all                              # todos os provedores com credencial; mês anterior

Credenciais: as cadastradas pelo provedor (PUT /v1/erp/credentials, cifradas) ou, na falta
delas, as variáveis HUBSOFT_* do ambiente (útil no piloto).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date

from . import mapping
from .integrations.hubsoft import HubsoftClient

ENV_KEYS = ["HUBSOFT_BASE_URL", "HUBSOFT_CLIENT_ID", "HUBSOFT_CLIENT_SECRET",
            "HUBSOFT_USERNAME", "HUBSOFT_PASSWORD"]


def client_from_env() -> HubsoftClient | None:
    if any(not os.getenv(k) for k in ENV_KEYS):
        return None
    return HubsoftClient(*(os.environ[k] for k in ENV_KEYS))


def client_for_provider(db, provider_id: int) -> HubsoftClient | None:
    from sqlalchemy import select
    from .models import ErpCredential
    from .security import decrypt_json
    c = db.scalar(select(ErpCredential).where(ErpCredential.provider_id == provider_id))
    if not c:
        return None
    s = decrypt_json(c.secret)
    return HubsoftClient(c.base_url, s["client_id"], s["client_secret"], s["username"], s["password"])


def previous_period(today: date | None = None) -> str:
    t = today or date.today()
    y, m = (t.year - 1, 12) if t.month == 1 else (t.year, t.month - 1)
    return f"{y}-{m:02d}"


def period_params(period: str) -> dict:
    """TODO(confirmar): nomes dos filtros de data na rota de faturas do Hubsoft."""
    import calendar
    y, m = map(int, period.split("-"))
    return {"data_inicio": f"{period}-01", "data_fim": f"{period}-{calendar.monthrange(y, m)[1]:02d}"}


def sync_provider(db, hub: HubsoftClient, provider_id: int, period: str, fmap: dict, smap: dict,
                  dry_run: bool = False, force: bool = False, limit: int = 0) -> dict:
    from sqlalchemy import select
    from . import schemas
    from .models import InvoiceSplit
    from .services.audit import audit
    from .services.split import run_split

    stats = dict(lidas=0, separadas=0, ja_processadas=0, erros=0, com_pendencia=0)
    unmapped_all: set[str] = set()
    pendente_total = 0.0

    for raw in hub.paginate("invoices", period_params(period)):
        if limit and stats["lidas"] >= limit:
            break
        stats["lidas"] += 1
        try:
            payload, unmapped = mapping.normalize_invoice(raw, period, fmap, smap)
            body = schemas.InvoiceSplitIn(**payload)
        except (mapping.MappingError, ValueError) as e:
            stats["erros"] += 1
            print(f"[erro] {type(e).__name__}: {str(e)[:200]}", file=sys.stderr)
            continue
        unmapped_all |= unmapped

        if not force and db.scalar(select(InvoiceSplit.id).where(
                InvoiceSplit.provider_id == provider_id,
                InvoiceSplit.invoice_ref == payload["invoice_ref"],
                InvoiceSplit.period == period).limit(1)):
            stats["ja_processadas"] += 1
            continue
        if dry_run:
            continue
        out = run_split(db, provider_id, body)
        db.commit()   # uma transação por fatura: falha no meio não perde o que já foi separado
        stats["separadas"] += 1
        if float(out.total_pendente) > 0:
            stats["com_pendencia"] += 1
            pendente_total += float(out.total_pendente)

    if not dry_run:
        audit(db, provider_id, "sync.run", {"period": period, **stats, "unmapped_codes": sorted(unmapped_all)})
        db.commit()
    return {"provider_id": provider_id, "periodo": period, "dry_run": dry_run, **stats,
            "valor_pendente_total": round(pendente_total, 2),
            "codigos_sem_mapeamento": sorted(unmapped_all)}


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m app.sync")
    ap.add_argument("--provider-id", type=int)
    ap.add_argument("--all", action="store_true", help="todos os provedores ativos com credencial")
    ap.add_argument("--period", help="YYYY-MM (padrão: mês anterior)")
    ap.add_argument("--dry-run", action="store_true", help="normaliza e resume, sem gravar")
    ap.add_argument("--inspect", action="store_true", help="imprime só a estrutura da 1ª fatura")
    ap.add_argument("--force", action="store_true", help="reprocessa faturas já separadas")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--field-map", default=os.getenv("FIELD_MAP"))
    ap.add_argument("--service-map", default=os.getenv("SERVICE_MAP"))
    a = ap.parse_args(argv)
    period = a.period or previous_period()

    from sqlalchemy import select
    from . import models
    from .db import SessionLocal

    fmap = mapping.load_json(a.field_map, mapping.DEFAULT_FIELD_MAP)
    smap = mapping.load_json(a.service_map, {})

    with SessionLocal() as db:
        if a.all:
            ids = db.scalars(select(models.ErpCredential.provider_id).join(
                models.Provider, models.Provider.id == models.ErpCredential.provider_id)
                .where(models.Provider.active.is_(True))).all()
        elif a.provider_id:
            ids = [a.provider_id]
        elif a.inspect:
            ids = [None]
        else:
            ap.error("informe --provider-id ou --all")

        results, failed = [], 0
        for pid in ids:
            hub = (client_for_provider(db, pid) if pid else None) or client_from_env()
            if hub is None:
                print(f"[erro] provedor {pid}: sem credencial cadastrada nem HUBSOFT_* no ambiente",
                      file=sys.stderr)
                failed += 1
                continue
            with hub:
                if a.inspect:
                    first = next(hub.paginate("invoices", period_params(period)), None)
                    print(json.dumps(mapping.shape_of(first), indent=2, ensure_ascii=False) if first
                          else "Nenhuma fatura retornada.")
                    return
                try:
                    results.append(sync_provider(db, hub, pid, period, fmap, smap,
                                                 dry_run=a.dry_run, force=a.force, limit=a.limit))
                except Exception as e:   # um provedor com erro não interrompe os demais
                    db.rollback()
                    failed += 1
                    print(f"[erro] provedor {pid}: {type(e).__name__}", file=sys.stderr)

    print(json.dumps(results if a.all else (results[0] if results else {}), indent=2, ensure_ascii=False))
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
