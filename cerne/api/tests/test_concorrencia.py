"""Concorrência na trilha (só no Postgres): gravações paralelas não podem bifurcar a cadeia."""
import os
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.db import SessionLocal, is_postgres
from app.models import Provider
from app.services.audit import audit, verify

pytestmark = pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="requer Postgres")


def test_gravacoes_paralelas_mantem_cadeia():
    assert is_postgres()
    with SessionLocal() as db:
        p = Provider(name="P", cnpj="33.333.333/0001-33"); db.add(p); db.commit(); pid = p.id

    def work(i):
        with SessionLocal() as db:
            audit(db, pid, "teste", {"i": i})
            db.commit()

    with ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(work, range(80)))
    with SessionLocal() as db:
        v = verify(db, pid)
    assert v["integra"] and v["entradas"] == 80
