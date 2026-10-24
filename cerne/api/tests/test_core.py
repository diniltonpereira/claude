import os, sys  # noqa: E401
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from decimal import Decimal
from app.core import (InvoiceLine, ServiceRule, split_invoice, chain_hash,
                      verify_chain, GENESIS)

RULES = {
    "INTERNET": ServiceRule("INTERNET", "SCM", True, "v1"),
    "ANTIVIRUS": ServiceRule("ANTIVIRUS", "SVA", True, "v1"),
    "CURSOS": ServiceRule("CURSOS", "SVA", False, "v1"),
}

def L(code, amt): return InvoiceLine(code, Decimal(amt))

def test_split_basico():
    r = split_invoice([L("INTERNET", "99.90"), L("ANTIVIRUS", "9.90")], RULES,
                      {"ANTIVIRUS"}, {"ANTIVIRUS"})
    assert (r.total_scm, r.total_sva, r.total_pendente) == (Decimal("99.90"), Decimal("9.90"), Decimal("0.00"))
    assert r.total == Decimal("109.80") and not r.warnings

def test_sva_sem_ativacao_vai_para_pendente():
    r = split_invoice([L("ANTIVIRUS", "9.90")], RULES, set(), set())
    assert r.total_sva == 0 and r.total_pendente == Decimal("9.90") and r.warnings

def test_regra_nao_validada_e_inexistente():
    r = split_invoice([L("CURSOS", "20"), L("XYZ", "5")], RULES, {"CURSOS"}, set())
    assert r.total_pendente == Decimal("25.00") and len(r.warnings) == 2

def test_sva_sem_uso_conta_mas_alerta():
    r = split_invoice([L("ANTIVIRUS", "9.90")], RULES, {"ANTIVIRUS"}, set())
    assert r.total_sva == Decimal("9.90") and len(r.warnings) == 1

def _build(n):
    out, prev = [], GENESIS
    for i in range(n):
        p, ts = {"acao": f"a{i}"}, f"2026-10-20T10:00:0{i}Z"
        h = chain_hash(prev, p, ts)
        out.append({"prev_hash": prev, "payload": p, "ts": ts, "hash": h})
        prev = h
    return out

def test_cadeia_integra_e_adulteracao():
    es = _build(4)
    assert verify_chain(es) == (True, None)
    es[2]["payload"]["acao"] = "alterado"
    assert verify_chain(es) == (False, 2)

if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f(); print("ok", n)
