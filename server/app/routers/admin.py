"""Admin: flags (kill switches), usage, job ops."""
from __future__ import annotations

import json

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse

from .. import skills as svc
from ..auth import Admin, Conn
from ..errors import ApiError, not_found
from ..flags import get_flags, update_flags
from ..jobs import JOB_STATUSES, retry_job
from ..models import FlagsUpdate

router = APIRouter(prefix="/admin")


@router.get("/flags")
def flags(user: Admin, conn: Conn):
    return get_flags(conn)


@router.put("/flags")
def put_flags(body: FlagsUpdate, user: Admin, conn: Conn):
    return update_flags(conn, body.model_dump(exclude_unset=True), user.id)


@router.get("/usage")
def usage(user: Admin, conn: Conn, days: int = Query(30, ge=1, le=366)):
    p = {"days": days}
    win = "created_at >= now() - make_interval(days => %(days)s)"
    tot = conn.execute(
        f"""SELECT count(*) AS calls, coalesce(sum(input_tokens), 0) AS itok, coalesce(sum(output_tokens), 0) AS otok,
                   coalesce(sum(est_cost_usd), 0) AS cost FROM llm_usage WHERE {win}""", p).fetchone()
    by_team = conn.execute(
        f"""SELECT u.team_id::text AS team_id, t.name AS team_name, count(*) AS calls,
                   coalesce(sum(u.input_tokens), 0) AS itok, coalesce(sum(u.output_tokens), 0) AS otok,
                   coalesce(sum(u.est_cost_usd), 0) AS cost
            FROM llm_usage u LEFT JOIN teams t ON t.id = u.team_id
            WHERE u.{win} GROUP BY u.team_id, t.name ORDER BY cost DESC, calls DESC""", p).fetchall()
    skills_created = conn.execute(f"SELECT count(*) AS n FROM skills WHERE {win}", p).fetchone()["n"]
    runs = conn.execute(
        """SELECT count(*) AS n,
                  count(*) FILTER (WHERE status <> 'running') AS finished,
                  count(*) FILTER (WHERE status = 'succeeded') AS ok
           FROM runs WHERE started_at >= now() - make_interval(days => %(days)s)""", p).fetchone()
    return {
        "days": days,
        "llm_calls": tot["calls"],
        "input_tokens": int(tot["itok"]),
        "output_tokens": int(tot["otok"]),
        "est_cost_usd": round(float(tot["cost"]), 6),
        "by_team": [
            {"team": {"id": r["team_id"], "name": r["team_name"]} if r["team_id"] else None,
             "llm_calls": r["calls"], "input_tokens": int(r["itok"]), "output_tokens": int(r["otok"]),
             "est_cost_usd": round(float(r["cost"]), 6)}
            for r in by_team
        ],
        "skills_created": skills_created,
        "runs": runs["n"],
        "run_success_rate": round(runs["ok"] / runs["finished"], 4) if runs["finished"] else None,
    }


@router.get("/jobs")
def jobs(user: Admin, conn: Conn, status: str | None = Query(None), limit: int | None = Query(None),
         cursor: str | None = Query(None)):
    lim = svc.clamp_limit(limit)
    params: dict = {"lim": lim + 1}
    where = []
    if status:
        if status not in JOB_STATUSES:
            raise ApiError(422, "validation_error", f"status must be one of {', '.join(JOB_STATUSES)}")
        where.append("status = %(status)s")
        params["status"] = status
    c = svc.decode_cursor(cursor, 2)
    if c:
        where.append("(updated_at, id) < (%(c_ts)s::timestamptz, %(c_id)s::uuid)")
        params["c_ts"], params["c_id"] = c
    rows = conn.execute(
        f"""SELECT id, kind, payload, status, attempts, max_attempts, run_after, last_error, locked_by,
                   created_at, updated_at
            FROM jobs WHERE {' AND '.join(where) or 'TRUE'}
            ORDER BY updated_at DESC, id DESC LIMIT %(lim)s""", params).fetchall()
    nxt = svc.encode_cursor(rows[lim - 1]["updated_at"], rows[lim - 1]["id"]) if len(rows) > lim else None
    items = [
        {"id": str(r["id"]), "kind": r["kind"], "payload": r["payload"], "status": r["status"],
         "attempts": r["attempts"], "max_attempts": r["max_attempts"], "run_after": svc.iso(r["run_after"]),
         "last_error": r["last_error"], "created_at": svc.iso(r["created_at"]),
         "updated_at": svc.iso(r["updated_at"])}
        for r in rows[:lim]
    ]
    return {"items": items, "next_cursor": nxt}


@router.post("/jobs/{job_id}/retry")
def retry(job_id: str, user: Admin, conn: Conn):
    svc.require_uuid(job_id, "job")
    row = retry_job(conn, job_id)
    if not row:
        exists = conn.execute("SELECT status FROM jobs WHERE id = %s", (job_id,)).fetchone()
        if not exists:
            raise not_found("job")
        raise ApiError(409, "job_not_dead", f"only dead jobs can be retried (status is {exists['status']})")
    if row["kind"] == "generate_skill":
        conn.execute("UPDATE recordings SET status = 'received', error = NULL, updated_at = now() WHERE id = %s",
                     (row["payload"].get("recording_id"),))
    return {"id": row["id"], "status": "queued"}


# ------------------------------------------------------------------ relevance filter
_THRESHOLDS = (0.5, 0.6, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95)


@router.get("/filter/stats")
def filter_stats(request: Request, user: Admin, conn: Conn, days: int = Query(30, ge=1, le=366)):
    """How well the filter agrees with reviewers, plus a threshold table to tune drop/review cut-offs.
    'Positive' = the reviewer did NOT keep the event (it should have been dropped)."""
    p = {"days": days}
    win = "created_at >= now() - make_interval(days => %(days)s)"
    totals = conn.execute(
        f"""SELECT count(*) AS events,
                   count(*) FILTER (WHERE reviewed_at IS NOT NULL) AS reviewed,
                   count(*) FILTER (WHERE decision = 'drop') AS drop,
                   count(*) FILTER (WHERE decision = 'review') AS review,
                   count(*) FILTER (WHERE decision = 'keep') AS keep
            FROM filter_decisions WHERE {win}""", p).fetchone()
    matrix = conn.execute(
        f"""SELECT decision, final_keep, count(*) AS n FROM filter_decisions
            WHERE {win} AND reviewed_at IS NOT NULL GROUP BY decision, final_keep""", p).fetchall()
    by_reason = conn.execute(
        f"""SELECT reason, count(*) AS n,
                   count(*) FILTER (WHERE reviewed_at IS NOT NULL AND decision <> 'keep' AND final_keep) AS restored
            FROM filter_decisions WHERE {win} AND decision <> 'keep'
            GROUP BY reason ORDER BY n DESC""", p).fetchall()
    by_source = conn.execute(
        f"""SELECT source, count(*) AS n FROM filter_decisions WHERE {win} GROUP BY source ORDER BY n DESC""",
        p).fetchall()
    curve = []
    for t in _THRESHOLDS:
        r = conn.execute(
            f"""SELECT count(*) FILTER (WHERE p_drop >= %(t)s AND NOT final_keep) AS tp,
                       count(*) FILTER (WHERE p_drop >= %(t)s AND final_keep) AS fp,
                       count(*) FILTER (WHERE p_drop < %(t)s AND NOT final_keep) AS fn
                FROM filter_decisions WHERE {win} AND reviewed_at IS NOT NULL""", {**p, "t": t}).fetchone()
        tp, fp, fn = r["tp"], r["fp"], r["fn"]
        curve.append({"threshold": t, "flagged": tp + fp,
                      "precision": round(tp / (tp + fp), 4) if tp + fp else None,
                      "recall": round(tp / (tp + fn), 4) if tp + fn else None})
    cells = {(r["decision"], r["final_keep"]): r["n"] for r in matrix}
    wrongly_dropped = cells.get(("drop", True), 0)
    dropped_reviewed = wrongly_dropped + cells.get(("drop", False), 0)
    missed = cells.get(("keep", False), 0)
    kept_reviewed = missed + cells.get(("keep", True), 0)
    with_jev = conn.execute(
        """SELECT count(*) AS calls, coalesce(sum(est_cost_usd), 0) AS cost,
                  count(*) FILTER (WHERE NOT ok) AS partial
           FROM llm_usage WHERE purpose = 'filter' AND created_at >= now() - make_interval(days => %(days)s)""",
        p).fetchone()
    return {
        "days": days,
        "provider": request.app.state.settings.filter_provider,
        "events": totals["events"],
        "reviewed": totals["reviewed"],
        "decisions": {"keep": totals["keep"], "review": totals["review"], "drop": totals["drop"]},
        "matrix": [{"decision": d, "final_keep": k, "n": n} for (d, k), n in sorted(cells.items(), key=str)],
        "wrongly_dropped_rate": round(wrongly_dropped / dropped_reviewed, 4) if dropped_reviewed else None,
        "missed_rate": round(missed / kept_reviewed, 4) if kept_reviewed else None,
        "by_reason": [{"reason": r["reason"], "n": r["n"], "restored": r["restored"]} for r in by_reason],
        "by_source": [{"source": r["source"], "n": r["n"]} for r in by_source],
        "threshold_curve": curve,
        "jev": {"recordings": with_jev["calls"], "partial_failures": with_jev["partial"],
                "est_cost_usd": round(float(with_jev["cost"]), 6)},
    }


@router.get("/filter/export")
def filter_export(user: Admin, conn: Conn, days: int = Query(90, ge=1, le=366),
                  reviewed_only: bool = Query(True)):
    """Labelled examples as NDJSON (redacted events, the filter's prediction, the reviewer's verdict).
    Use it to calibrate thresholds, evaluate a new model, or train an in-house filter."""
    rows = conn.execute(
        f"""SELECT d.recording_id::text AS recording_id, d.seq, d.segment, d.event, d.decision, d.reason,
                   d.p_drop, d.source, d.model, d.final_keep, d.reviewed_version,
                   coalesce(r.intent, r.title_hint) AS goal
            FROM filter_decisions d JOIN recordings r ON r.id = d.recording_id
            WHERE d.created_at >= now() - make_interval(days => %(days)s)
              {"AND d.reviewed_at IS NOT NULL" if reviewed_only else ""}
            ORDER BY d.recording_id, d.seq LIMIT 200000""", {"days": days}).fetchall()

    def gen():
        for r in rows:
            yield json.dumps({**r, "p_drop": round(float(r["p_drop"]), 4)}, ensure_ascii=False, default=str) + "\n"

    return StreamingResponse(gen(), media_type="application/x-ndjson",
                             headers={"Content-Disposition": 'attachment; filename="filter-labels.ndjson"'})
