from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_validator

PERIOD = r"^\d{4}-(0[1-9]|1[0-2])$"


class ProviderCreate(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    cnpj: str = Field(min_length=14, max_length=18)


class KeyCreate(BaseModel):
    label: str | None = Field(default=None, max_length=100)


class ServiceCreate(BaseModel):
    code: str = Field(min_length=1, max_length=60, pattern=r"^[A-Za-z0-9_.\-]+$")
    name: str = Field(min_length=1, max_length=200)
    kind: Literal["SCM", "SVA"]
    criteria: str = Field(min_length=20, description="Fundamento da classificação")
    contract_ref: str | None = Field(default=None, max_length=200)


class ServiceUpdate(BaseModel):
    """Alterar tipo ou critério gera nova versão da regra e exige nova validação."""
    name: str | None = Field(default=None, min_length=1, max_length=200)
    kind: Literal["SCM", "SVA"] | None = None
    criteria: str | None = Field(default=None, min_length=20)
    contract_ref: str | None = Field(default=None, max_length=200)


class ServiceValidate(BaseModel):
    validated_by: str = Field(min_length=5, max_length=200,
                              description="Nome e registro (CRC/OAB) de quem validou")


class ActivationCreate(BaseModel):
    customer_ref: str = Field(min_length=1, max_length=80)
    service_code: str = Field(min_length=1, max_length=60)
    consent_ref: str | None = Field(default=None, max_length=200)
    activated_at: datetime


class UsageCreate(BaseModel):
    event_type: str = Field(min_length=1, max_length=60)
    occurred_at: datetime
    meta: dict | None = None


class InvoiceLineIn(BaseModel):
    service_code: str = Field(min_length=1, max_length=60)
    amount: Decimal = Field(ge=0, max_digits=14, decimal_places=2)
    description: str = Field(default="", max_length=300)


class InvoiceSplitIn(BaseModel):
    invoice_ref: str = Field(min_length=1, max_length=80)
    customer_ref: str = Field(min_length=1, max_length=80)
    period: str = Field(pattern=PERIOD)
    lines: list[InvoiceLineIn] = Field(min_length=1, max_length=500)


class InvoiceSplitOut(BaseModel):
    invoice_ref: str
    total_scm: str
    total_sva: str
    total_pendente: str
    lines: list[dict]
    warnings: list[str]


class TaxParameterCreate(BaseModel):
    tax: Literal["ICMS", "ISS", "IBS", "CBS"]
    applies_to: Literal["SCM", "SVA"]
    rate: Decimal = Field(ge=0, le=100, max_digits=7, decimal_places=4, description="Alíquota em %")
    valid_from: datetime
    valid_to: datetime | None = None
    source_note: str | None = Field(default=None, max_length=300)


class TaxParameterClose(BaseModel):
    valid_to: datetime


class ErpCredentialIn(BaseModel):
    erp: Literal["hubsoft"] = "hubsoft"
    base_url: str = Field(min_length=8, max_length=300)
    client_id: str
    client_secret: str
    username: str
    password: str

    @field_validator("base_url")
    @classmethod
    def https_only(cls, v: str) -> str:
        if not v.startswith("https://"):
            raise ValueError("use https://")
        return v.rstrip("/")
