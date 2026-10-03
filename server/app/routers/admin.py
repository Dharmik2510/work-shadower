"""Admin: flags (kill switches), usage, job ops."""
from __future__ import annotations

from fastapi import APIRouter, Query

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
