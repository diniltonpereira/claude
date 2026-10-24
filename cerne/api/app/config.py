"""Configuração por variáveis de ambiente (12-factor). Nenhum segredo tem valor padrão."""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache


def _bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    return default if v is None else v.strip().lower() in ("1", "true", "sim", "yes", "on")


@dataclass(frozen=True)
class Settings:
    database_url: str
    admin_token: str
    master_key: str               # chave Fernet para cifrar credenciais de ERP
    rate_limit_per_min: int
    auto_create_schema: bool      # só para desenvolvimento/testes; produção usa Alembic
    docs_enabled: bool
    log_level: str


@lru_cache
def get_settings() -> Settings:
    url = os.getenv("DATABASE_URL", "sqlite:///./cerne.db")
    return Settings(
        database_url=url,
        admin_token=os.getenv("ADMIN_TOKEN", ""),
        master_key=os.getenv("CERNE_MASTER_KEY", ""),
        rate_limit_per_min=int(os.getenv("RATE_LIMIT_PER_MIN", "240")),
        auto_create_schema=_bool("AUTO_CREATE_SCHEMA", url.startswith("sqlite")),
        docs_enabled=_bool("DOCS_ENABLED", True),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
    )
