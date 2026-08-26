"""Environment-driven PostgreSQL configuration, engine, session factory, and
initialization (Step 4).

No credentials are hardcoded anywhere - every value comes from the process
environment, loaded from a local .env file via python-dotenv if one exists
(see .env.example for the variables this expects, and docker-compose.yml
for the container that provides them in local dev).
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.database.models import Base

load_dotenv()

_REQUIRED_VARS = (
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "POSTGRES_DB",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
)


@dataclass(frozen=True)
class DatabaseConfig:
    host: str
    port: int
    database: str
    user: str
    password: str

    @property
    def url(self) -> str:
        return (
            f"postgresql+psycopg://{self.user}:{self.password}"
            f"@{self.host}:{self.port}/{self.database}"
        )


def load_database_config() -> DatabaseConfig:
    missing = [name for name in _REQUIRED_VARS if not os.environ.get(name)]
    if missing:
        raise RuntimeError(
            "Missing required PostgreSQL environment variable(s): "
            f"{', '.join(missing)}.\n"
            "Copy .env.example to .env and fill in real values, then start "
            "the database with `docker compose up -d` (or set DATABASE_URL "
            "for a managed/cloud instance instead)."
        )

    return DatabaseConfig(
        host=os.environ["POSTGRES_HOST"],
        port=int(os.environ["POSTGRES_PORT"]),
        database=os.environ["POSTGRES_DB"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
    )


def _normalize_database_url(raw: str) -> str:
    """Managed providers (Neon, Supabase, ...) hand you a ready-to-use
    postgresql://... URL, already carrying whatever *they* require in its
    query string (Neon: sslmode=require&channel_binding=require) - passed
    through unchanged here. The one thing every one of them omits is the
    driver name: SQLAlchemy defaults a bare "postgresql://" scheme to
    psycopg2, which isn't installed (requirements.txt has psycopg[binary],
    i.e. psycopg3) - so the scheme is rewritten to name it explicitly.
    """
    for prefix in ("postgresql://", "postgres://"):
        if raw.startswith(prefix):
            return "postgresql+psycopg://" + raw[len(prefix):]
    return raw


def resolve_database_url() -> str:
    """DATABASE_URL, if set, is used as-is (aside from the driver-name fix
    above) - this is the path for a managed provider's own connection
    string. Otherwise built from the individual POSTGRES_* vars, for
    local/self-hosted Postgres (docker-compose.yml).
    """
    raw = os.environ.get("DATABASE_URL")
    if raw:
        return _normalize_database_url(raw)
    return load_database_config().url


engine = create_engine(resolve_database_url(), future=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)


def init_db(*, drop_existing: bool = False) -> None:
    """Create all tables. Pass drop_existing=True to reset the schema first."""
    if drop_existing:
        Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


@contextmanager
def get_session() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
