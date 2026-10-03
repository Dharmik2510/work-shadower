"""Connection pool, request-scoped connections, and the SQL migration runner."""
from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import psycopg
from fastapi import Request
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .config import Settings

log = logging.getLogger("app.db")

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"
_MIGRATION_LOCK_ID = 727_001_337  # arbitrary, constant advisory-lock key


def create_pool(settings: Settings) -> ConnectionPool:
    pool = ConnectionPool(
        settings.database_url,
        min_size=settings.db_pool_min,
        max_size=settings.db_pool_max,
        kwargs={"row_factory": dict_row, "autocommit": False},
        open=False,
        name="ws",
    )
    pool.open(wait=True, timeout=30)
    return pool


def run_migrations(database_url: str, disable_vector: bool = False) -> list[str]:
    """Apply pending migrations in order. Safe with concurrent replicas (advisory lock)."""
    applied: list[str] = []
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute("SELECT pg_advisory_lock(%s)", (_MIGRATION_LOCK_ID,))
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                " version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
            )
            done = {r[0] for r in conn.execute("SELECT version FROM schema_migrations").fetchall()}
            for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
                version = path.stem
                if version in done:
                    continue
                sql = path.read_text()
                with conn.transaction():
                    conn.execute("SELECT set_config('ws.disable_vector', %s, true)", ("on" if disable_vector else "off",))
                    conn.execute(sql)
                    conn.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (version,))
                applied.append(version)
                log.info("applied migration", extra={"version": version})
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (_MIGRATION_LOCK_ID,))
    return applied


def vector_available(pool: ConnectionPool) -> bool:
    with pool.connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM information_schema.columns WHERE table_name='skills' AND column_name='embedding'"
        ).fetchone()
    return row is not None


def get_conn(request: Request) -> Iterator[psycopg.Connection]:
    """FastAPI dependency: one pooled connection per request, committed on success."""
    pool: ConnectionPool = request.app.state.pool
    with pool.connection() as conn:  # commits on clean exit, rolls back on exception
        yield conn


@contextmanager
def connection(pool: ConnectionPool) -> Iterator[psycopg.Connection]:
    with pool.connection() as conn:
        yield conn
