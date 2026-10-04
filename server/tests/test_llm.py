"""LLM providers (mocked HTTP), parsing, and fallback to the heuristic generator."""
from __future__ import annotations

import json

import httpx
import pytest

from app import skillgen
from app.jobs import WorkerContext, run_until_empty
from app.llm import AnthropicProvider, LLMError, OpenAIProvider, make_llm, parse_json_object
from app.models import SkillContent
from app.redact import redact_events

from .conftest import idem, login, make_settings
from .fixtures import realistic_events, recording_body

GOOD = {
    "title": "Find a claim and notify the adjuster",
    "goal": "Look up a claim by policy number then email the adjuster team.",
    "apps": ["Google Chrome", "Microsoft Outlook"],
    "prerequisites": ["ClaimCenter access"],
    "inputs": [{"name": "policy_number", "description": "Policy", "example": "P-123456"}],
    "steps": [
        {"index": 1, "title": "Log in", "instruction": "Sign in to ClaimCenter.", "app": "Google Chrome",
         "action": {"type": "click", "target": {"role": "AXButton", "label": "Log In", "identifier": "loginBtn",
                                                "path": [], "window_title": "ClaimCenter Login"}},
         "screenshot_sha256": "f" * 64, "irreversible": False},
        {"index": 7, "title": "Send the email", "instruction": "Email jane@corp.example.com.", "app": "Microsoft Outlook",
         "action": {"type": "click", "target": {"role": "AXButton", "label": "Send"}}, "irreversible": False},
    ],
    "tags": ["claims"],
}


def anthropic_transport(text: str, status: int = 200, seen: list | None = None):
    def handle(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(status, json={
            "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-haiku-4-5",
            "content": [{"type": "text", "text": text}],
            "usage": {"input_tokens": 1200, "output_tokens": 300},
        })
    return httpx.MockTransport(handle)


def openai_transport(text: str, seen: list | None = None):
    def handle(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": text}}],
                                         "usage": {"prompt_tokens": 900, "completion_tokens": 200}})
    return httpx.MockTransport(handle)


@pytest.fixture
def llm_settings(tmp_path_factory):
    return make_settings(tmp_path_factory, llm_provider="anthropic", llm_api_key="sk-test")


def test_parse_json_object_variants():
    assert parse_json_object('{"a": 1}') == {"a": 1}
    assert parse_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_object('Sure! Here it is: {"a": {"b": 2}} hope that helps') == {"a": {"b": 2}}
    with pytest.raises(ValueError):
        parse_json_object("no json here")
    with pytest.raises(ValueError):
        parse_json_object("[1, 2]")


def test_anthropic_provider_request_and_parsing(llm_settings):
    seen: list[httpx.Request] = []
    p = AnthropicProvider(llm_settings, anthropic_transport("```json\n" + json.dumps(GOOD) + "\n```", seen=seen))
    res = p.complete_json("sys", "prompt")
    assert res.data["title"] == GOOD["title"] and (res.input_tokens, res.output_tokens) == (1200, 300)
    req = seen[0]
    assert str(req.url) == "https://api.anthropic.com/v1/messages"
    assert req.headers["x-api-key"] == "sk-test" and req.headers["anthropic-version"] == "2023-06-01"
    body = json.loads(req.content)
    assert body["model"] == "claude-haiku-4-5" and body["system"][0]["text"] == "sys" and body["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert body["messages"] == [{"role": "user", "content": "prompt"}]
    assert p.estimate_cost(1_000_000, 1_000_000) == pytest.approx(6.0)
    with pytest.raises(LLMError) as e:
        AnthropicProvider(llm_settings, anthropic_transport("garbage, not json")).complete_json("s", "p")
    assert e.value.input_tokens == 1200  # usage still reported on parse failure
    with pytest.raises(LLMError):
        AnthropicProvider(llm_settings, anthropic_transport("{}", status=529)).complete_json("s", "p")


def test_openai_provider_request_and_parsing(tmp_path_factory):
    s = make_settings(tmp_path_factory, llm_provider="openai", llm_api_key="k", llm_model="gpt-x",
                      llm_base_url="http://llm.local/v1")
    seen: list[httpx.Request] = []
    p = make_llm(s, openai_transport(json.dumps(GOOD), seen))
    assert isinstance(p, OpenAIProvider)
    res = p.complete_json("sys", "prompt")
    assert res.data["steps"][0]["title"] == "Log in" and res.input_tokens == 900
    body = json.loads(seen[0].content)
    assert str(seen[0].url) == "http://llm.local/v1/chat/completions"
    assert body["response_format"] == {"type": "json_object"} and body["messages"][0]["role"] == "system"
    assert seen[0].headers["authorization"] == "Bearer k"


def test_skillgen_llm_success_is_postprocessed(llm_settings):
    p = AnthropicProvider(llm_settings, anthropic_transport(json.dumps(GOOD)))
    usage: list = []
    events = redact_events(realistic_events())
    res = skillgen.generate(events, None, p, on_usage=lambda i, o, ok: usage.append((i, o, ok)))
    assert res.method == "llm" and usage == [(1200, 300, True)]
    c = res.content
    SkillContent.model_validate(c)
    assert [s["index"] for s in c["steps"]] == [1, 2]
    assert c["steps"][0]["screenshot_sha256"] is None  # sha not present in the events -> dropped
    assert c["steps"][1]["irreversible"] is True  # "Send" forced irreversible
    assert "jane@corp.example.com" not in json.dumps(c) and "[REDACTED:email]" in c["steps"][1]["instruction"]


@pytest.mark.parametrize("text", ["not json at all", json.dumps({"title": "x", "steps": "nope"}),
                                  json.dumps({"title": "x", "steps": []})])
def test_skillgen_falls_back_on_malformed_output(llm_settings, text):
    p = AnthropicProvider(llm_settings, anthropic_transport(text))
    usage: list = []
    res = skillgen.generate(redact_events(realistic_events()), "My title", p,
                            on_usage=lambda i, o, ok: usage.append(ok))
    assert res.method == "heuristic" and res.error
    assert res.content["title"] == "My title" and len(res.content["steps"]) >= 8
    assert len(usage) == 1


def test_heuristic_without_title_hint():
    c = skillgen.heuristic_skill(redact_events(realistic_events()))
    assert c["title"] == "Send in Microsoft Outlook"
    assert "microsoft outlook" in c["tags"] or "outlook" in c["tags"]
    assert any("credentials" in p for p in c["prerequisites"])


def test_worker_uses_llm_records_usage_and_respects_budget(client, app, db, llm_settings):
    h = login(client, "a@example.com", team="Claims")
    llm = AnthropicProvider(llm_settings, anthropic_transport(json.dumps(GOOD)))
    ctx = WorkerContext(pool=app.state.pool, settings=llm_settings.model_copy(update={"llm_daily_calls_per_user": 1}),
                        llm=llm, embedder=app.state.embedder, vector_enabled=False, worker_id="t")
    r1 = client.post("/api/v1/recordings", headers={**h, **idem()}, json=recording_body(title_hint=None)).json()
    run_until_empty(ctx)
    s1 = client.get(f"/api/v1/skills/{client.get(f'/api/v1/recordings/{r1['id']}', headers=h).json()['skill_id']}",
                    headers=h).json()
    assert s1["draft"]["title"] == GOOD["title"]
    u = db.execute("SELECT * FROM llm_usage").fetchall()
    assert len(u) == 1 and u[0]["input_tokens"] == 1200 and float(u[0]["est_cost_usd"]) == pytest.approx(0.0027)
    assert u[0]["team_id"] is not None
    # budget exhausted (limit 1/day): second recording still processed, heuristically, no LLM call
    r2 = client.post("/api/v1/recordings", headers={**h, **idem()}, json=recording_body()).json()
    run_until_empty(ctx)
    rec2 = client.get(f"/api/v1/recordings/{r2['id']}", headers=h).json()
    assert rec2["status"] == "ready"
    s2 = client.get(f"/api/v1/skills/{rec2['skill_id']}", headers=h).json()
    assert s2["draft"]["title"] == "Look up a claim and email the adjuster"
    assert db.execute("SELECT count(*) AS n FROM llm_usage").fetchone()["n"] == 1


def test_repair_with_mocked_llm(client, app, llm_settings):
    h = login(client, "a@example.com")
    content = {"title": "t", "steps": [{"title": "Click Save", "action": {"type": "click",
               "target": {"role": "AXButton", "label": "Save", "window_title": "Doc"}}}]}
    sid = client.post("/api/v1/skills", headers=h, json={"content": content, "visibility": "private"}).json()["id"]
    client.post(f"/api/v1/skills/{sid}/publish", headers=h)
    old = app.state.llm
    app.state.llm = AnthropicProvider(llm_settings, anthropic_transport('{"node": 1, "confidence": 0.82, "reason": "renamed"}'))
    try:
        body = {"skill_id": sid, "version": 1, "step_index": 1, "ui_tree": [
            {"role": "AXButton", "label": "Cancel", "identifier": None, "path": []},
            {"role": "AXButton", "label": "Save changes", "identifier": "save", "path": ["AXWindow:Doc"]}]}
        r = client.post("/api/v1/replay/repair", headers=h, json=body)
        assert r.status_code == 200
        assert r.json() == {"target": {"role": "AXButton", "label": "Save changes", "identifier": "save",
                                       "path": ["AXWindow:Doc"], "window_title": "Doc"}, "confidence": 0.82}
        app.state.llm = AnthropicProvider(llm_settings, anthropic_transport('{"node": 99, "confidence": 0.9}'))
        assert client.post("/api/v1/replay/repair", headers=h, json=body).json() == {"target": None, "confidence": 0.0}
        too_big = {**body, "ui_tree": body["ui_tree"] * 151}
        assert client.post("/api/v1/replay/repair", headers=h, json=too_big).status_code == 422
    finally:
        app.state.llm = old
