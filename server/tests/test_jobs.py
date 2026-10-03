from __future__ import annotations

import json

from app import jobs
from app.jobs import enqueue, run_once

from .conftest import idem, login
from .fixtures import recording_body

CALLS: list[dict] = []


@jobs.handler("test_flaky")
def _flaky(ctx, payload):
    CALLS.append(payload)
    if len([c for c in CALLS if c.get("tag") == payload.get("tag")]) <= payload.get("fail_times", 99):
        raise RuntimeError("boom")


def _job(db, job_id):
    return db.execute("SELECT * FROM jobs WHERE id = %s", (job_id,)).fetchone()


def _make_due(db, job_id):
    db.execute("UPDATE jobs SET run_after = now() - interval '1 second' WHERE id = %s", (job_id,))


def test_retry_backoff_then_success(worker, db):
    with worker.pool.connection() as c:
        jid = enqueue(c, "test_flaky", {"tag": "a", "fail_times": 2}, max_attempts=5)
    assert run_once(worker)
    j = _job(db, jid)
    assert j["status"] == "queued" and j["attempts"] == 1 and "boom" in j["last_error"]
    assert db.execute("SELECT run_after > now() AS later FROM jobs WHERE id = %s", (jid,)).fetchone()["later"]
    assert not run_once(worker)  # backoff: not due yet
    _make_due(db, jid)
    assert run_once(worker)
    j2 = _job(db, jid)
    assert j2["attempts"] == 2 and j2["run_after"] > j["run_after"]  # exponential: later each time
    _make_due(db, jid)
    assert run_once(worker)
    assert _job(db, jid)["status"] == "succeeded"


def test_dead_after_max_attempts_and_admin_retry(client, worker, db):
    with worker.pool.connection() as c:
        jid = enqueue(c, "test_flaky", {"tag": "b"}, max_attempts=3)
    for _ in range(3):
        _make_due(db, jid)
        assert run_once(worker)
    j = _job(db, jid)
    assert j["status"] == "dead" and j["attempts"] == 3
    admin = login(client, "admin@example.com")
    dead = client.get("/api/v1/admin/jobs?status=dead", headers=admin).json()["items"]
    assert [d["id"] for d in dead] == [jid] and dead[0]["last_error"].startswith("RuntimeError")
    assert client.get("/api/v1/admin/jobs?status=dead", headers=login(client, "m@example.com")).status_code == 403
    r = client.post(f"/api/v1/admin/jobs/{jid}/retry", headers=admin)
    assert r.status_code == 200 and r.json() == {"id": jid, "status": "queued"}
    j = _job(db, jid)
    assert j["status"] == "queued" and j["attempts"] == 0
    assert client.post(f"/api/v1/admin/jobs/{jid}/retry", headers=admin).status_code == 409


def test_visibility_timeout_reclaims_stuck_job(worker, db):
    with worker.pool.connection() as c:
        jid = enqueue(c, "test_flaky", {"tag": "c", "fail_times": 0}, max_attempts=3)
    # simulate a worker that claimed the job and crashed
    db.execute("UPDATE jobs SET status='running', attempts=1, locked_by='dead-worker', "
               "locked_at = now() - interval '1 hour' WHERE id = %s", (jid,))
    assert run_once(worker)
    j = _job(db, jid)
    assert j["status"] == "succeeded" and j["attempts"] == 2
    # a fresh lock is NOT reclaimed
    with worker.pool.connection() as c:
        jid2 = enqueue(c, "test_flaky", {"tag": "d", "fail_times": 0})
    db.execute("UPDATE jobs SET status='running', attempts=1, locked_by='busy', locked_at = now() WHERE id = %s", (jid2,))
    assert not run_once(worker)


def test_generate_skill_dead_marks_recording_failed(client, worker, db):
    h = login(client, "a@example.com")
    rec = client.post("/api/v1/recordings", headers={**h, **idem()}, json=recording_body()).json()
    # corrupt the stored event log so the handler fails every attempt
    db.execute("UPDATE recordings SET events = '\"not-a-list\"'::jsonb WHERE id = %s", (rec["id"],))
    db.execute("UPDATE jobs SET max_attempts = 2")
    jid = db.execute("SELECT id::text AS id FROM jobs").fetchone()["id"]
    for _ in range(2):
        _make_due(db, jid)
        run_once(worker)
    assert _job(db, jid)["status"] == "dead"
    r = client.get(f"/api/v1/recordings/{rec['id']}", headers=h).json()
    assert r["status"] == "failed" and r["error"]
    # fix data, admin retries -> ready
    db.execute("UPDATE recordings SET events = %s::jsonb WHERE id = %s",
               (json.dumps(recording_body()["events"]), rec["id"]))
    admin = login(client, "admin@example.com")
    client.post(f"/api/v1/admin/jobs/{jid}/retry", headers=admin)
    assert client.get(f"/api/v1/recordings/{rec['id']}", headers=h).json()["status"] == "received"
    run_once(worker)
    assert client.get(f"/api/v1/recordings/{rec['id']}", headers=h).json()["status"] == "ready"
