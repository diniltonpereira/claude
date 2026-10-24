from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import get_settings

_url = get_settings().database_url
engine = create_engine(
    _url, future=True, pool_pre_ping=True,
    connect_args={"check_same_thread": False} if _url.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def is_postgres() -> bool:
    return engine.dialect.name == "postgresql"


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
