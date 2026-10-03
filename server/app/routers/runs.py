"""Replay telemetry (runs + steps) and LLM-assisted target repair."""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import JSONResponse
from psycopg.types.json import Jsonb

from .. import skills as svc
from .. import usage
from ..auth import Conn, User
from ..errors import ApiError, not_found
from ..flags import get_flags
from ..llm import LLMError
from ..models import RepairRequest, RunCreate, RunFinish, RunStepCreate
from .recordings import require_idempotency_key

router = APIRouter()
log = logging.getLogger("app.runs")


def _run_view(r: dict) -> dict:
    return {
        "id": str(r["id"]),
        "skill_id": str(r["skill_id"]),
        "version": r["version"],
        "mode": r["mode"],
        "status": r["status"],
        "error": r["error"],
        "inputs": r["inputs"],
        "user": {"id": str(r["user_id"]), "name": r["user_name"]},
        "step_count": r["step_count"],
        "started_at": svc.iso(r["started_at"]),
        "finished_at": svc.iso(r["finished_at"]),
    }


def _replay_enabled(conn) -> None:
    if not get_flags(conn)["replay_enabled"]:
        raise ApiError(403, "replay_disabled", "replay is currently disabled by an administrator")


@router.post("/runs", status_code=201)
def create_run(body: RunCreate, user: User, conn: Conn,
               idempotency_key: str | None = Header(None, alias="Idempotency-Key")):
    key = require_idempotency_key(idempotency_key)
    existing = conn.execute(
        "SELECT id::text AS id FROM runs WHERE user_id = %s AND idempotency_key = %s", (user.id, key)
    ).fetchone()
    if existing:
        return JSONResponse({"id": existing["id"]}, status_code=200)
    _replay_enabled(conn)
    skill = svc.get_visible(conn, user, body.skill_id)
    if not skill["current_version"] or body.version > skill["current_version"]:
        raise ApiError(422, "invalid_version", "version is not a published version of this skill")
    row = conn.execute(
        """INSERT INTO runs (user_id, skill_id, version, mode, inputs, idempotency_key)
           VALUES (%s, %s, %s, %s, %s, %s)
           ON CONFLICT (user_id, idempotency_key) DO NOTHING RETURNING id::text AS id""",
        (user.id, body.skill_id, body.version, body.mode, Jsonb(body.inputs), key),
    ).fetchone()
    if row is None:
        row = conn.execute(
            "SELECT id::text AS id FROM runs WHERE user_id = %s AND idempotency_key = %s", (user.id, key)
        ).fetchone()
        return JSONResponse({"id": row["id"]}, status_code=200)
    conn.execute("UPDATE skills SET last_run_at = now() WHERE id = %s", (body.skill_id,))
    return {"id": row["id"]}


def _own_run(conn, user, run_id: str, lock: bool = False) -> dict:
    svc.require_uuid(run_id, "run")
    row = conn.execute(
        f"SELECT id, user_id::text AS user_id, skill_id, status FROM runs WHERE id = %s{' FOR UPDATE' if lock else ''}",
        (run_id,),
    ).fetchone()
    if not row or (row["user_id"] != user.id and not user.is_admin):
        raise not_found("run")
    return row


@router.post("/runs/{run_id}/steps", status_code=201)
def add_step(run_id: str, body: RunStepCreate, user: User, conn: Conn):
    _own_run(conn, user, run_id)
    row = conn.execute(
        """INSERT INTO run_steps (run_id, step_index, status, strategy, duration_ms, detail)
           VALUES (%s, %s, %s, %s, %s, %s) RETURNING id::text AS id""",
        (run_id, body.step_index, body.status, body.strategy, body.duration_ms,
         Jsonb(body.detail) if body.detail is not None else None),
    ).fetchone()
    return {"id": row["id"]}


@router.post("/runs/{run_id}/finish")
def finish_run(run_id: str, body: RunFinish, user: User, conn: Conn):
    run = _own_run(conn, user, run_id, lock=True)
    if run["status"] != "running":  # idempotent: a retried finish never double-counts
        return {"id": run_id, "status": run["status"]}
    conn.execute("UPDATE runs SET status = %s, error = %s, finished_at = now() WHERE id = %s",
                 (body.status, body.error, run_id))
    conn.execute(
        """UPDATE skills SET run_count = run_count + 1,
                  run_success_count = run_success_count + (CASE WHEN %s = 'succeeded' THEN 1 ELSE 0 END),
                  last_run_at = now()
           WHERE id = %s""",
        (body.status, run["skill_id"]),
    )
    return {"id": run_id, "status": body.status}


@router.get("/runs")
def list_runs(user: User, conn: Conn, skill_id: str | None = Query(None), limit: int | None = Query(None),
              cursor: str | None = Query(None)):
    lim = svc.clamp_limit(limit)
    params: dict = {"uid": user.id, "lim": lim + 1}
    where = []
    if skill_id:
        skill = svc.get_visible(conn, user, skill_id)
        where.append("r.skill_id = %(sid)s")
        params["sid"] = skill_id
        if not svc.can_edit(user, skill):  # owners/admins see everyone's runs of their skill
            where.append("r.user_id = %(uid)s")
    elif not user.is_admin:
        where.append("r.user_id = %(uid)s")
    c = svc.decode_cursor(cursor, 2)
    if c:
        where.append("(r.started_at, r.id) < (%(c_ts)s::timestamptz, %(c_id)s::uuid)")
        params["c_ts"], params["c_id"] = c
    rows = conn.execute(
        f"""SELECT r.*, u.name AS user_name,
                   (SELECT count(*) FROM run_steps st WHERE st.run_id = r.id) AS step_count
            FROM runs r JOIN users u ON u.id = r.user_id
            WHERE {' AND '.join(where) or 'TRUE'}
            ORDER BY r.started_at DESC, r.id DESC LIMIT %(lim)s""",
        params,
    ).fetchall()
    nxt = svc.encode_cursor(rows[lim - 1]["started_at"], rows[lim - 1]["id"]) if len(rows) > lim else None
    return {"items": [_run_view(r) for r in rows[:lim]], "next_cursor": nxt}


REPAIR_SYSTEM = """You help a macOS automation tool recover when a recorded UI element can no longer be found.
Given the step being replayed, the originally recorded target, and the current accessibility tree (numbered nodes),
pick the node that the user would click/type into to perform the same step.
Reply with ONE JSON object only: {"node": <number or null>, "confidence": <0..1>, "reason": "<short>"}.
Use null when no node is a plausible match. Be conservative: never pick a node that would do something different."""


@router.post("/replay/repair")
def repair(body: RepairRequest, request: Request, user: User, conn: Conn):
    llm = request.app.state.llm
    flags = get_flags(conn)
    if not llm.enabled or not flags["llm_enabled"]:
        raise ApiError(503, "llm_disabled", "LLM repair is not available")
    if not flags["replay_enabled"]:
        raise ApiError(403, "replay_disabled", "replay is currently disabled by an administrator")
    skill = svc.get_visible(conn, user, body.skill_id)
    ver = conn.execute("SELECT content FROM skill_versions WHERE skill_id = %s AND version = %s",
                       (skill["id"], body.version)).fetchone()
    if not ver:
        raise not_found("version")
    steps = ver["content"].get("steps") or []
    if body.step_index > len(steps):
        raise ApiError(422, "invalid_step", "step_index is out of range")
    if not body.ui_tree:
        return {"target": None, "confidence": 0.0}
    settings = request.app.state.settings
    if not usage.within_budget(conn, user.id, settings.llm_daily_calls_per_user):
        raise ApiError(429, "llm_budget_exceeded", "daily LLM budget exhausted; try again tomorrow")
    step = steps[body.step_index - 1]
    original = (step.get("action") or {}).get("target") or {}
    nodes = [n.model_dump() for n in body.ui_tree]
    prompt = (
        f"Step: {json.dumps({k: step.get(k) for k in ('title', 'instruction', 'app')}, ensure_ascii=False)}\n"
        f"Action type: {(step.get('action') or {}).get('type')}\n"
        f"Recorded target: {json.dumps(original, ensure_ascii=False)}\n"
        "Current UI nodes:\n" + "\n".join(f"{i}: {json.dumps(n, ensure_ascii=False)}" for i, n in enumerate(nodes))
    )
    target, confidence = None, 0.0
    try:
        res = llm.complete_json(REPAIR_SYSTEM, prompt, max_tokens=300)
        itok, otok, ok = res.input_tokens, res.output_tokens, True
        idx = res.data.get("node")
        conf = float(res.data.get("confidence") or 0)
        if isinstance(idx, int) and not isinstance(idx, bool) and 0 <= idx < len(nodes):
            n = nodes[idx]
            target = {"role": n.get("role"), "label": n.get("label"), "identifier": n.get("identifier"),
                      "path": n.get("path") or [], "window_title": original.get("window_title")}
            confidence = max(0.0, min(1.0, conf))
    except LLMError as e:
        itok, otok, ok = e.input_tokens, e.output_tokens, False
        log.warning("repair llm call failed", extra={"error": str(e)})
    except (TypeError, ValueError):
        itok, otok, ok = res.input_tokens, res.output_tokens, False
    usage.record(conn, user_id=user.id, team_id=user.primary_team_id, purpose="repair", provider=llm,
                 model=llm.model, input_tokens=itok, output_tokens=otok,
                 est_cost_usd=llm.estimate_cost(itok, otok), ok=ok)
    return {"target": target, "confidence": round(confidence, 3)}
