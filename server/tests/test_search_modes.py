"""Hybrid search with pgvector (fake deterministic embedder) and the full-text-only fallback."""
from __future__ import annotations

import hashlib
import math

import psycopg
from psycopg.rows import dict_row

from app.auth import CurrentUser, upsert_user
from app.db import run_migrations
from app.jobs import run_until_empty
from app.llm import EMBED_DIM, Embedder
from app.skills import create_skill, search, publish

from .conftest import TEST_DB_URL, login, recreate_db
from .test_skills_flow import _content, _publish_new


class FakeEmbedder(Embedder):
    """Bag-of-words hashed into 1536 dims: texts sharing words are close."""
    name = "fake"
    model = "fake"

    @property
    def enabled(self):
        return True

    def embed(self, texts):
        out = []
        for t in texts:
            v = [0.0] * EMBED_DIM
            for w in t.lower().split():
                v[int(hashlib.md5(w.strip(".,:").encode()).hexdigest(), 16) % EMBED_DIM] += 1.0
            n = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append([x / n for x in v])
        return out


def test_vector_hybrid_search(client, app, worker, db):
    assert app.state.vector_enabled, "pgvector expected in the dev cluster"
    fake = FakeEmbedder()
    old = app.state.embedder
    app.state.embedder = worker.embedder = fake
    try:
        h = login(client, "v@example.com")
        a = _publish_new(client, h, {**_content("Rotate the shared drive credentials"), "goal": "quarterly rotation"})
        b = _publish_new(client, h, _content("Book a meeting room"))
        assert db.execute("SELECT count(*) AS n FROM jobs WHERE kind = 'embed_skill'").fetchone()["n"] == 2
        run_until_empty(worker)
        assert db.execute("SELECT count(*) AS n FROM skills WHERE embedding IS NOT NULL").fetchone()["n"] == 2
        # 'quarterly' only matches through the goal text; 'meeting' only through the title
        items = client.get("/api/v1/search?q=quarterly rotation", headers=h).json()["items"]
        assert items[0]["id"] == a
        ids = [i["id"] for i in client.get("/api/v1/search?q=meeting", headers=h).json()["items"]]
        assert ids[0] == b
    finally:
        app.state.embedder = worker.embedder = old


def test_full_text_only_when_pgvector_disabled():
    url = TEST_DB_URL + "_novec"
    recreate_db(url)
    run_migrations(url, disable_vector=True)
    with psycopg.connect(url, row_factory=dict_row) as conn:
        col = conn.execute("SELECT 1 FROM information_schema.columns "
                           "WHERE table_name='skills' AND column_name='embedding'").fetchone()
        assert col is None
        uid = upsert_user(conn, "n@example.com", "N", None)
        user = CurrentUser(uid, "n@example.com", "N", "member", [])
        sid = create_skill(conn, owner_id=uid, content=_content("Close a ticket in ServiceNow"),
                           team_id=None, visibility="org")
        row = conn.execute("SELECT s.*, NULL::jsonb AS published FROM skills s WHERE id = %s", (sid,)).fetchone()
        publish(conn, row, user, embeddings=False)
        res = search(conn, user, "servicenow ticket", 10, None)
        assert [r["id"] for r in res] == [str(sid)] and res[0]["score"] > 0
    with psycopg.connect(TEST_DB_URL.rsplit("/", 1)[0] + "/postgres", autocommit=True) as c:
        c.execute(f'DROP DATABASE IF EXISTS "{url.rsplit("/", 1)[1]}" WITH (FORCE)')
