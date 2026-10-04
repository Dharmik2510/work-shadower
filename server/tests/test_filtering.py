"""Relevance filter: local rules, Jev client (mocked HTTP), resilience, and skill generation with
keep / review / drop decisions."""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import httpx
import pytest

from app import skillgen
from app.eval_filter import evaluate
from app.filtering import (CircuitBreaker, JevClient, JevError, RelevanceFilter, Thresholds, keep_all,
                           local_scores, segment)
from app.llm import AnthropicProvider
from app.models import SkillContent

from .conftest import make_settings

EVAL_DIR = Path(__file__).resolve().parent.parent / "eval" / "recordings"


def scenario(name: str) -> dict:
    return json.loads((EVAL_DIR / f"{name}.json").read_text())


def fake_jev(answers_for, seen: list | None = None, status_seq: list[int] | None = None):
    """answers_for(event_compact, request_json) -> answers dict. status_seq: statuses to return first."""
    statuses = list(status_seq or [])

    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if seen is not None:
            seen.append((request, body))
        if statuses:
            st = statuses.pop(0)
            if st != 200:
                return httpx.Response(st, json={"error": "nope"}, headers={"retry-after": "0"})
        return httpx.Response(200, json={"model": "jev-1.13.0", "answers": answers_for(body["state"].get("event"), body),
                                         "usage": {"input_tokens": 300, "output_tokens": 20}})
    return httpx.MockTransport(handle)


def labelled_answers(sc: dict, new_task_at: set[int] = frozenset()):
    """A 'good model': answers from the scenario's labels."""
    def answers(ev, body):
        if ev is None:  # recording-level task_type question
            return {"task_type": {"type": "choice", "choice": "claims", "confidence": 0.9,
                                  "probabilities": {"claims": 0.9}}}
        drop = sc["labels"][str(ev["seq"])] == "drop"
        out = {"needed": {"type": "noul", "noul": 0.04 if drop else 0.97},
               "reason": {"type": "choice", "choice": "detour" if drop else "on_task", "confidence": 0.9,
                          "probabilities": {}},
               "irreversible": {"type": "noul", "noul": 0.9 if ev.get("label") in ("Submit", "Send") else 0.02}}
        if "new_task" in body["questions"]:
            out["new_task"] = {"type": "noul", "noul": 0.95 if ev["seq"] in new_task_at else 0.02}
        return out
    return answers


@pytest.fixture
def jev_settings(tmp_path_factory):
    return make_settings(tmp_path_factory, filter_provider="jev", jev_api_key="ts-test", jev_concurrency=4,
                         jev_breaker_failures=3, jev_breaker_cooldown_seconds=60)


TH = Thresholds(drop=0.9, review=0.6, split=0.85, min_segment_steps=3)


# ------------------------------------------------------------------ local rules
def test_local_rules_on_eval_set_never_wrongly_flag(tmp_path_factory):
    s = make_settings(tmp_path_factory, filter_provider="local")
    report = evaluate(sorted(EVAL_DIR.glob("*.json")), RelevanceFilter(s), TH)
    assert report["drop_precision"] == 1.0  # never auto-drops a needed step
    assert report["flag_precision"] == 1.0 and report["flag_recall"] == 1.0  # flags every unneeded step
    assert report["segmentation_accuracy"] == 1.0


def test_local_detour_respects_goal_and_protects_irreversible():
    sc = scenario("slack_detour")
    cleaned = skillgen.cleanup(sc["events"])
    assert local_scores(cleaned, "Added a driver")[7][1] == "detour"
    assert local_scores(cleaned, "Posted in Slack then added a driver")[7] == (0.05, "on_task")
    submit = next(e for e in cleaned if (e.get("element") or {}).get("label") == "Submit")
    assert local_scores(cleaned, None)[submit["seq"]][0] < 0.6


def test_undo_pair_is_dropped_and_rest_kept():
    sc = scenario("undo_mistake")
    res = RelevanceFilter(make_settings_local()).run(skillgen.cleanup(sc["events"]), sc["intent"], TH)
    assert {s for s, d in res.decisions.items() if d.decision == "drop"} == {4, 5}
    assert res.source == "local" and len(res.segments) == 1


def make_settings_local():
    from app.config import Settings

    return Settings(_env_file=None, filter_provider="local")


def test_keep_all_and_segment_min_size():
    sc = scenario("two_tasks")
    cleaned = skillgen.cleanup(sc["events"])
    assert {d.decision for d in keep_all(cleaned).decisions.values()} == {"keep"}
    res = RelevanceFilter(make_settings_local()).run(cleaned, None, TH)
    assert [len(s) for s in res.segments] == [4, 5]
    strict = Thresholds(min_segment_steps=10)
    assert len(segment(cleaned, res.decisions, strict)) == 1  # too small to split


# ------------------------------------------------------------------ Jev
def test_jev_request_shape_and_decisions(jev_settings):
    sc = scenario("slack_detour")
    seen: list = []
    jev = JevClient(jev_settings, fake_jev(labelled_answers(sc), seen), sleep=lambda s: None)
    res = RelevanceFilter(jev_settings, jev).run(skillgen.cleanup(sc["events"]), sc["intent"], TH)
    req, body = seen[0]
    assert str(req.url) == "https://api.typesafe.ai/v1/systemone"
    assert req.headers["authorization"] == "Bearer ts-test"
    assert body["model"] == "jev-latest" and set(body["questions"]) >= {"needed", "reason", "irreversible"}
    assert body["state"]["goal"] == sc["intent"] and "event" in body["state"]
    assert body["questions"]["reason"]["type"] == "choice" and "detour" in body["questions"]["reason"]["criteria"]
    dropped = {s for s, d in res.decisions.items() if d.decision == "drop"}
    assert dropped == {6, 7, 8}
    assert res.source == "jev" and res.jev_calls == len(res.decisions) and res.input_tokens > 0
    assert res.decisions[14].p_irreversible == pytest.approx(0.9)


def test_jev_cannot_drop_a_committing_step(jev_settings):
    sc = scenario("slack_detour")

    def answers(ev, body):
        a = labelled_answers(sc)(ev, body)
        a["needed"]["noul"] = 0.01  # model says nothing is needed
        return a
    res = RelevanceFilter(jev_settings, JevClient(jev_settings, fake_jev(answers), sleep=lambda s: None)).run(
        skillgen.cleanup(sc["events"]), sc["intent"], TH)
    assert res.decisions[14].decision == "review"  # Submit: capped below drop


def test_jev_retries_then_succeeds(jev_settings):
    sc = scenario("undo_mistake")
    seen: list = []
    jev = JevClient(jev_settings, fake_jev(labelled_answers(sc), seen, status_seq=[529, 429]), sleep=lambda s: None)
    res = RelevanceFilter(jev_settings, jev).run(skillgen.cleanup(sc["events"]), sc["intent"], TH)
    assert res.jev_failures == 0 and res.source in ("jev", "jev+local")


def test_jev_failure_falls_back_to_local_and_breaker_opens(jev_settings):
    sc = scenario("undo_mistake")
    seen: list = []

    def handle(request):
        seen.append(request)
        return httpx.Response(401, json={"error": "bad key"})
    jev = JevClient(jev_settings, httpx.MockTransport(handle), sleep=lambda s: None)
    flt = RelevanceFilter(jev_settings, jev)
    res = flt.run(skillgen.cleanup(sc["events"]), sc["intent"], TH)
    assert res.source == "local" and res.jev_calls == 0 and res.jev_failures == len(res.decisions)
    assert {s for s, d in res.decisions.items() if d.decision == "drop"} == {4, 5}  # local still works
    assert jev.breaker.state == "open"
    n = len(seen)
    flt.run(skillgen.cleanup(sc["events"]), sc["intent"], TH)
    assert len(seen) == n  # breaker open: no more calls


def test_jev_malformed_answers_use_local(jev_settings):
    sc = scenario("undo_mistake")
    jev = JevClient(jev_settings, fake_jev(lambda ev, b: {"needed": {"noul": "high"}}), sleep=lambda s: None)
    res = RelevanceFilter(jev_settings, jev).run(skillgen.cleanup(sc["events"]), sc["intent"], TH)
    assert res.decisions[5].decision == "drop" and res.decisions[3].decision == "keep"


def test_jev_segments_and_task_type(tmp_path_factory):
    s = make_settings(tmp_path_factory, filter_provider="jev", jev_api_key="k", jev_task_types="claims,billing")
    sc = scenario("slack_detour")
    sc["labels"] = {k: "keep" for k in sc["labels"]}
    jev = JevClient(s, fake_jev(labelled_answers(sc, new_task_at={9})), sleep=lambda x: None)
    res = RelevanceFilter(s, jev).run(skillgen.cleanup(sc["events"]), None, TH)
    assert len(res.segments) == 2 and res.segments[1][0] == 9
    assert res.task_type == "claims"


def test_circuit_breaker_half_open():
    t = [0.0]
    b = CircuitBreaker(2, 10, clock=lambda: t[0])
    b.failure()
    assert b.allow()
    b.failure()
    assert b.state == "open" and not b.allow()
    t[0] = 11
    assert b.state == "half_open" and b.allow() and not b.allow()  # exactly one trial
    b.success()
    assert b.state == "closed" and b.allow()


def test_jev_client_raises_on_bad_payload(jev_settings):
    jev = JevClient(jev_settings, httpx.MockTransport(lambda r: httpx.Response(200, json={"oops": 1})),
                    sleep=lambda s: None)
    with jev.client() as http, pytest.raises(JevError):
        jev.ask(http, "state", {})


# ------------------------------------------------------------------ skill generation with decisions
def _decisions(sc, settings, jev=None):
    cleaned = skillgen.cleanup(sc["events"])
    res = RelevanceFilter(settings, jev).run(cleaned, sc.get("intent"), TH)
    return cleaned, {k: asdict(v) for k, v in res.decisions.items()}


def test_heuristic_marks_excluded_steps_and_uses_intent(jev_settings):
    sc = scenario("slack_detour")
    jev = JevClient(jev_settings, fake_jev(labelled_answers(sc)), sleep=lambda s: None)
    cleaned, dec = _decisions(sc, jev_settings, jev)
    c = skillgen.heuristic_skill(cleaned, "Workflow in Google Chrome, Slack", cleaned=True, intent=sc["intent"],
                                 decisions=dec)
    SkillContent.model_validate(c)
    assert c["title"] == "Added a driver to an auto policy in PolicyCenter"
    assert c["goal"] == "Added a driver to an auto policy in PolicyCenter."
    excluded = [s for s in c["steps"] if s["excluded"]]
    assert excluded and all(s["filter"]["decision"] == "drop" for s in excluded)
    assert {q for s in excluded for q in s["source_seqs"]} == {6, 7, 8}
    assert "Slack" not in c["apps"]  # apps describe included steps only
    submit = next(s for s in c["steps"] if s["title"] == "Click “Submit”")
    assert submit["irreversible"] and not submit["excluded"]
    assert all(s["source_seqs"] for s in c["steps"])


def test_llm_exclude_flags_review_and_never_drops_alone(tmp_path_factory):
    sc = scenario("undo_mistake")
    cleaned, dec = _decisions(sc, make_settings_local())
    out = {
        "title": "Write a renewal notice", "goal": "g", "apps": ["Microsoft Word", "Slack"], "prerequisites": [],
        "inputs": [], "tags": ["letters"],
        "steps": [
            {"title": "Type greeting", "instruction": "Type it", "app": "Microsoft Word",
             "action": {"type": "type"}, "irreversible": False, "source_seqs": [2, 3, 999], "exclude": False},
            {"title": "Bold", "instruction": "Click Bold", "app": "Microsoft Word", "action": {"type": "click"},
             "irreversible": False, "source_seqs": [4], "exclude": True, "exclude_reason": "undone"},
            {"title": "Undo", "instruction": "Undo", "app": "Microsoft Word", "action": {"type": "key", "key": "cmd+z"},
             "irreversible": False, "source_seqs": [5], "exclude": True},
            {"title": "Sign", "instruction": "Insert signature", "app": "Microsoft Word", "action": {"type": "click"},
             "irreversible": False, "source_seqs": [6], "exclude": True, "exclude_reason": "looks optional"},
        ],
    }
    c = skillgen.postprocess(out, cleaned, dec)
    st = c["steps"]
    assert st[0]["source_seqs"] == [3] and not st[0]["excluded"]
    assert st[1]["excluded"] and st[2]["excluded"]  # filter said drop
    assert st[3]["filter"] == {"decision": "review", "reason": "looks optional", "p_drop": 0.6, "source": "llm"}
    assert not st[3]["excluded"]
    assert c["apps"] == ["Microsoft Word"]
    # every step dropped (no committing step among them) -> unusable, caller falls back
    everything = {**out, "steps": [{**s, "exclude": True} for s in out["steps"][:3]]}
    dec_all = {k: {**v, "decision": "drop", "p_drop": 0.95} for k, v in dec.items()}
    with pytest.raises(ValueError):
        skillgen.postprocess(everything, cleaned, dec_all)


def test_llm_tool_use_output_and_part_prompt(tmp_path_factory):
    s = make_settings(tmp_path_factory, llm_provider="anthropic", llm_api_key="sk")
    sc = scenario("undo_mistake")
    cleaned, dec = _decisions(sc, make_settings_local())
    seen: list = []
    tool_input = {"title": "Write a notice", "goal": "Write it", "apps": ["Microsoft Word"], "tags": ["x"],
                  "steps": [{"title": "Sign", "instruction": "Sign it", "action": {"type": "click"},
                             "irreversible": False, "source_seqs": [6], "exclude": False}]}

    def handle(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"content": [{"type": "tool_use", "id": "t1", "name": "emit_result",
                                                      "input": tool_input}],
                                         "stop_reason": "tool_use",
                                         "usage": {"input_tokens": 10, "cache_read_input_tokens": 90,
                                                   "output_tokens": 5}})
    p = AnthropicProvider(s, httpx.MockTransport(handle))
    res = skillgen.generate(cleaned, None, p, intent=sc["intent"], decisions=dec, cleaned=True, part=(2, 3))
    assert res.method == "llm" and res.content["title"] == "Write a notice"
    body = seen[0]
    assert body["tool_choice"] == {"type": "tool", "name": "emit_result"}
    assert body["tools"][0]["input_schema"]["properties"]["steps"]["items"]["properties"]["exclude"]
    prompt = body["messages"][0]["content"]
    assert "task 2 of 3" in prompt and sc["intent"] in prompt and '"filter": "drop"' in prompt


def test_anthropic_retries_on_overload(tmp_path_factory):
    s = make_settings(tmp_path_factory, llm_provider="anthropic", llm_api_key="sk", llm_max_retries=2)
    calls = []

    def handle(request):
        calls.append(1)
        if len(calls) < 3:
            return httpx.Response(529, json={"error": "overloaded"})
        return httpx.Response(200, json={"content": [{"type": "text", "text": '{"a": 1}'}], "usage": {}})
    p = AnthropicProvider(s, httpx.MockTransport(handle))
    p._sleep = lambda x: None
    assert p.complete_json("s", "p").data == {"a": 1} and len(calls) == 3
