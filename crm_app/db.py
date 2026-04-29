"""Database engine + session.

Production: set `DATABASE_URL` (Postgres). Render's free Postgres
add-on provides this automatically — set it as an env var on the web
service and we'll use it.

Local dev / tests: leave `DATABASE_URL` unset and we fall back to a
SQLite file at `crm.db` (or `CRM_DB_PATH` if overridden). This keeps
`pytest` and `python run_etl.py` painless.

The rest of the codebase uses dialect-aware INSERT statements via
`crm_app.db.insert_on_conflict_do_nothing(...)` so the same code
works on both backends.
"""
import os
from typing import Any, Iterable

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import declarative_base, sessionmaker


def _build_url() -> str:
    """DATABASE_URL wins. Render's Postgres add-on prefixes with
    `postgres://` which SQLAlchemy 2.x rejects — normalize to
    `postgresql+psycopg2://`."""
    url = os.environ.get("DATABASE_URL")
    if url:
        if url.startswith("postgres://"):
            url = "postgresql+psycopg2://" + url[len("postgres://"):]
        elif url.startswith("postgresql://") and "+" not in url[: url.find("://")]:
            url = url.replace("postgresql://", "postgresql+psycopg2://", 1)
        return url
    # SQLite fallback — used by local dev and tests.
    db_path = os.environ.get(
        "CRM_DB_PATH",
        os.path.join(os.path.dirname(os.path.dirname(__file__)), "crm.db"),
    )
    return f"sqlite:///{db_path}"


DB_URL = _build_url()
_is_sqlite = DB_URL.startswith("sqlite")

# SQLite needs `check_same_thread=False` for FastAPI's threadpool;
# Postgres has its own connection pool and doesn't need it.
_connect_args = {"check_same_thread": False} if _is_sqlite else {}

engine: Engine = create_engine(
    DB_URL,
    future=True,
    connect_args=_connect_args,
    pool_pre_ping=not _is_sqlite,  # avoid stale Postgres conns
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ── dialect-agnostic INSERT … ON CONFLICT DO NOTHING ──────────────────────
# SQLite and Postgres both support upsert with the same semantics but
# via different dialect modules. Hide the difference behind one helper.
def insert_on_conflict_do_nothing(table, values: list[dict], conflict_cols: Iterable[str], returning=None):
    """Build an Insert statement with ON CONFLICT DO NOTHING that works
    on both SQLite and Postgres. `returning` is optional (SQLAlchemy
    column object). Returns the statement; caller does `db.execute(stmt)`.
    """
    if _is_sqlite:
        from sqlalchemy.dialects.sqlite import insert as _sqlite_insert
        stmt = _sqlite_insert(table).values(values).on_conflict_do_nothing(
            index_elements=list(conflict_cols)
        )
    else:
        from sqlalchemy.dialects.postgresql import insert as _pg_insert
        stmt = _pg_insert(table).values(values).on_conflict_do_nothing(
            index_elements=list(conflict_cols)
        )
    if returning is not None:
        stmt = stmt.returning(returning)
    return stmt


def is_sqlite() -> bool:
    """True iff the active engine is SQLite. Used by the migration
    runner — Postgres uses Alembic-style schema management instead of
    PRAGMA-driven ALTER TABLEs."""
    return _is_sqlite
