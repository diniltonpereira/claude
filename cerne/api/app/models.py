from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import (JSON, Boolean, DateTime, ForeignKey, Index, Integer, LargeBinary, Numeric,
                        String, Text, UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base

MONEY = Numeric(14, 2)


def now() -> datetime:
    return datetime.now(timezone.utc)


class Provider(Base):
    """Provedor (cliente da API). Cada um acessa apenas os próprios dados."""
    __tablename__ = "providers"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    cnpj: Mapped[str] = mapped_column(String(18), unique=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ApiKey(Base):
    """Chave de API do provedor. Só o hash é guardado; o prefixo identifica a chave em telas e logs."""
    __tablename__ = "api_keys"
    id: Mapped[int] = mapped_column(primary_key=True)
    provider_id: Mapped[int] = mapped_column(ForeignKey("providers.id"), index=True)
    prefix: Mapped[str] = mapped_column(String(16))
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    label: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provider: Mapped[Provider] = relationship()


class Service(Base):
    """Catálogo: cada oferta classificada como SCM ou SVA, com critério documentado."""
    __tablename__ = "services"
    __table_args__ = (UniqueConstraint("provider_id", "code"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    provider_id: Mapped[int] = mapped_column(ForeignKey("providers.id"), index=True)
    code: Mapped[str] = mapped_column(String(60))
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(3))            # SCM | SVA
    criteria: Mapped[str] = mapped_column(Text)             # por que é SCM/SVA
    contract_ref: Mapped[str | None] = mapped_column(String(200))
    rule_version: Mapped[str] = mapped_column(String(20), default="v1")
    validated_by: Mapped[str | None] = mapped_column(String(200))  # nome + registro (CRC/OAB)
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    @property
    def validated(self) -> bool:
        return bool(self.validated_by and self.validated_at)


class Activation(Base):
    """Contratação/ativação de um SVA por um assinante (com prova de consentimento)."""
    __tablename__ = "activations"
    __table_args__ = (Index("ix_activations_provider_customer", "provider_id", "customer_ref"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    provider_id: Mapped[int] = mapped_column(ForeignKey("providers.id"), index=True)
    customer_ref: Mapped[str] = mapped_column(String(80))  # id do cliente no ERP (nunca nome/CPF)
    service_code: Mapped[str] = mapped_column(String(60))
    consent_ref: Mapped[str | None] = mapped_column(String(200))      # aceite/contrato/log
    activated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    canceled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    usage: Mapped[list["UsageEvent"]] = relationship(back_populates="activation")


class UsageEvent(Base):
    __tablename__ = "usage_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    activation_id: Mapped[int] = mapped_column(ForeignKey("activations.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(60))     # login, download, sessão...
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    meta: Mapped[dict | None] = mapped_column(JSON)
    activation: Mapped[Activation] = relationship(back_populates="usage")


class InvoiceSplit(Base):
    """Cada separação gravada. Reprocessar cria nova linha; vale a mais recente (maior id)."""
    __tablename__ = "invoice_splits"
    __table_args__ = (Index("ix_splits_provider_period_ref", "provider_id", "period", "invoice_ref"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    provider_id: Mapped[int] = mapped_column(ForeignKey("providers.id"), index=True)
    invoice_ref: Mapped[str] = mapped_column(String(80))
    customer_ref: Mapped[str] = mapped_column(String(80))
    period: Mapped[str] = mapped_column(String(7))          # YYYY-MM
    total_scm: Mapped[Decimal] = mapped_column(MONEY)
    total_sva: Mapped[Decimal] = mapped_column(MONEY)
    total_pendente: Mapped[Decimal] = mapped_column(MONEY)
    detail: Mapped[dict] = mapped_column(JSON)              # linhas, avisos, versões de regra
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class AuditEntry(Base):
    """Trilha encadeada. (provider_id, prev_hash) único: impede bifurcação da cadeia."""
    __tablename__ = "audit_entries"
    __table_args__ = (UniqueConstraint("provider_id", "prev_hash", name="uq_audit_provider_prev"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider_id: Mapped[int] = mapped_column(ForeignKey("providers.id"), index=True)
    ts: Mapped[str] = mapped_column(String(40))             # ISO-8601 UTC (entra no hash)
    action: Mapped[str] = mapped_column(String(60))
    payload: Mapped[dict] = mapped_column(JSON)
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64), unique=True)


class TaxParameter(Base):
    """Parâmetros tributários por vigência (ICMS/ISS hoje; IBS/CBS na transição)."""
    __tablename__ = "tax_parameters"
    id: Mapped[int] = mapped_column(primary_key=True)
    provider_id: Mapped[int] = mapped_column(ForeignKey("providers.id"), index=True)
    tax: Mapped[str] = mapped_column(String(10))            # ICMS | ISS | IBS | CBS
    applies_to: Mapped[str] = mapped_column(String(3))      # SCM | SVA
    rate: Mapped[Decimal] = mapped_column(Numeric(7, 4))    # alíquota (%) informada pelo provedor/contador
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_note: Mapped[str | None] = mapped_column(String(300))


class ErpCredential(Base):
    """Credenciais do ERP por provedor, cifradas (Fernet). Nunca devolvidas pela API."""
    __tablename__ = "erp_credentials"
    id: Mapped[int] = mapped_column(primary_key=True)
    provider_id: Mapped[int] = mapped_column(ForeignKey("providers.id"), unique=True)
    erp: Mapped[str] = mapped_column(String(30))            # hubsoft
    base_url: Mapped[str] = mapped_column(String(300))
    secret: Mapped[bytes] = mapped_column(LargeBinary)      # JSON cifrado
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
