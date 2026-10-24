"""Conversão pura (sem dependências) de uma fatura do ERP para o formato da API.

Os nomes de campo do Hubsoft abaixo são HIPÓTESES. Ajuste `field_map.json`
depois de rodar `python -m app.sync --inspect` com as credenciais do piloto.
Minimização (LGPD): só são extraídos identificadores e valores. Nome, CPF,
endereço e contatos do assinante NÃO são lidos nem armazenados.
"""
from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation

DEFAULT_FIELD_MAP = {
    "invoice_ref": "id_fatura",
    "customer_ref": "id_cliente",
    "items": "itens",
    "item_service_code": "id_servico",
    "item_amount": "valor",
    "item_description": "descricao",
}


class MappingError(ValueError):
    pass


def dget(obj, path: str, default=None):
    """Acesso por caminho pontilhado: 'cliente.id' -> obj['cliente']['id']."""
    cur = obj
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return default
    return cur


def parse_amount(value) -> Decimal:
    if isinstance(value, (int, float, Decimal)):
        return Decimal(str(value))
    s = str(value).replace("R$", "").replace(" ", "").strip()
    if "," in s:                       # formato brasileiro: 1.234,56
        s = s.replace(".", "").replace(",", ".")
    try:
        return Decimal(s)
    except InvalidOperation as e:
        raise MappingError(f"valor monetário inválido: {value!r}") from e


def load_json(path: str | None, default: dict) -> dict:
    if not path:
        return dict(default)
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def normalize_invoice(raw: dict, period: str, field_map: dict, service_map: dict):
    """Retorna (payload no formato InvoiceSplitIn, códigos sem mapeamento).

    Código de serviço sem mapeamento NÃO é descartado: segue com o código bruto
    e cai em PENDENTE na separação (comportamento seguro).
    """
    ref = dget(raw, field_map["invoice_ref"])
    cust = dget(raw, field_map["customer_ref"])
    items = dget(raw, field_map["items"])
    if ref is None or cust is None or not isinstance(items, list):
        raise MappingError("fatura sem invoice_ref/customer_ref/itens; revise o field_map")

    lines, unmapped = [], set()
    for it in items:
        raw_code = dget(it, field_map["item_service_code"])
        if raw_code is None:
            raise MappingError("item sem código de serviço; revise o field_map")
        raw_code = str(raw_code)
        code = service_map.get(raw_code)
        if code is None:
            unmapped.add(raw_code)
            code = raw_code
        lines.append({
            "service_code": code,
            "amount": str(parse_amount(dget(it, field_map["item_amount"], 0))),
            "description": str(dget(it, field_map["item_description"], "") or ""),
        })
    payload = {"invoice_ref": str(ref), "customer_ref": str(cust), "period": period, "lines": lines}
    return payload, unmapped


def shape_of(obj, depth: int = 0, max_depth: int = 3):
    """Estrutura (chaves e tipos) sem valores — para inspecionar a API sem expor dados pessoais."""
    if depth >= max_depth:
        return type(obj).__name__
    if isinstance(obj, dict):
        return {k: shape_of(v, depth + 1, max_depth) for k, v in obj.items()}
    if isinstance(obj, list):
        return [shape_of(obj[0], depth + 1, max_depth)] if obj else []
    return type(obj).__name__
