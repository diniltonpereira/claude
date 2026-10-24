"""Testes de integração dos endpoints (TestClient), incluindo isolamento entre provedores."""

from sqlalchemy import update

from app.db import SessionLocal
from app.models import AuditEntry
from conftest import ADMIN, new_provider

CRIT = "Serviço de valor adicionado conforme contrato e art. 61 da LGT."


def seed_catalog(client, h, validate=True):
    for code, kind in (("INTERNET", "SCM"), ("ANTIVIRUS", "SVA"), ("CURSOS", "SVA")):
        r = client.post("/v1/services", json={"code": code, "name": code.title(), "kind": kind,
                                              "criteria": CRIT}, headers=h)
        assert r.status_code == 201, r.text
    if validate:
        for code in ("INTERNET", "ANTIVIRUS"):
            r = client.post(f"/v1/services/{code}/validate",
                            json={"validated_by": "Contadora Exemplo CRC/MG 000000"}, headers=h)
            assert r.status_code == 200 and r.json()["validated"]


def activate(client, h, customer="C-1", code="ANTIVIRUS", when="2026-09-01T00:00:00Z"):
    r = client.post("/v1/activations", json={"customer_ref": customer, "service_code": code,
                                             "consent_ref": "CONS-1", "activated_at": when}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["activation_id"]


def split(client, h, ref="F-1", customer="C-1", period="2026-10", lines=None):
    lines = lines or [{"service_code": "INTERNET", "amount": "99.90"},
                      {"service_code": "ANTIVIRUS", "amount": "9.90"}]
    return client.post("/v1/invoices/split", json={"invoice_ref": ref, "customer_ref": customer,
                                                   "period": period, "lines": lines}, headers=h)


def test_healthz(client):
    assert client.get("/healthz").json()["status"] == "ok"


def test_admin_exige_token(client):
    assert client.post("/v1/admin/providers", json={"name": "X", "cnpj": "00.000.000/0001-00"},
                       headers={"X-Admin-Token": "errado"}).status_code == 403
    assert client.get("/v1/admin/providers").status_code == 422          # header ausente


def test_chave_invalida(client):
    assert client.get("/v1/services", headers={"X-API-Key": "cerne_x_y"}).status_code == 401


def test_fluxo_completo(client, provider):
    _, h = provider
    seed_catalog(client, h)
    aid = activate(client, h)
    assert client.post(f"/v1/activations/{aid}/usage", json={
        "event_type": "login", "occurred_at": "2026-10-05T12:00:00Z"}, headers=h).status_code == 201

    r = split(client, h, lines=[{"service_code": "INTERNET", "amount": "99.90"},
                                {"service_code": "ANTIVIRUS", "amount": "9.90"},
                                {"service_code": "CURSOS", "amount": "20"}])
    assert r.status_code == 200, r.text
    d = r.json()
    assert (d["total_scm"], d["total_sva"], d["total_pendente"]) == ("99.90", "9.90", "20.00")
    assert [l["alert"] for l in d["lines"]] == [None, None, "NAO_VALIDADA"]

    s = client.get("/v1/reports/2026-10/summary", headers=h).json()
    assert s["faturas"] == 1 and s["faturas_com_pendencia"] == 1 and s["total_pendente"] == "20.00"
    assert s["avisos_por_tipo"][0]["codigo"] == "NAO_VALIDADA"
    assert s["trilha"]["integra"]

    ev = client.get("/v1/evidence/C-1", headers=h).json()
    assert ev[0]["usage_events"] == 1


def test_sva_sem_ativacao_e_sem_uso(client, provider):
    _, h = provider
    seed_catalog(client, h)
    d = split(client, h).json()
    assert d["total_pendente"] == "9.90" and d["lines"][1]["alert"] == "SVA_SEM_ATIVACAO"
    activate(client, h)
    d = split(client, h, ref="F-2").json()
    assert d["total_sva"] == "9.90" and d["lines"][1]["alert"] == "SVA_SEM_USO"


def test_ativacao_cancelada_antes_do_periodo(client, provider):
    _, h = provider
    seed_catalog(client, h)
    aid = activate(client, h)
    assert client.post(f"/v1/activations/{aid}/cancel", headers=h).status_code == 200
    assert client.post(f"/v1/activations/{aid}/cancel", headers=h).status_code == 409
    # cancelada agora (depois de 2026-10): continua valendo para 2026-10? cancel = hoje >= início do período
    d = split(client, h, period="2026-10").json()
    assert d["total_sva"] == "9.90"


def test_ativacao_so_para_sva(client, provider):
    _, h = provider
    seed_catalog(client, h)
    r = client.post("/v1/activations", json={"customer_ref": "C-1", "service_code": "INTERNET",
                                              "activated_at": "2026-09-01T00:00:00Z"}, headers=h)
    assert r.status_code == 422


def test_alterar_regra_exige_nova_validacao(client, provider):
    _, h = provider
    seed_catalog(client, h)
    r = client.put("/v1/services/ANTIVIRUS", json={"criteria": CRIT + " Revisado."}, headers=h).json()
    assert r["rule_version"] == "v2" and not r["validated"]
    activate(client, h)
    assert split(client, h).json()["total_pendente"] == "9.90"
    # alterar só o nome não muda a versão
    r = client.put("/v1/services/INTERNET", json={"name": "Fibra 500"}, headers=h).json()
    assert r["rule_version"] == "v1" and r["validated"]


def test_servico_inativo_vai_para_pendente(client, provider):
    _, h = provider
    seed_catalog(client, h)
    client.post("/v1/services/INTERNET/deactivate", headers=h)
    assert split(client, h).json()["total_pendente"] == "109.80"


def test_isolamento_entre_provedores(client, provider):
    _, ha = provider
    _, hb = new_provider(client, cnpj="22.222.222/0001-22", name="Outro")
    seed_catalog(client, ha)
    aid = activate(client, ha)
    split(client, ha)

    assert client.get("/v1/services", headers=hb).json() == []
    assert client.get("/v1/services/INTERNET", headers=hb).status_code == 404
    assert client.post(f"/v1/activations/{aid}/usage", json={
        "event_type": "x", "occurred_at": "2026-10-01T00:00:00Z"}, headers=hb).status_code == 404
    assert client.post(f"/v1/activations/{aid}/cancel", headers=hb).status_code == 404
    assert client.get("/v1/activations", headers=hb).json() == []
    assert client.get("/v1/evidence/C-1", headers=hb).json() == []
    assert client.get("/v1/invoices?period=2026-10", headers=hb).json()["total"] == 0
    assert client.get("/v1/invoices/F-1?period=2026-10", headers=hb).status_code == 404
    assert client.get("/v1/reports/2026-10/summary", headers=hb).json()["faturas"] == 0
    # B separa fatura com o mesmo código: sem catálogo próprio, tudo pendente
    assert split(client, hb).json()["total_pendente"] == "109.80"
    audit_b = client.get("/v1/audit", headers=hb).json()
    assert all(e["action"] != "service.created" for e in audit_b)
    assert client.get("/v1/audit/verify", headers=ha).json()["integra"]
    assert client.get("/v1/audit/verify", headers=hb).json()["integra"]


def test_trilha_detecta_adulteracao(client, provider):
    pid, h = provider
    seed_catalog(client, h)
    v = client.get("/v1/audit/verify", headers=h).json()
    assert v["integra"] and v["entradas"] == 6      # provider + 3 serviços + 2 validações
    with SessionLocal() as db:
        target = db.query(AuditEntry).filter_by(provider_id=pid).order_by(AuditEntry.id).all()[2]
        db.execute(update(AuditEntry).where(AuditEntry.id == target.id)
                   .values(payload={**target.payload, "kind": "SCM" if target.payload["kind"] == "SVA" else "SVA"}))
        db.commit()
    v = client.get("/v1/audit/verify", headers=h).json()
    assert not v["integra"] and v["primeira_inconsistencia"] == target.id


def test_reprocessamento_vale_o_mais_recente(client, provider):
    _, h = provider
    seed_catalog(client, h)
    split(client, h)                       # antivírus pendente (sem ativação)
    activate(client, h)
    split(client, h)                       # agora é SVA
    lst = client.get("/v1/invoices?period=2026-10", headers=h).json()
    assert lst["total"] == 1 and lst["items"][0]["total_sva"] == "9.90"
    det = client.get("/v1/invoices/F-1?period=2026-10", headers=h).json()
    assert len(det["historico"]) == 1 and det["lines"]
    assert client.get("/v1/invoices?period=2026-10&only_pending=true", headers=h).json()["total"] == 0


def test_validacao_de_entrada(client, provider):
    _, h = provider
    assert split(client, h, period="2026-13").status_code == 422
    assert split(client, h, lines=[{"service_code": "X", "amount": "-1"}]).status_code == 422
    assert client.post("/v1/services", json={"code": "A B", "name": "x", "kind": "SCM",
                                             "criteria": CRIT}, headers=h).status_code == 422
    assert client.post("/v1/services", json={"code": "A", "name": "x", "kind": "SCM",
                                             "criteria": "curto"}, headers=h).status_code == 422


def test_chaves_rotacao_e_revogacao(client, provider):
    pid, h = provider
    keys = client.get("/v1/keys", headers=h).json()
    assert len(keys) == 1
    assert client.delete(f"/v1/keys/{keys[0]['id']}", headers=h).status_code == 409   # última ativa
    new = client.post("/v1/keys", json={"label": "integração"}, headers=h).json()
    h2 = {"X-API-Key": new["api_key"]}
    assert client.delete(f"/v1/keys/{keys[0]['id']}", headers=h2).status_code == 200
    assert client.get("/v1/me", headers=h).status_code == 401
    assert client.get("/v1/me", headers=h2).json()["provider_id"] == pid
    # admin revoga e emite
    r = client.post(f"/v1/admin/providers/{pid}/keys", json={}, headers=ADMIN).json()
    assert client.delete(f"/v1/admin/keys/{new['key_id']}", headers=ADMIN).status_code == 200
    assert client.get("/v1/me", headers=h2).status_code == 401
    assert client.get("/v1/me", headers={"X-API-Key": r["api_key"]}).status_code == 200


def test_provedor_desativado(client, provider):
    pid, h = provider
    client.post(f"/v1/admin/providers/{pid}/deactivate", headers=ADMIN)
    assert client.get("/v1/me", headers=h).status_code == 401


def test_cnpj_duplicado(client, provider):
    r = client.post("/v1/admin/providers", json={"name": "Dup", "cnpj": "11.111.111/0001-11"}, headers=ADMIN)
    assert r.status_code == 409


def test_parametros_tributarios(client, provider):
    _, h = provider
    base = {"tax": "ICMS", "applies_to": "SCM", "rate": "18", "valid_from": "2026-01-01T00:00:00Z"}
    t = client.post("/v1/tax-parameters", json=base, headers=h)
    assert t.status_code == 201
    assert client.post("/v1/tax-parameters", json={**base, "valid_from": "2026-06-01T00:00:00Z"},
                       headers=h).status_code == 409                      # sobreposição
    tid = t.json()["id"]
    assert client.post(f"/v1/tax-parameters/{tid}/close", json={"valid_to": "2027-01-01T00:00:00Z"},
                       headers=h).status_code == 200
    assert client.post("/v1/tax-parameters", json={**base, "tax": "ICMS", "rate": "17.5",
                                                   "valid_from": "2027-01-01T00:00:00Z"},
                       headers=h).status_code == 201
    now = client.get("/v1/tax-parameters?at=2026-10-01T00:00:00Z", headers=h).json()
    assert [x["rate"] for x in now] == ["18.0000"]


def test_credenciais_erp_cifradas(client, provider):
    pid, h = provider
    body = {"base_url": "https://api.exemplo.hubsoft.com.br", "client_id": "cid", "client_secret": "sec",
            "username": "u", "password": "SENHA-SECRETA"}
    assert client.put("/v1/erp/credentials", json={**body, "base_url": "http://x.com"},
                      headers=h).status_code == 422
    r = client.put("/v1/erp/credentials", json=body, headers=h).json()
    assert r == {"configured": True, "erp": "hubsoft", "base_url": body["base_url"]}
    g = client.get("/v1/erp/credentials", headers=h).json()
    assert "password" not in str(g) and "SENHA" not in str(g)
    from app.models import ErpCredential
    from app.security import decrypt_json
    with SessionLocal() as db:
        c = db.query(ErpCredential).filter_by(provider_id=pid).one()
        assert b"SENHA-SECRETA" not in bytes(c.secret)
        assert decrypt_json(c.secret)["password"] == "SENHA-SECRETA"
    audit = client.get("/v1/audit", headers=h).json()
    assert "SENHA" not in str(audit)
    assert client.delete("/v1/erp/credentials", headers=h).json() == {"configured": False}


def test_relatorio_pdf(client, provider):
    _, h = provider
    seed_catalog(client, h)
    activate(client, h)
    split(client, h)
    split(client, h, ref="F-2", customer="C-2")
    r = client.get("/v1/reports/2026-10/evidence.pdf", headers=h)
    assert r.status_code == 200 and r.content.startswith(b"%PDF")
    rid = r.headers["X-Report-Id"]
    assert rid.startswith("CER-2026-10-")
    ev = client.get("/v1/audit?action=report.generated", headers=h).json()
    assert ev[0]["payload"]["report_id"] == rid and len(ev[0]["payload"]["sha256"]) == 64
    r2 = client.get("/v1/reports/2026-10/evidence.pdf", headers=h)
    assert r2.headers["X-Report-Id"] != rid
    assert client.get("/v1/reports/2026-10/summary", headers=h).json()["trilha"]["integra"]


def test_relatorio_pdf_vazio(client, provider):
    _, h = provider
    r = client.get("/v1/reports/2026-01/evidence.pdf", headers=h)
    assert r.status_code == 200 and r.content.startswith(b"%PDF")


def test_audit_head_e_paginacao(client, provider):
    _, h = provider
    seed_catalog(client, h)
    head = client.get("/v1/audit/head", headers=h).json()
    page = client.get("/v1/audit?limit=2", headers=h).json()
    assert page[0]["hash"] == head["hash"] and len(page) == 2
    older = client.get(f"/v1/audit?limit=100&before_id={page[-1]['id']}", headers=h).json()
    assert all(e["id"] < page[-1]["id"] for e in older) and len(older) == 4


def test_rate_limit():
    from app.security import RateLimiter
    rl = RateLimiter()
    assert all(rl.allow("k", 3) for _ in range(3)) and not rl.allow("k", 3)
    assert rl.allow("outra", 3)


def test_cors_so_para_os_aplicativos(client, provider):
    _, h = provider
    for origin in ("https://localhost", "capacitor://localhost", "app://cerne"):
        pre = client.options("/v1/me", headers={"Origin": origin, "Access-Control-Request-Method": "GET",
                                                "Access-Control-Request-Headers": "x-api-key"})
        assert pre.status_code == 200 and pre.headers["access-control-allow-origin"] == origin
        r = client.get("/v1/me", headers={**h, "Origin": origin})
        assert r.headers["access-control-allow-origin"] == origin
    r = client.get("/v1/me", headers={**h, "Origin": "https://site-malicioso.example"})
    assert "access-control-allow-origin" not in r.headers
