"""Núcleo sem dependências externas: separação SCM x SVA e trilha de auditoria.

Regra de ouro: este código NÃO decide o que é SCM ou SVA. Ele aplica a
classificação cadastrada e validada pelo provedor (com contador/advogado) e
sinaliza tudo o que não tem base documental.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP

CENT = Decimal("0.01")
GENESIS = "0" * 64

# Códigos de alerta por linha (agrupados no relatório de evidências).
SEM_REGRA = "SEM_REGRA"
NAO_VALIDADA = "NAO_VALIDADA"
SVA_SEM_ATIVACAO = "SVA_SEM_ATIVACAO"
SVA_SEM_USO = "SVA_SEM_USO"
ALERTAS = {
    SEM_REGRA: ("Serviço sem classificação cadastrada",
                "Cadastrar o serviço no catálogo (ou mapear o código do ERP) e validar com o contador."),
    NAO_VALIDADA: ("Serviço sem classificação validada",
                   "Validar a classificação com o contador e registrar no cadastro."),
    SVA_SEM_ATIVACAO: ("SVA sem ativação registrada para o cliente",
                       "Localizar o aceite do assinante ou rever a cobrança do item."),
    SVA_SEM_USO: ("SVA ativo sem registro de uso no período",
                  "Verificar se o serviço está de fato disponível e sendo usado."),
}


def money(value) -> Decimal:
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class ServiceRule:
    code: str
    kind: str            # "SCM" | "SVA"
    validated: bool      # classificação validada por profissional habilitado
    rule_version: str


@dataclass(frozen=True)
class InvoiceLine:
    service_code: str
    amount: Decimal
    description: str = ""


@dataclass
class SplitResult:
    total_scm: Decimal = Decimal("0.00")
    total_sva: Decimal = Decimal("0.00")
    total_pendente: Decimal = Decimal("0.00")
    lines: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def total(self) -> Decimal:
        return self.total_scm + self.total_sva + self.total_pendente


def split_invoice(
    lines: list[InvoiceLine],
    rules: dict[str, ServiceRule],
    active_sva_codes: set[str],
    used_sva_codes: set[str],
) -> SplitResult:
    """Separa a fatura em SCM, SVA e PENDENTE (precisa de revisão humana).

    - Sem regra cadastrada ou regra não validada -> PENDENTE.
    - SVA sem ativação registrada para o cliente -> PENDENTE.
    - SVA ativo, mas sem registro de uso no período -> conta como SVA, com alerta.
    """
    res = SplitResult()
    for ln in lines:
        amount = money(ln.amount)
        rule = rules.get(ln.service_code)
        bucket, why, alert = "PENDENTE", None, None

        if rule is None:
            why, alert = f"serviço '{ln.service_code}' sem classificação cadastrada", SEM_REGRA
        elif not rule.validated:
            why, alert = f"classificação de '{ln.service_code}' ainda não validada", NAO_VALIDADA
        elif rule.kind == "SVA" and ln.service_code not in active_sva_codes:
            why, alert = (f"SVA '{ln.service_code}' sem ativação registrada para o cliente",
                          SVA_SEM_ATIVACAO)
        else:
            bucket = rule.kind
            if rule.kind == "SVA" and ln.service_code not in used_sva_codes:
                alert = SVA_SEM_USO
                res.warnings.append(
                    f"SVA '{ln.service_code}' ativo, mas sem registro de uso no período"
                )
        if why:
            res.warnings.append(why)

        if bucket == "SCM":
            res.total_scm += amount
        elif bucket == "SVA":
            res.total_sva += amount
        else:
            res.total_pendente += amount

        res.lines.append(
            {
                "service_code": ln.service_code,
                "description": ln.description,
                "amount": str(amount),
                "bucket": bucket,
                "rule_version": rule.rule_version if rule else None,
                "alert": alert,
            }
        )
    return res


# --- Trilha de auditoria com encadeamento de hash (evidência de adulteração) ---

def chain_hash(prev_hash: str, payload: dict, ts: str) -> str:
    body = json.dumps(
        {"prev": prev_hash, "ts": ts, "payload": payload},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def verify_chain(entries: list[dict]) -> tuple[bool, int | None]:
    """entries em ordem cronológica. Retorna (ok, índice da 1ª inconsistência)."""
    prev = GENESIS
    for i, e in enumerate(entries):
        if e["prev_hash"] != prev or chain_hash(prev, e["payload"], e["ts"]) != e["hash"]:
            return False, i
        prev = e["hash"]
    return True, None
