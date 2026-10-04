from __future__ import annotations

import hashlib
from urllib.parse import urlsplit

from .conftest import idem, login
from .fixtures import recording_body

PNG = b"\x89PNG\r\n\x1a\n" + b"fake-screenshot-bytes" * 20
PNG_SHA = hashlib.sha256(PNG).hexdigest()


def _upload(client, headers, data=PNG, sha=PNG_SHA):
    r = client.post("/api/v1/assets/presign", headers=headers,
                    json={"sha256": sha, "content_type": "image/png", "bytes": len(data)})
    assert r.status_code == 200, r.text
    body = r.json()
    if body["upload"]:
        up = body["upload"]
        assert up["method"] == "PUT"
        u = urlsplit(up["url"])
        r2 = client.put(f"{u.path}?{u.query}", content=data, headers=up["headers"])  # no bearer, like S3
        assert r2.status_code == 200, r2.text
    return body


# ------------------------------------------------------------------ auth
def test_dev_login_and_me(client):
    h = login(client, "Alice@Example.com", "Alice", team="UBI")
    r = client.get("/api/v1/me", headers=h)
    assert r.status_code == 200
    me = r.json()
    assert me["email"] == "alice@example.com"
    assert me["name"] == "Alice"
    assert me["role"] == "member"
    assert [t["name"] for t in me["teams"]] == ["UBI"]
    assert set(me) == {"id", "email", "name", "role", "teams"}

    admin = login(client, "admin@example.com", "Admin")
    assert client.get("/api/v1/me", headers=admin).json()["role"] == "admin"

    teams = client.get("/api/v1/teams", headers=h).json()["items"]
    assert [t["name"] for t in teams] == ["UBI"]


def test_auth_errors_and_public_config(client):
    r = client.get("/api/v1/me")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"
    r = client.get("/api/v1/me", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "invalid_token"
    pub = client.get("/api/v1/config/public")
    assert pub.status_code == 200
    assert pub.json() == {"auth_mode": "dev", "oidc": None}
    assert client.get("/api/v1/nope").json()["error"]["code"] == "not_found"
    h = login(client, "x@example.com")
    cfg = client.get("/api/v1/config", headers=h).json()
    assert cfg == {"recording_enabled": True, "replay_enabled": True, "llm_enabled": False,
                   "max_recording_minutes": 30, "screenshot_policy": "key_moments",
                   "filter_enabled": True, "filter_drop_threshold": 0.9, "filter_review_threshold": 0.6,
                   "split_tasks_enabled": True}


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "db": True, "storage": True}
    assert r.headers.get("x-request-id")


# ------------------------------------------------------------------ assets
def test_presign_upload_and_dedup(client):
    h = login(client, "a@example.com")
    first = _upload(client, h)
    assert first["exists"] is False and first["upload"]["url"].startswith("http://testserver/api/v1/assets/upload/")
    again = client.post("/api/v1/assets/presign", headers=h,
                        json={"sha256": PNG_SHA, "content_type": "image/png", "bytes": len(PNG)}).json()
    assert again == {"asset_id": first["asset_id"], "exists": True, "upload": None}
    # uploader can read it back
    r = client.get(f"/api/v1/assets/{PNG_SHA}", headers=h)
    assert r.status_code == 200 and r.content == PNG and r.headers["content-type"] == "image/png"


def test_asset_validation(client):
    h = login(client, "a@example.com")
    r = client.post("/api/v1/assets/presign", headers=h,
                    json={"sha256": "a" * 64, "content_type": "image/png", "bytes": 3 * 1024 * 1024})
    assert r.status_code == 413 and r.json()["error"]["code"] == "asset_too_large"
    r = client.post("/api/v1/assets/presign", headers=h,
                    json={"sha256": "a" * 64, "content_type": "image/gif", "bytes": 10})
    assert r.status_code == 422
    # sha mismatch on upload
    body = client.post("/api/v1/assets/presign", headers=h,
                       json={"sha256": "b" * 64, "content_type": "image/png", "bytes": 10}).json()
    u = urlsplit(body["upload"]["url"])
    r = client.put(f"{u.path}?{u.query}", content=b"0123456789")
    assert r.status_code == 422 and r.json()["error"]["code"] == "sha256_mismatch"
    # tampered signature
    r = client.put(f"{u.path}?expires=99999999999&sig=deadbeef", content=b"x")
    assert r.status_code == 403


# ------------------------------------------------------------------ recordings
def test_recording_idempotency(client, db):
    h = login(client, "a@example.com")
    key = idem()
    r1 = client.post("/api/v1/recordings", headers={**h, **key}, json=recording_body())
    assert r1.status_code == 202 and r1.json()["status"] == "received"
    r2 = client.post("/api/v1/recordings", headers={**h, **key}, json=recording_body())
    assert r2.status_code == 200 and r2.json()["id"] == r1.json()["id"]
    assert db.execute("SELECT count(*) AS n FROM recordings").fetchone()["n"] == 1
    assert db.execute("SELECT count(*) AS n FROM jobs").fetchone()["n"] == 1
    # same key, different user -> separate resource
    h2 = login(client, "b@example.com")
    r3 = client.post("/api/v1/recordings", headers={**h2, **key}, json=recording_body())
    assert r3.status_code == 202 and r3.json()["id"] != r1.json()["id"]
    # missing key
    r4 = client.post("/api/v1/recordings", headers=h, json=recording_body())
    assert r4.status_code == 400 and r4.json()["error"]["code"] == "idempotency_key_required"
    # list + get
    lst = client.get("/api/v1/recordings", headers=h).json()
    assert [i["id"] for i in lst["items"]] == [r1.json()["id"]] and lst["next_cursor"] is None
    one = client.get(f"/api/v1/recordings/{r1.json()['id']}", headers=h).json()
    assert one["status"] == "received" and one["event_count"] == 26 and one["skill_id"] is None
    # another user cannot see it
    assert client.get(f"/api/v1/recordings/{r1.json()['id']}", headers=h2).status_code == 404


def test_recording_limits(client):
    h = login(client, "a@example.com")
    body = recording_body()
    ev = body["events"][0]
    body["events"] = [{**ev, "seq": i} for i in range(5001)]
    r = client.post("/api/v1/recordings", headers={**h, **idem()}, json=body)
    assert r.status_code == 413 and r.json()["error"]["code"] == "too_many_events"


def test_pagination(client):
    h = login(client, "a@example.com")
    ids = [client.post("/api/v1/recordings", headers={**h, **idem()}, json=recording_body()).json()["id"]
           for _ in range(5)]
    seen, cursor = [], None
    while True:
        url = "/api/v1/recordings?limit=2" + (f"&cursor={cursor}" if cursor else "")
        page = client.get(url, headers=h).json()
        assert len(page["items"]) <= 2
        seen += [i["id"] for i in page["items"]]
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert sorted(seen) == sorted(ids) and len(seen) == 5
    assert client.get("/api/v1/recordings?cursor=garbage", headers=h).json()["error"]["code"] == "invalid_cursor"
