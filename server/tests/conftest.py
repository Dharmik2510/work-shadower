"""Test fixtures. Tests run against a REAL Postgres (default: local dev cluster, db workshadower_test).

Override with TEST_DATABASE_URL. The test database is dropped and recreated per session.
"""
from __future__ import annotations

import os
import uuid
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.jobs import WorkerContext
from app.main import create_app

TEST_DB_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://postgres@localhost:5432/workshadower_test")


def _admin_url(url: str) -> str:
    p = urlsplit(url)
    return urlunsplit((p.scheme, p.netloc, "/postgres", p.query, p.fragment))


def recreate_db(url: str) -> None:
    name = urlsplit(url).path.lstrip("/")
    with psycopg.connect(_admin_url(url), autocommit=True) as c:
        c.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        c.execute(f'CREATE DATABASE "{name}"')


def make_settings(tmp_path_factory, **overrides) -> Settings:
    base = dict(
        database_url=TEST_DB_URL,
        auth_mode="dev",
        admin_emails="admin@example.com",
        storage_driver="local",
        local_storage_dir=str(tmp_path_factory.mktemp("assets")),
        llm_provider="none",
        embed_provider="none",
        public_base_url="http://testserver",
        job_backoff_base_seconds=1.0,
        llm_daily_calls_per_user=50,
        log_level="WARNING",
        llm_retry_base_seconds=0.001,
        jev_retry_base_seconds=0.001,
    )
    base.update(overrides)
    return Settings(_env_file=None, **base)


@pytest.fixture(scope="session")
def settings(tmp_path_factory) -> Settings:
    recreate_db(TEST_DB_URL)
    return make_settings(tmp_path_factory)


@pytest.fixture(scope="session")
def app(settings):
    return create_app(settings, configure_logging=False)


@pytest.fixture(scope="session")
def client(app):
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def clean_db(client):
    with psycopg.connect(TEST_DB_URL, autocommit=True) as c:
        tables = [r[0] for r in c.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename <> 'schema_migrations'"
        ).fetchall()]
        c.execute("TRUNCATE " + ", ".join(tables) + " CASCADE")
        c.execute("INSERT INTO app_flags (id) VALUES (1)")
    yield


@pytest.fixture
def db():
    from psycopg.rows import dict_row

    with psycopg.connect(TEST_DB_URL, autocommit=True, row_factory=dict_row) as c:
        yield c


@pytest.fixture
def worker(app, client) -> WorkerContext:
    st = app.state
    return WorkerContext(pool=st.pool, settings=st.settings, llm=st.llm, embedder=st.embedder,
                         vector_enabled=st.vector_enabled, worker_id="test-worker")


def login(client: TestClient, email: str, name: str | None = None, team: str | None = None) -> dict:
    body = {"email": email, "name": name or email.split("@")[0].title()}
    if team:
        body["team"] = team
    r = client.post("/api/v1/auth/dev-login", json=body)
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def idem() -> dict:
    return {"Idempotency-Key": str(uuid.uuid4())}
