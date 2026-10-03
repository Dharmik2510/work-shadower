"""Recordings: idempotent upload of a redacted event log, then async skill generation."""
from __future__ import annotations

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import JSONResponse
from psycopg.types.json import Jsonb

from ..auth import Conn, User
from ..errors import ApiError, not_found
from ..flags import get_flags
from ..jobs import enqueue
from ..models import RecordingCreate
from ..redact import redact_events
from ..skills import clamp_limit, decode_cursor, encode_cursor, iso, require_uuid

router = APIRouter()

MAX_EVENTS = 5000


def _view(r: dict) -> dict:
    return {
        "id": str(r["id"]),
        "status": r["status"],
        "error": r["error"],
        "skill_id": str(r["skill_id"]) if r["skill_id"] else None,
        "event_count": r["event_count"],
        "title_hint": r["title_hint"],
        "created_at": iso(r["created_at"]),
    }


def require_idempotency_key(key: str | None) -> str:
    if not key or not key.strip():
        raise ApiError(400, "idempotency_key_required", "Idempotency-Key header is required")
    if len(key) > 200:
        raise ApiError(400, "invalid_idempotency_key", "Idempotency-Key is too long")
    return key.strip()


@router.post("/recordings", status_code=202)
def create_recording(
    body: RecordingCreate,
    request: Request,
    user: User,
    conn: Conn,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
):
    key = require_idempotency_key(idempotency_key)
    existing = conn.execute(
        "SELECT id::text AS id, status FROM recordings WHERE user_id = %s AND idempotency_key = %s", (user.id, key)
    ).fetchone()
    if existing:
        return JSONResponse({"id": existing["id"], "status": existing["status"]}, status_code=200)
    if not get_flags(conn)["recording_enabled"]:
        raise ApiError(403, "recording_disabled", "recording is currently disabled by an administrator")
    if len(body.events) > MAX_EVENTS:
        raise ApiError(413, "too_many_events", f"a recording may contain at most {MAX_EVENTS} events")
    if body.ended_at < body.started_at:
        raise ApiError(422, "validation_error", "ended_at must not be before started_at")

    events = redact_events([e.model_dump(mode="json", exclude_none=False) for e in body.events])
    shas = sorted({e["screenshot_sha256"] for e in events if e.get("screenshot_sha256")})
    row = conn.execute(
        """INSERT INTO recordings (user_id, idempotency_key, title_hint, started_at, ended_at, client, events,
                                   event_count, asset_shas)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (user_id, idempotency_key) DO NOTHING
           RETURNING id::text AS id, status""",
        (user.id, key, body.title_hint, body.started_at, body.ended_at, Jsonb(body.client.model_dump()),
         Jsonb(events), len(events), shas),
    ).fetchone()
    if row is None:  # lost a race with a concurrent request carrying the same key
        row = conn.execute(
            "SELECT id::text AS id, status FROM recordings WHERE user_id = %s AND idempotency_key = %s", (user.id, key)
        ).fetchone()
        return JSONResponse({"id": row["id"], "status": row["status"]}, status_code=200)
    enqueue(conn, "generate_skill", {"recording_id": row["id"]}, request.app.state.settings.job_max_attempts)
    return {"id": row["id"], "status": "received"}


@router.get("/recordings")
def list_recordings(user: User, conn: Conn, limit: int | None = Query(None), cursor: str | None = Query(None)):
    lim = clamp_limit(limit)
    params: dict = {"uid": user.id, "lim": lim + 1}
    where = "user_id = %(uid)s"
    c = decode_cursor(cursor, 2)
    if c:
        where += " AND (created_at, id) < (%(c_ts)s::timestamptz, %(c_id)s::uuid)"
        params["c_ts"], params["c_id"] = c
    rows = conn.execute(
        f"""SELECT id, status, error, skill_id, event_count, title_hint, created_at FROM recordings
            WHERE {where} ORDER BY created_at DESC, id DESC LIMIT %(lim)s""",
        params,
    ).fetchall()
    nxt = encode_cursor(rows[lim - 1]["created_at"], rows[lim - 1]["id"]) if len(rows) > lim else None
    return {"items": [_view(r) for r in rows[:lim]], "next_cursor": nxt}


@router.get("/recordings/{recording_id}")
def get_recording(recording_id: str, user: User, conn: Conn):
    require_uuid(recording_id, "recording")
    row = conn.execute(
        """SELECT id, user_id::text AS user_id, status, error, skill_id, event_count, title_hint, created_at
           FROM recordings WHERE id = %s""",
        (recording_id,),
    ).fetchone()
    if not row or (row["user_id"] != user.id and not user.is_admin):
        raise not_found("recording")
    return _view(row)
