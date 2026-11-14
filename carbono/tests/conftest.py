import pytest


@pytest.fixture(autouse=True)
def _modo_demo_por_padrao(monkeypatch):
    """Os testes de regra de negócio usam o modo demonstração (cabeçalho X-Usuario-Id).
    Os testes de acesso em produção definem o modo explicitamente."""
    monkeypatch.setenv("CARBONO_MODO", "demo")
