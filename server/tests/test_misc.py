from __future__ import annotations

import threading

import psycopg
from fastapi.testclient import TestClient

from app.db import run_migrations
from app.main import create_app
from app.redact import redact_event, redact_text

from .conftest import TEST_DB_URL, make_settings, recreate_db


def test_redaction_rules():
    assert redact_text("mail a.b+c@x.co now") == "mail [REDACTED:email] now"
    assert redact_text("4111-1111-1111-1111") == "[REDACTED:card]"
    assert redact_text("SIN 046 454 286") == "SIN [REDACTED:sin]"
    assert redact_text("(416) 555-0199") == "[REDACTED:phone]"
    assert redact_text("Policy P-123456, claim 12-3") == "Policy P-123456, claim 12-3"
    sha = "ab1234567890123cd" + "e" * 47
    assert redact_text(sha) == sha  # digit runs inside hex are not touched
    ev = redact_event({"type": "type", "text": "s3cret", "element": {"value_kind": "secure", "label": "Password"},
                       "url": "https://x.example.com/p?q=jane@x.com#frag", "window": {"title": "Inbox – jane@x.com"}})
    assert ev["text"] is None and ev["url"] == "https://x.example.com/p" and ev["window"]["title"] == "Inbox – [REDACTED:email]"


def test_spa_served_with_fallback(tmp_path, tmp_path_factory, client):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<html>spa</html>")
    (tmp_path / "assets" / "app.js").write_text("console.log(1)")
    s = make_settings(tmp_path_factory, web_dist_dir=str(tmp_path), cors_origins="http://localhost:5173")
    with TestClient(create_app(s, configure_logging=False)) as c:
        assert c.get("/").text == "<html>spa</html>"
        assert c.get("/skills/123/edit").text == "<html>spa</html>"
        r = c.get("/assets/app.js")
        assert r.text == "console.log(1)" and "immutable" in r.headers["cache-control"]
        assert c.get("/../../etc/passwd").text == "<html>spa</html>"
        r = c.get("/api/v1/does-not-exist")
        assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
        assert c.get("/healthz").json()["ok"] is True
        pre = c.options("/api/v1/me", headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"})
        assert pre.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_concurrent_migrations_are_safe():
    url = TEST_DB_URL + "_mig"
    recreate_db(url)
    results, errors = [], []

    def go():
        try:
            results.append(run_migrations(url))
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=go) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors
    assert sorted(len(r) for r in results) == [0, 0, 0, 3]  # exactly one runner applied them
    assert run_migrations(url) == []
    with psycopg.connect(TEST_DB_URL.rsplit("/", 1)[0] + "/postgres", autocommit=True) as c:
        c.execute(f'DROP DATABASE IF EXISTS "{url.rsplit("/", 1)[1]}" WITH (FORCE)')
