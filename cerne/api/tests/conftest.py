"""Ambiente de teste: SQLite temporário por padrão; Postgres se TEST_DATABASE_URL estiver definido."""
import os
import tempfile

from cryptography.fernet import Fernet

_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = os.getenv("TEST_DATABASE_URL", f"sqlite:///{_tmp}/test.db")
os.environ["ADMIN_TOKEN"] = "admin-teste"
os.environ["CERNE_MASTER_KEY"] = Fernet.generate_key().decode()
os.environ["AUTO_CREATE_SCHEMA"] = "1"
os.environ["RATE_LIMIT_PER_MIN"] = "0"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import Base, engine  # noqa: E402
from app.main import app  # noqa: E402

ADMIN = {"X-Admin-Token": "admin-teste"}


@pytest.fixture(autouse=True)
def clean_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield


@pytest.fixture
def client():
    return TestClient(app)


def new_provider(client, cnpj="11.111.111/0001-11", name="Provedor Teste"):
    r = client.post("/v1/admin/providers", json={"name": name, "cnpj": cnpj}, headers=ADMIN)
    assert r.status_code == 201, r.text
    return r.json()["provider_id"], {"X-API-Key": r.json()["api_key"]}


@pytest.fixture
def provider(client):
    return new_provider(client)
