"""Job de sincronização contra um Hubsoft falso (httpx.MockTransport)."""
import httpx

from app import sync
from app.db import SessionLocal
from app.integrations.hubsoft import HubsoftClient
from app.models import InvoiceSplit
from conftest import new_provider

FATURAS = [
    {"id_fatura": 1, "id_cliente": "C-1", "nome": "Fulano de Tal", "cpf": "000.000.000-00",
     "itens": [{"id_servico": 101, "valor": "99,90", "descricao": "Internet"},
               {"id_servico": 205, "valor": "9,90", "descricao": "Antivírus"}]},
    {"id_fatura": 2, "id_cliente": "C-2", "itens": [{"id_servico": 999, "valor": "5,00"}]},
    {"id_fatura": 3, "itens": []},                                   # inválida: sem cliente
]


def fake_hub():
    def handler(req: httpx.Request):
        if req.url.path == "/oauth/token":
            return httpx.Response(200, json={"access_token": "t"})
        page = int(req.url.params["pagina"])
        return httpx.Response(200, json={"faturas": FATURAS if page == 0 else []})
    h = HubsoftClient("https://hub.test", "a", "b", "c", "d")
    h._http = httpx.Client(transport=httpx.MockTransport(handler))
    return h


def test_sync_separa_e_e_idempotente(client):
    pid, h = new_provider(client)
    client.post("/v1/services", json={"code": "INTERNET", "name": "Internet", "kind": "SCM",
                                      "criteria": "Conexão à internet, serviço de telecomunicações."}, headers=h)
    client.post("/v1/services/INTERNET/validate", json={"validated_by": "Contadora CRC 1"}, headers=h)
    smap = {"101": "INTERNET", "205": "ANTIVIRUS"}
    with SessionLocal() as db:
        r = sync.sync_provider(db, fake_hub(), pid, "2026-10", sync.mapping.DEFAULT_FIELD_MAP, smap)
    assert (r["lidas"], r["separadas"], r["erros"], r["com_pendencia"]) == (3, 2, 1, 2)
    assert r["codigos_sem_mapeamento"] == ["999"]
    with SessionLocal() as db:
        r2 = sync.sync_provider(db, fake_hub(), pid, "2026-10", sync.mapping.DEFAULT_FIELD_MAP, smap)
        assert r2["ja_processadas"] == 2 and r2["separadas"] == 0
        rows = db.query(InvoiceSplit).all()
        assert "Fulano" not in str([x.detail for x in rows]) and "000.000.000" not in str([x.detail for x in rows])
    assert client.get("/v1/audit/verify", headers=h).json()["integra"]


def test_dry_run_nao_grava(client):
    pid, _ = new_provider(client)
    with SessionLocal() as db:
        r = sync.sync_provider(db, fake_hub(), pid, "2026-10", sync.mapping.DEFAULT_FIELD_MAP, {}, dry_run=True)
        assert r["lidas"] == 3 and db.query(InvoiceSplit).count() == 0


def test_previous_period_e_params():
    from datetime import date
    assert sync.previous_period(date(2026, 1, 15)) == "2025-12"
    assert sync.previous_period(date(2026, 10, 1)) == "2026-09"
    assert sync.period_params("2028-02") == {"data_inicio": "2028-02-01", "data_fim": "2028-02-29"}
