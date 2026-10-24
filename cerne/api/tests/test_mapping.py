import os, sys  # noqa: E401
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from decimal import Decimal
from app.mapping import (DEFAULT_FIELD_MAP, MappingError, normalize_invoice,
                         parse_amount, shape_of)

RAW = {"id_fatura": 9001, "id_cliente": "C-77", "nome": "Fulano",
       "itens": [{"id_servico": 101, "valor": "99,90", "descricao": "Internet 300MB"},
                 {"id_servico": 205, "valor": 9.9, "descricao": "Antivírus"},
                 {"id_servico": 999, "valor": "1.234,56", "descricao": "Outro"}]}
SMAP = {"101": "INTERNET", "205": "ANTIVIRUS"}

def test_normaliza_e_marca_sem_mapeamento():
    p, un = normalize_invoice(RAW, "2026-10", DEFAULT_FIELD_MAP, SMAP)
    assert p["invoice_ref"] == "9001" and p["customer_ref"] == "C-77"
    assert [l["service_code"] for l in p["lines"]] == ["INTERNET", "ANTIVIRUS", "999"]
    assert [l["amount"] for l in p["lines"]] == ["99.90", "9.9", "1234.56"]
    assert un == {"999"}
    assert "Fulano" not in str(p)          # nenhum dado pessoal vaza para o payload

def test_valores():
    assert parse_amount("R$ 1.234,56") == Decimal("1234.56")
    assert parse_amount(10) == Decimal("10")

def test_fatura_incompleta():
    try:
        normalize_invoice({"id_fatura": 1}, "2026-10", DEFAULT_FIELD_MAP, {})
    except MappingError:
        return
    raise AssertionError("deveria falhar")

def test_shape_sem_valores():
    sh = shape_of(RAW)
    assert sh["nome"] == "str" and sh["itens"][0]["valor"] == "str"

if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f(); print("ok", n)
