"""End to end: recording with intent -> filter (fake Jev) -> draft with excluded steps -> publish strips
them and records reviewer feedback -> admin stats/export. Plus task splitting, kill switch, batch mode."""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import timedelta

import httpx

from app.filtering import JevClient, RelevanceFilter
from app.jobs import WorkerContext, run_once, run_until_empty
from app.llm import AnthropicProvider

from .conftest import idem, login, make_settings
from .fixtures import T0
from .test_filtering import fake_jev, labelled_answers, scenario


def body_for(sc: dict, intent: str | None = "use scenario", title_hint="Workflow in Google Chrome") -> dict:
    return {"title_hint": title_hint, "intent": sc["intent"] if intent == "use scenario" else intent,
            "started_at": T0.isoformat(), "ended_at": (T0 + timedelta(minutes=10)).isoformat(),
            "client": {"app_version": "1.1.0"}, "events": sc["events"]}


def jev_worker(worker: WorkerContext, sc: dict, tmp_path_factory, new_task_at=frozenset(), **kw) -> WorkerContext:
    s = make_settings(tmp_path_factory, filter_provider="jev", jev_api_key="k", **kw)
    jev = JevClient(s, fake_jev(labelled_answers(sc, set(new_task_at))), sleep=lambda x: None)
    return replace(worker, filter=RelevanceFilter(s, jev))


def _record(client, h, body) -> str:
    r = client.post("/api/v1/recordings", headers={**h, **idem()}, json=body)
    assert r.status_code == 202, r.text
    return r.json()["id"]


def test_filtered_draft_publish_and_feedback(client, worker, db, tmp_path_factory):
    sc = scenario("slack_detour")
    h = login(client, "author@example.com", team="Auto")
    rid = _record(client, h, body_for(sc, intent="  Added a driver to an auto policy\n in PolicyCenter  "))
    w = jev_worker(worker, sc, tmp_path_factory)
    assert run_until_empty(w) == 1

    rec = client.get(f"/api/v1/recordings/{rid}", headers=h).json()
    assert rec["status"] == "ready" and rec["intent"] == "Added a driver to an auto policy in PolicyCenter"
    assert rec["filter"]["source"] == "jev" and rec["filter"]["counts"]["drop"] == 3
    assert rec["skill_ids"] == [rec["skill_id"]]

    sk = client.get(f"/api/v1/skills/{rec['skill_id']}", headers=h).json()
    draft = sk["draft"]
    assert draft["title"] == "Added a driver to an auto policy in PolicyCenter"
    excluded = [s for s in draft["steps"] if s["excluded"]]
    assert len(excluded) == 3 and {s["filter"]["reason"] for s in excluded} == {"detour"}

    # reviewer restores the Slack click, then publishes
    for s in draft["steps"]:
        if 7 in s["source_seqs"]:
            s["excluded"] = False
    r = client.patch(f"/api/v1/skills/{sk['id']}", headers=h, json={"content": draft})
    assert r.status_code == 200
    r = client.post(f"/api/v1/skills/{sk['id']}/publish", headers=h)
    assert r.status_code == 200, r.text
    pub = r.json()["published"]
    assert len(pub["steps"]) == len(draft["steps"]) - 2
    assert all(not s["excluded"] and s["filter"] is None for s in pub["steps"])

    rows = db.execute("SELECT seq, decision, final_keep FROM filter_decisions WHERE recording_id = %s ORDER BY seq",
                      (rid,)).fetchall()
    fk = {r["seq"]: r["final_keep"] for r in rows}
    assert fk[7] is True and fk[6] is False and fk[8] is False and fk[4] is True

    admin = login(client, "admin@example.com")
    st = client.get("/api/v1/admin/filter/stats", headers=admin).json()
    assert st["events"] == len(rows) and st["reviewed"] == len(rows)
    assert st["decisions"]["drop"] == 3 and st["wrongly_dropped_rate"] == round(1 / 3, 4)
    assert st["jev"]["recordings"] == 1 and st["jev"]["est_cost_usd"] > 0
    assert any(c["threshold"] == 0.9 and c["precision"] == 0.6667 for c in st["threshold_curve"])
    ex = client.get("/api/v1/admin/filter/export", headers=admin)
    assert ex.status_code == 200 and ex.headers["content-type"].startswith("application/x-ndjson")
    lines = [json.loads(x) for x in ex.text.splitlines()]
    assert len(lines) == len(rows) and lines[0]["goal"].startswith("Added a driver")
    assert client.get("/api/v1/admin/filter/stats", headers=h).status_code == 403

    # filter usage does not consume the per-user LLM budget
    n = db.execute("SELECT count(*) AS n FROM llm_usage WHERE purpose = 'filter'").fetchone()["n"]
    assert n == 1


def test_publish_rejects_all_excluded(client, worker, db, tmp_path_factory):
    sc = scenario("undo_mistake")
    h = login(client, "a2@example.com")
    rid = _record(client, h, body_for(sc))
    run_until_empty(worker)
    sid = client.get(f"/api/v1/recordings/{rid}", headers=h).json()["skill_id"]
    draft = client.get(f"/api/v1/skills/{sid}", headers=h).json()["draft"]
    assert sum(s["excluded"] for s in draft["steps"]) == 2  # local rules: the undo pair
    for s in draft["steps"]:
        s["excluded"] = True
    client.patch(f"/api/v1/skills/{sid}", headers=h, json={"content": draft})
    r = client.post(f"/api/v1/skills/{sid}/publish", headers=h)
    assert r.status_code == 422 and "excluded" in r.json()["error"]["message"]


def test_two_tasks_become_two_drafts(client, worker, db):
    sc = scenario("two_tasks")
    h = login(client, "split@example.com")
    rid = _record(client, h, body_for(sc, intent=None, title_hint="Workflow in Microsoft Excel, Microsoft Outlook"))
    run_until_empty(worker)
    rec = client.get(f"/api/v1/recordings/{rid}", headers=h).json()
    assert len(rec["skill_ids"]) == 2 and rec["skill_id"] == rec["skill_ids"][0]
    titles = [client.get(f"/api/v1/skills/{s}", headers=h).json()["draft"] for s in rec["skill_ids"]]
    assert titles[0]["apps"] == ["Microsoft Excel"] and titles[1]["apps"] == ["Microsoft Outlook"]
    segs = db.execute("SELECT DISTINCT segment, skill_id::text AS sid FROM filter_decisions WHERE recording_id = %s "
                      "ORDER BY segment", (rid,)).fetchall()
    assert [r["sid"] for r in segs] == rec["skill_ids"]


def test_filter_kill_switch_and_split_flag(client, worker, db):
    admin = login(client, "admin@example.com")
    r = client.put("/api/v1/admin/flags", headers=admin, json={"filter_enabled": False, "split_tasks_enabled": False})
    assert r.json()["filter_enabled"] is False
    r = client.put("/api/v1/admin/flags", headers=admin, json={"filter_review_threshold": 0.95})
    assert r.status_code == 422  # review must be <= drop
    h = login(client, "k@example.com")
    rid = _record(client, h, body_for(scenario("undo_mistake")))
    run_until_empty(worker)
    sid = client.get(f"/api/v1/recordings/{rid}", headers=h).json()["skill_id"]
    draft = client.get(f"/api/v1/skills/{sid}", headers=h).json()["draft"]
    assert not any(s["excluded"] for s in draft["steps"])
    assert {s["filter"]["decision"] for s in draft["steps"]} == {"keep"}


def test_filter_crash_never_blocks(client, worker, db):
    class Boom(RelevanceFilter):
        def run(self, *a, **k):
            raise RuntimeError("kaboom")
    h = login(client, "crash@example.com")
    rid = _record(client, h, body_for(scenario("slack_detour")))
    run_until_empty(replace(worker, filter=Boom(worker.settings)))
    rec = client.get(f"/api/v1/recordings/{rid}", headers=h).json()
    assert rec["status"] == "ready" and rec["filter"]["source"] == "none"


def test_retry_reuses_stored_decisions(client, worker, db, tmp_path_factory):
    sc = scenario("slack_detour")
    h = login(client, "retry@example.com")
    rid = _record(client, h, body_for(sc))
    calls = []

    class Counting(RelevanceFilter):
        def run(self, *a, **k):
            calls.append(1)
            return super().run(*a, **k)

    class FailingCreate(Exception):
        pass

    import app.jobs as jobs_mod
    orig = jobs_mod._create_skills

    def explode(*a, **k):
        raise FailingCreate("db hiccup")
    jobs_mod._create_skills = explode
    try:
        run_once(replace(worker, filter=Counting(worker.settings)))
    finally:
        jobs_mod._create_skills = orig
    db.execute("UPDATE jobs SET run_after = now()")
    run_until_empty(replace(worker, filter=Counting(worker.settings)))
    assert len(calls) == 1  # second attempt reused the persisted decisions
    assert client.get(f"/api/v1/recordings/{rid}", headers=h).json()["status"] == "ready"


def test_batch_mode_end_to_end(client, worker, db, tmp_path_factory):
    s = make_settings(tmp_path_factory, llm_provider="anthropic", llm_api_key="sk", llm_batch_mode=True,
                      llm_batch_poll_seconds=0)
    state = {"polls": 0, "submitted": None}
    tool_input = {"title": "Add a driver", "goal": "Add a driver to a policy", "apps": ["Google Chrome"],
                  "tags": ["policy"], "steps": [
                      {"title": "Submit", "instruction": "Click Submit", "app": "Google Chrome",
                       "action": {"type": "click", "target": {"label": "Submit"}}, "irreversible": True,
                       "source_seqs": [14], "exclude": False}]}

    def handle(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/v1/messages/batches") and request.method == "POST":
            state["submitted"] = json.loads(request.content)
            return httpx.Response(200, json={"id": "msgbatch_1", "processing_status": "in_progress"})
        if url.endswith("/v1/messages/batches/msgbatch_1"):
            state["polls"] += 1
            if state["polls"] < 2:
                return httpx.Response(200, json={"id": "msgbatch_1", "processing_status": "in_progress"})
            return httpx.Response(200, json={"id": "msgbatch_1", "processing_status": "ended",
                                             "results_url": "https://api.anthropic.com/v1/messages/batches/msgbatch_1/results"})
        if url.endswith("/results"):
            line = {"custom_id": "seg-0", "result": {"type": "succeeded", "message": {
                "content": [{"type": "tool_use", "name": "emit_result", "input": tool_input}],
                "usage": {"input_tokens": 1000, "output_tokens": 100}}}}
            return httpx.Response(200, text=json.dumps(line) + "\n")
        return httpx.Response(404)

    llm = AnthropicProvider(s, httpx.MockTransport(handle))
    w = replace(worker, settings=s, llm=llm, filter=RelevanceFilter(s))
    h = login(client, "batch@example.com")
    rid = _record(client, h, body_for(scenario("slack_detour")))
    assert run_once(w)  # generate_skill -> batch submitted
    assert state["submitted"]["requests"][0]["custom_id"] == "seg-0"
    assert state["submitted"]["requests"][0]["params"]["tool_choice"]["name"] == "emit_result"
    rec = db.execute("SELECT status, llm_batch_id FROM recordings WHERE id = %s", (rid,)).fetchone()
    assert rec["llm_batch_id"] == "msgbatch_1" and rec["status"] == "processing"
    assert run_once(w)  # poll: still in progress -> rescheduled without using an attempt
    job = db.execute("SELECT status, attempts FROM jobs WHERE kind = 'poll_llm_batch'").fetchone()
    assert job["status"] == "queued" and job["attempts"] == 0
    run_until_empty(w)
    rec = client.get(f"/api/v1/recordings/{rid}", headers=h).json()
    assert rec["status"] == "ready"
    draft = client.get(f"/api/v1/skills/{rec['skill_id']}", headers=h).json()["draft"]
    assert draft["title"] == "Add a driver" and draft["steps"][0]["irreversible"]
    cost = db.execute("SELECT est_cost_usd FROM llm_usage WHERE purpose = 'skillgen'").fetchone()["est_cost_usd"]
    assert float(cost) == round((1000 * 1.0 + 100 * 5.0) / 1e6 * 0.5, 6)  # batch = half price
