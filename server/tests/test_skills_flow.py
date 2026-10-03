from __future__ import annotations

import json

import psycopg
import pytest

from app.jobs import run_until_empty

from .conftest import idem, login
from .fixtures import recording_body, realistic_events
from .test_api_basics import PNG_SHA, _upload


def _content(title="Approve an expense report", steps=2, shot=None, tags=("finance",)):
    return {
        "title": title,
        "goal": "Approve a pending expense report in Concur.",
        "apps": ["Google Chrome"],
        "prerequisites": [],
        "inputs": [],
        "steps": [
            {"index": i + 1, "title": f"Step {i + 1}", "instruction": f"Do thing {i + 1} in Concur",
             "app": "Google Chrome", "action": {"type": "click", "target": {"role": "AXButton", "label": f"B{i}"}},
             "screenshot_sha256": shot if i == 0 else None}
            for i in range(steps)
        ],
        "tags": list(tags),
    }


def _publish_new(client, h, content, team_id=None, visibility="org"):
    r = client.post("/api/v1/skills", headers=h, json={"content": content, "team_id": team_id, "visibility": visibility})
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    r = client.post(f"/api/v1/skills/{sid}/publish", headers=h)
    assert r.status_code == 200, r.text
    return sid


def _team_id(client, h, name):
    return next(t["id"] for t in client.get("/api/v1/me", headers=h).json()["teams"] if t["name"] == name)


# ------------------------------------------------------------------ worker -> draft skill
def test_worker_turns_recording_into_redacted_draft(client, worker, db):
    h = login(client, "dharmik@example.com", "Dharmik", team="Claims")
    _upload(client, h)
    rec = client.post("/api/v1/recordings", headers={**h, **idem()},
                      json=recording_body(realistic_events(shot=PNG_SHA))).json()
    # stored events were re-redacted server-side
    stored = json.dumps(db.execute("SELECT events FROM recordings WHERE id = %s", (rec["id"],)).fetchone()["events"])
    assert "hunter2" not in stored
    assert "adjuster.team@intact.example.com" not in stored and "[REDACTED:email]" in stored
    assert "416-555-0199" not in stored and "[REDACTED:phone]" in stored
    assert "session=abc123" not in stored

    assert run_until_empty(worker) == 1
    r = client.get(f"/api/v1/recordings/{rec['id']}", headers=h).json()
    assert r["status"] == "ready" and r["skill_id"]
    skill = client.get(f"/api/v1/skills/{r['skill_id']}", headers=h).json()
    assert skill["status"] == "draft" and skill["published"] is None and skill["current_version"] is None
    assert skill["source_recording_id"] == rec["id"]
    assert skill["visibility"] == "team" and skill["team"]["name"] == "Claims"
    assert skill["health"] == {"runs": 0, "success_rate": None, "last_run_at": None}
    d = skill["draft"]
    blob = json.dumps(d)
    assert "hunter2" not in blob and "intact.example.com" not in blob and "416-555" not in blob
    assert d["title"] == "Look up a claim and email the adjuster"
    assert d["apps"] == ["Google Chrome", "Microsoft Outlook"]
    kinds = [s["action"]["type"] for s in d["steps"]]
    # noise dropped: scrolls, tab key, focus clicks, the duplicated Search Claims click, app flicking
    assert kinds.count("open_app") == 2
    assert "key" not in kinds
    assert 8 <= len(d["steps"]) <= 14, [s["title"] for s in d["steps"]]
    assert [s["index"] for s in d["steps"]] == list(range(1, len(d["steps"]) + 1))
    pw = next(s for s in d["steps"] if (s["action"].get("target") or {}).get("label") == "Password")
    assert pw["action"]["text"] is None
    policy = next(s for s in d["steps"] if (s["action"].get("target") or {}).get("label") == "Policy Number")
    assert policy["action"]["text"] == "{{policy_number}}"  # typing burst merged + parameterised
    assert {"name": "policy_number", "description": "Value for “Policy Number”", "example": "P-123456"} in d["inputs"]
    send = next(s for s in d["steps"] if (s["action"].get("target") or {}).get("label") == "Send")
    assert send["irreversible"] is True
    search_claims = [s for s in d["steps"] if (s["action"].get("target") or {}).get("label") == "Search Claims"]
    assert len(search_claims) == 1 and search_claims[0]["screenshot_sha256"] == PNG_SHA
    assert search_claims[0]["expect"] is None or isinstance(search_claims[0]["expect"], dict)
    assert any(s["action"]["type"] == "open_url" and "?" not in s["action"]["url"] for s in d["steps"])
    # the generated draft is valid input for PATCH (round-trips the schema)
    assert client.patch(f"/api/v1/skills/{skill['id']}", headers=h, json={"content": d}).status_code == 200
    # idempotent job: running the job again does not create a second skill
    db.execute("INSERT INTO jobs (kind, payload) VALUES ('generate_skill', %s)",
               (json.dumps({"recording_id": rec["id"]}),))
    run_until_empty(worker)
    assert db.execute("SELECT count(*) AS n FROM skills").fetchone()["n"] == 1


# ------------------------------------------------------------------ versions
def test_patch_publish_creates_immutable_versions(client, db):
    h = login(client, "owner@example.com")
    r = client.post("/api/v1/skills", headers=h, json={"content": _content(), "visibility": "private"})
    sid = r.json()["id"]
    assert r.json()["status"] == "draft"
    assert client.post(f"/api/v1/skills/{sid}/publish", headers=h).json()["current_version"] == 1
    s = client.get(f"/api/v1/skills/{sid}", headers=h).json()
    assert s["status"] == "published" and s["draft"] is None and s["published"]["title"] == "Approve an expense report"
    # publishing without a draft is a conflict
    assert client.post(f"/api/v1/skills/{sid}/publish", headers=h).json()["error"]["code"] == "no_draft"
    # empty PATCH creates a draft from published
    s = client.patch(f"/api/v1/skills/{sid}", headers=h, json={}).json()
    assert s["draft"] == s["published"]
    s = client.patch(f"/api/v1/skills/{sid}", headers=h,
                     json={"content": _content(title="Approve an expense report (v2)", steps=3)}).json()
    assert s["draft"]["title"].endswith("(v2)") and s["published"]["title"] == "Approve an expense report"
    s = client.post(f"/api/v1/skills/{sid}/publish", headers=h).json()
    assert s["current_version"] == 2 and s["draft"] is None and len(s["published"]["steps"]) == 3
    vs = client.get(f"/api/v1/skills/{sid}/versions", headers=h).json()["items"]
    assert [v["version"] for v in vs] == [2, 1]
    assert vs[0]["created_by"]["email"] == "owner@example.com"
    v1 = client.get(f"/api/v1/skills/{sid}/versions/1", headers=h).json()
    assert v1["content"]["title"] == "Approve an expense report" and len(v1["content"]["steps"]) == 2
    assert client.get(f"/api/v1/skills/{sid}/versions/9", headers=h).status_code == 404
    with pytest.raises(psycopg.errors.RaiseException):
        db.execute("UPDATE skill_versions SET content = '{}' WHERE skill_id = %s", (sid,))
    # non-owner cannot edit (even if visible)
    client.patch(f"/api/v1/skills/{sid}", headers=h, json={"visibility": "org"})
    other = login(client, "other@example.com")
    r = client.patch(f"/api/v1/skills/{sid}", headers=other, json={"content": _content()})
    assert r.status_code == 403
    assert client.post(f"/api/v1/skills/{sid}/archive", headers=other).status_code == 403
    # admin can
    admin = login(client, "admin@example.com")
    assert client.post(f"/api/v1/skills/{sid}/archive", headers=admin).json()["status"] == "archived"
    # validation: empty steps cannot be published; team visibility needs a team
    sid2 = client.post("/api/v1/skills", headers=h, json={"content": _content(steps=0), "visibility": "private"}).json()["id"]
    assert client.post(f"/api/v1/skills/{sid2}/publish", headers=h).json()["error"]["code"] == "skill_has_no_steps"
    r = client.post("/api/v1/skills", headers=h, json={"content": _content(), "visibility": "team"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "team_required"


# ------------------------------------------------------------------ visibility
def test_visibility_enforced_everywhere(client):
    a = login(client, "author@example.com", team="Claims")
    teammate = login(client, "teammate@example.com", team="Claims")
    outsider = login(client, "outsider@example.com", team="IT")
    claims = _team_id(client, a, "Claims")
    _upload(client, a)  # screenshot used only by the private skill

    priv = _publish_new(client, a, _content("Quarterly reserve review", shot=PNG_SHA, tags=("reserve",)), claims, "private")
    team = _publish_new(client, a, _content("Reserve change for claims team", tags=("reserve",)), claims, "team")
    org = _publish_new(client, a, _content("Reserve glossary for everyone", tags=("reserve",)), claims, "org")
    draft_org = client.post("/api/v1/skills", headers=a,
                            json={"content": _content("Reserve draft"), "visibility": "org"}).json()["id"]

    def search_ids(h):
        return {i["id"] for i in client.get("/api/v1/search?q=reserve", headers=h).json()["items"]}

    def list_ids(h):
        return {i["id"] for i in client.get("/api/v1/skills?limit=100", headers=h).json()["items"]}

    assert search_ids(a) == {priv, team, org}  # search = published only
    assert list_ids(a) == {priv, team, org, draft_org}
    assert search_ids(teammate) == {team, org} and list_ids(teammate) == {team, org}
    assert search_ids(outsider) == {org} and list_ids(outsider) == {org}
    for sid, ok in ((priv, False), (team, False), (org, True), (draft_org, False)):
        assert (client.get(f"/api/v1/skills/{sid}", headers=outsider).status_code == 200) is ok
        assert (client.get(f"/api/v1/skills/{sid}/versions", headers=outsider).status_code == 200) is ok
    assert client.get(f"/api/v1/skills/{team}", headers=teammate).status_code == 200
    # non-owners never see the draft field
    client.patch(f"/api/v1/skills/{org}", headers=a, json={})
    assert client.get(f"/api/v1/skills/{org}", headers=outsider).json()["draft"] is None
    assert client.get(f"/api/v1/skills/{org}", headers=a).json()["draft"] is not None
    # asset referenced only by the private skill
    assert client.get(f"/api/v1/assets/{PNG_SHA}", headers=a).status_code == 200
    assert client.get(f"/api/v1/assets/{PNG_SHA}", headers=teammate).status_code == 404
    assert client.get(f"/api/v1/assets/{PNG_SHA}", headers=outsider).status_code == 404
    # make it team-visible -> teammate can see the screenshot, outsider still can't
    client.patch(f"/api/v1/skills/{priv}", headers=a, json={"visibility": "team"})
    assert client.get(f"/api/v1/assets/{PNG_SHA}", headers=teammate).status_code == 200
    token = teammate["Authorization"].split()[1]
    assert client.get(f"/api/v1/assets/{PNG_SHA}?access_token={token}").status_code == 200
    assert client.get(f"/api/v1/assets/{PNG_SHA}", headers=outsider).status_code == 404
    # filters
    assert {i["id"] for i in client.get("/api/v1/skills?mine=true", headers=teammate).json()["items"]} == set()
    assert {i["id"] for i in client.get(f"/api/v1/skills?team_id={claims}&status=published&q=glossary",
                                        headers=outsider).json()["items"]} == {org}
    # cannot assign a skill to a team you are not in
    it = _team_id(client, outsider, "IT")
    r = client.post("/api/v1/skills", headers=a, json={"content": _content(), "team_id": it, "visibility": "team"})
    assert r.status_code == 403


def test_search_ranking_and_summary_shape(client):
    h = login(client, "s@example.com")
    a = _publish_new(client, h, _content("Reset a password in Okta", tags=("it", "okta")))
    b = _publish_new(client, h, {**_content("Expense report approval"), "goal": "Mentions okta once in the goal."})
    items = client.get("/api/v1/search?q=okta pass", headers=h).json()["items"]
    assert [i["id"] for i in items][:2] == [a, b]
    assert items[0]["score"] > items[1]["score"] > 0
    assert set(items[0]) == {"id", "title", "goal", "owner", "team", "visibility", "status", "tags", "apps",
                             "current_version", "updated_at", "health", "score"}
    assert client.get("/api/v1/search?q=zzzunmatched", headers=h).json()["items"] == []
    assert len(client.get("/api/v1/search?q=", headers=h).json()["items"]) == 2


# ------------------------------------------------------------------ runs
def test_runs_steps_finish_update_health(client):
    owner = login(client, "owner@example.com")
    runner = login(client, "runner@example.com")
    sid = _publish_new(client, owner, _content())
    key = idem()
    r = client.post("/api/v1/runs", headers={**runner, **key}, json={"skill_id": sid, "version": 1, "mode": "guided", "inputs": {"x": 1}})
    assert r.status_code == 201
    run_id = r.json()["id"]
    r2 = client.post("/api/v1/runs", headers={**runner, **key}, json={"skill_id": sid, "version": 1, "mode": "guided"})
    assert r2.status_code == 200 and r2.json() == {"id": run_id}
    assert client.post("/api/v1/runs", headers=runner, json={"skill_id": sid, "version": 1, "mode": "auto"}).status_code == 400
    bad = client.post("/api/v1/runs", headers={**runner, **idem()}, json={"skill_id": sid, "version": 7, "mode": "auto"})
    assert bad.json()["error"]["code"] == "invalid_version"
    for i, st in ((1, "ok"), (2, "repaired")):
        r = client.post(f"/api/v1/runs/{run_id}/steps", headers=runner,
                        json={"step_index": i, "status": st, "strategy": "deterministic" if st == "ok" else "llm_repair",
                              "duration_ms": 120, "detail": {"note": "x"}})
        assert r.status_code == 201
    assert client.post(f"/api/v1/runs/{run_id}/steps", headers=owner,
                       json={"step_index": 1, "status": "ok", "strategy": "human", "duration_ms": 1}).status_code == 404
    assert client.post(f"/api/v1/runs/{run_id}/finish", headers=runner, json={"status": "succeeded"}).json() == \
        {"id": run_id, "status": "succeeded"}
    client.post(f"/api/v1/runs/{run_id}/finish", headers=runner, json={"status": "succeeded"})  # retried: no double count
    run2 = client.post("/api/v1/runs", headers={**runner, **idem()}, json={"skill_id": sid, "version": 1, "mode": "auto"}).json()["id"]
    client.post(f"/api/v1/runs/{run2}/finish", headers=runner, json={"status": "failed", "error": "element missing"})
    health = client.get(f"/api/v1/skills/{sid}", headers=owner).json()["health"]
    assert health["runs"] == 2 and health["success_rate"] == 0.5 and health["last_run_at"]
    assert client.get("/api/v1/search?q=expense", headers=runner).json()["items"][0]["health"]["runs"] == 2
    # owner sees all runs of their skill; runner sees own; others none
    assert len(client.get(f"/api/v1/runs?skill_id={sid}", headers=owner).json()["items"]) == 2
    mine = client.get("/api/v1/runs", headers=runner).json()["items"]
    assert len(mine) == 2 and {m["status"] for m in mine} == {"succeeded", "failed"}
    assert next(m for m in mine if m["id"] == run_id)["step_count"] == 2
    # suggest-fix is stored for the owner
    r = client.post(f"/api/v1/skills/{sid}/suggest-fix", headers=runner,
                    json={"step_index": 1, "new_target": {"role": "AXButton", "label": "B0 (new)"}, "note": "renamed"})
    assert r.status_code == 201
    sugg = client.get(f"/api/v1/skills/{sid}/suggestions", headers=owner).json()["items"]
    assert sugg[0]["new_target"]["label"] == "B0 (new)"


def test_repair_returns_503_without_llm(client):
    h = login(client, "r@example.com")
    sid = _publish_new(client, h, _content())
    r = client.post("/api/v1/replay/repair", headers=h, json={
        "skill_id": sid, "version": 1, "step_index": 1,
        "ui_tree": [{"role": "AXButton", "label": "B0", "identifier": None, "path": []}]})
    assert r.status_code == 503 and r.json()["error"]["code"] == "llm_disabled"


# ------------------------------------------------------------------ admin flags
def test_kill_switch(client):
    admin = login(client, "admin@example.com")
    member = login(client, "m@example.com")
    assert client.put("/api/v1/admin/flags", headers=member, json={"recording_enabled": False}).status_code == 403
    r = client.put("/api/v1/admin/flags", headers=admin, json={"recording_enabled": False, "replay_enabled": False})
    assert r.status_code == 200 and r.json()["recording_enabled"] is False
    assert client.get("/api/v1/admin/flags", headers=admin).json()["replay_enabled"] is False
    cfg = client.get("/api/v1/config", headers=member).json()
    assert cfg["recording_enabled"] is False and cfg["replay_enabled"] is False
    r = client.post("/api/v1/recordings", headers={**member, **idem()}, json=recording_body())
    assert r.status_code == 403 and r.json() == {"error": {"code": "recording_disabled", "message": r.json()["error"]["message"]}}
    sid = _publish_new(client, member, _content())
    r = client.post("/api/v1/runs", headers={**member, **idem()}, json={"skill_id": sid, "version": 1, "mode": "auto"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "replay_disabled"
    assert client.put("/api/v1/admin/flags", headers=admin, json={"screenshot_policy": "sometimes"}).status_code == 422
    client.put("/api/v1/admin/flags", headers=admin, json={"recording_enabled": True})
    assert client.post("/api/v1/recordings", headers={**member, **idem()}, json=recording_body()).status_code == 202


def test_admin_usage(client, worker, db):
    admin = login(client, "admin@example.com", team="UBI")
    sid = _publish_new(client, admin, _content())
    run = client.post("/api/v1/runs", headers={**admin, **idem()}, json={"skill_id": sid, "version": 1, "mode": "auto"}).json()["id"]
    client.post(f"/api/v1/runs/{run}/finish", headers=admin, json={"status": "succeeded"})
    team_id = _team_id(client, admin, "UBI")
    db.execute("INSERT INTO llm_usage (user_id, team_id, purpose, provider, model, input_tokens, output_tokens, est_cost_usd, ok) "
               "SELECT id, %s, 'skillgen', 'anthropic', 'claude-haiku-4-5', 1000, 500, 0.0035, true FROM users WHERE email = 'admin@example.com'",
               (team_id,))
    u = client.get("/api/v1/admin/usage?days=30", headers=admin).json()
    assert u["llm_calls"] == 1 and u["input_tokens"] == 1000 and u["output_tokens"] == 500
    assert u["est_cost_usd"] == pytest.approx(0.0035)
    assert u["by_team"][0]["team"]["name"] == "UBI"
    assert u["skills_created"] == 1 and u["runs"] == 1 and u["run_success_rate"] == 1.0
