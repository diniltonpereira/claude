"""Adaptador SOMENTE LEITURA para o Hubsoft.

ATENÇÃO: os caminhos abaixo são provisórios. Confirme cada um na documentação
atual (docs.hubsoft.com.br) ou testando com as credenciais do provedor-piloto.
A documentação pública pode estar defasada.
"""
from __future__ import annotations

import time

import httpx

# TODO(confirmar): ajustar conforme o ambiente de teste do piloto.
PATHS = {
    "token": "/oauth/token",
    "customers": "/api/v1/integracao/cliente",
    "contracts": "/api/v1/integracao/cliente",   # TODO: rota de serviços/contratos
    "invoices": "/api/v1/integracao/cliente/financeiro",  # TODO: rota de faturas
}


class HubsoftClient:
    def __init__(self, base_url: str, client_id: str, client_secret: str,
                 username: str, password: str, timeout: float = 30.0):
        self.base = base_url.rstrip("/")
        self._cred = dict(grant_type="password", client_id=client_id,
                          client_secret=client_secret, username=username, password=password)
        self._http = httpx.Client(timeout=timeout)
        self._token: str | None = None

    def _auth(self) -> dict:
        if not self._token:
            r = self._http.post(self.base + PATHS["token"], json=self._cred)
            r.raise_for_status()
            self._token = r.json()["access_token"]
        return {"Authorization": f"Bearer {self._token}"}

    def get(self, key: str, params: dict | None = None, retries: int = 3) -> dict:
        for attempt in range(retries + 1):
            r = self._http.get(self.base + PATHS[key], params=params, headers=self._auth())
            if r.status_code == 401 and attempt == 0:   # token expirado: renova uma vez
                self._token = None
                continue
            if (r.status_code == 429 or r.status_code >= 500) and attempt < retries:
                time.sleep(min(2 ** attempt * 2, 30))     # recuo exponencial: 2s, 4s, 8s
                continue
            break
        r.raise_for_status()
        return r.json()

    def close(self) -> None:
        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def paginate(self, key: str, params: dict | None = None, page_size: int = 100):
        """Itera páginas até esgotar. TODO(confirmar): nomes dos parâmetros de paginação."""
        page = 0
        while True:
            data = self.get(key, {**(params or {}), "pagina": page, "itens_por_pagina": page_size})
            items = data.get("clientes") or data.get("faturas") or data.get("data") or []
            if not items:
                return
            yield from items
            page += 1
