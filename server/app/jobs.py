"""Postgres-backed job queue.

* claim: `SELECT ... FOR UPDATE SKIP LOCKED` (safe with many workers)
* failure: exponential backoff with jitter until `max_attempts`, then status 'dead'
* visibility timeout: a 'running' job whose lock is older than JOB_VISIBILITY_TIMEOUT_SECONDS
  (worker crashed) is reclaimed by the next poll and counts as a failed attempt
"""
from __future__ import annotations

import logging
import os
import random
import socket
import uuid
from dataclasses import dataclass
from typing import Any, Callable

import psycopg
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from .config import Settings
from .llm import Embedder, LLMProvider

log = logging.getLogger("app.jobs")

JOB_STATUSES = ("queued", "running", "succeeded", "dead")


@dataclass
class WorkerContext:
    pool: ConnectionPool
    settings: Settings
    llm: LLMProvider
    embedder: Embedder
    vector_enabled: bool
    worker_id: str = ""

    def __post_init__(self):
        if not self.worker_id:
            self.worker_id = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"


Handler = Callable[[WorkerContext, dict], None]
HANDLERS: dict[str, Handler] = {}


def handler(kind: str) -> Callable[[Handler], Handler]:
    def deco(fn: Handler) -> Handler:
        HANDLERS[kind] = fn
        return fn

    return deco


def enqueue(conn: psycopg.Connection, kind: str, payload: dict[str, Any], max_attempts: int | None = None) -> str:
    row = conn.execute(
        "INSERT INTO jobs (kind, payload, max_attempts) VALUES (%s, %s, coalesce(%s, 5)) RETURNING id::text",
        (kind, Jsonb(payload), max_attempts),
    ).fetchone()
    return row["id"]


def backoff_seconds(attempts: int, base: float, cap: float) -> float:
    delay = min(base * (2 ** max(attempts - 1, 0)), cap)
    return delay * random.uniform(0.8, 1.2)


def claim(conn: psycopg.Connection, worker_id: str, visibility_timeout: int) -> dict | None:
    return conn.execute(
        """
        WITH c AS (
            SELECT id FROM jobs
            WHERE (status = 'queued' AND run_after <= now())
               OR (status = 'running' AND locked_at < now() - make_interval(secs => %(vt)s))
            ORDER BY run_after, created_at
            FOR UPDATE SKIP LOCKED
            LIMIT 1)
        UPDATE jobs j SET status = 'running', attempts = j.attempts + 1, locked_at = now(),
                          locked_by = %(w)s, updated_at = now()
        FROM c WHERE j.id = c.id
        RETURNING j.id::text AS id, j.kind, j.payload, j.attempts, j.max_attempts
        """,
        {"vt": visibility_timeout, "w": worker_id},
    ).fetchone()


def mark_succeeded(conn: psycopg.Connection, job_id: str, worker_id: str) -> None:
    conn.execute(
        "UPDATE jobs SET status = 'succeeded', locked_at = NULL, last_error = NULL, updated_at = now() "
        "WHERE id = %s AND locked_by = %s AND status = 'running'",
        (job_id, worker_id),
    )


def mark_failed(conn: psycopg.Connection, job: dict, worker_id: str, error: str, settings: Settings) -> str:
    """Returns the new status ('queued' for retry, or 'dead')."""
    if job["attempts"] >= job["max_attempts"]:
        status, delay = "dead", 0.0
    else:
        status = "queued"
        delay = backoff_seconds(job["attempts"], settings.job_backoff_base_seconds, settings.job_backoff_max_seconds)
    conn.execute(
        """UPDATE jobs SET status = %s, last_error = %s, locked_at = NULL, locked_by = NULL,
                  run_after = now() + make_interval(secs => %s), updated_at = now()
           WHERE id = %s AND locked_by = %s""",
        (status, error[:4000], delay, job["id"], worker_id),
    )
    return status


def retry_job(conn: psycopg.Connection, job_id: str) -> dict | None:
    return conn.execute(
        """UPDATE jobs SET status = 'queued', attempts = 0, run_after = now(), locked_at = NULL,
                  locked_by = NULL, updated_at = now()
           WHERE id = %s AND status = 'dead'
           RETURNING id::text AS id, kind, payload""",
        (job_id,),
    ).fetchone()


def run_once(ctx: WorkerContext) -> bool:
    """Claim and process at most one job. Returns True if a job was processed."""
    with ctx.pool.connection() as conn:
        job = claim(conn, ctx.worker_id, ctx.settings.job_visibility_timeout_seconds)
    if not job:
        return False
    log_extra = {"job_id": job["id"], "kind": job["kind"], "attempt": job["attempts"]}
    if job["attempts"] > job["max_attempts"]:  # reclaimed after a crash on its final attempt
        with ctx.pool.connection() as conn:
            mark_failed(conn, job, ctx.worker_id, "exceeded max attempts (worker timeout)", ctx.settings)
            _on_dead(conn, job, "exceeded max attempts (worker timeout)")
        log.error("job dead after timeout", extra=log_extra)
        return True
    fn = HANDLERS.get(job["kind"])
    try:
        if fn is None:
            raise RuntimeError(f"no handler for job kind {job['kind']!r}")
        fn(ctx, job["payload"])
    except Exception as e:  # noqa: BLE001 - any handler failure is retried
        err = f"{type(e).__name__}: {e}"
        with ctx.pool.connection() as conn:
            status = mark_failed(conn, job, ctx.worker_id, err, ctx.settings)
            if status == "dead":
                _on_dead(conn, job, err)
        log.warning("job failed", extra={**log_extra, "error": err, "new_status": status})
        return True
    with ctx.pool.connection() as conn:
        mark_succeeded(conn, job["id"], ctx.worker_id)
    log.info("job succeeded", extra=log_extra)
    return True


def run_until_empty(ctx: WorkerContext, max_jobs: int = 1000) -> int:
    n = 0
    while n < max_jobs and run_once(ctx):
        n += 1
    return n


def _on_dead(conn: psycopg.Connection, job: dict, error: str) -> None:
    if job["kind"] == "generate_skill":
        conn.execute(
            "UPDATE recordings SET status = 'failed', error = %s, updated_at = now() WHERE id = %s",
            (error[:2000], job["payload"].get("recording_id")),
        )


# ====================================================================== handlers
@handler("generate_skill")
def _generate_skill(ctx: WorkerContext, payload: dict) -> None:
    from . import skillgen, usage
    from .flags import get_flags
    from .skills import create_skill

    rec_id = payload["recording_id"]
    with ctx.pool.connection() as conn:
        rec = conn.execute(
            """UPDATE recordings SET status = 'processing', error = NULL, updated_at = now()
               WHERE id = %s AND skill_id IS NULL
               RETURNING id::text AS id, user_id::text AS user_id, events, title_hint""",
            (rec_id,),
        ).fetchone()
        if not rec:
            return  # deleted, or already turned into a skill (idempotent)
        team = conn.execute(
            "SELECT t.id::text AS id FROM team_members m JOIN teams t ON t.id = m.team_id "
            "WHERE m.user_id = %s ORDER BY t.name LIMIT 1",
            (rec["user_id"],),
        ).fetchone()
        team_id = team["id"] if team else None
        allow_llm = bool(get_flags(conn)["llm_enabled"]) and usage.within_budget(
            conn, rec["user_id"], ctx.settings.llm_daily_calls_per_user
        )

    def on_usage(itok: int, otok: int, ok: bool) -> None:
        with ctx.pool.connection() as c:
            usage.record(c, user_id=rec["user_id"], team_id=team_id, purpose="skillgen", provider=ctx.llm,
                         model=ctx.llm.model, input_tokens=itok, output_tokens=otok,
                         est_cost_usd=ctx.llm.estimate_cost(itok, otok), ok=ok)

    result = skillgen.generate(rec["events"], rec["title_hint"], ctx.llm, allow_llm=allow_llm, on_usage=on_usage)

    with ctx.pool.connection() as conn:
        locked = conn.execute("SELECT skill_id FROM recordings WHERE id = %s FOR UPDATE", (rec_id,)).fetchone()
        if not locked or locked["skill_id"]:
            return
        skill_id = create_skill(
            conn, owner_id=rec["user_id"], content=result.content, team_id=team_id,
            visibility="team" if team_id else "private", source_recording_id=rec_id,
        )
        conn.execute(
            "UPDATE recordings SET status = 'ready', skill_id = %s, error = NULL, updated_at = now() WHERE id = %s",
            (skill_id, rec_id),
        )
    log.info("skill generated", extra={"recording_id": rec_id, "skill_id": skill_id, "method": result.method,
                                        "llm_error": result.error})


@handler("embed_skill")
def _embed_skill(ctx: WorkerContext, payload: dict) -> None:
    from .llm import vector_literal
    from .skills import embedding_text

    if not (ctx.vector_enabled and ctx.embedder.enabled):
        return
    with ctx.pool.connection() as conn:
        row = conn.execute(
            """SELECT v.content, s.owner_id::text AS owner_id, s.team_id::text AS team_id
               FROM skill_versions v JOIN skills s ON s.id = v.skill_id
               WHERE v.skill_id = %s AND v.version = %s AND s.current_version = v.version""",
            (payload["skill_id"], payload["version"]),
        ).fetchone()
    if not row:
        return  # superseded by a newer publish
    vec = ctx.embedder.embed([embedding_text(row["content"])])[0]
    with ctx.pool.connection() as conn:
        conn.execute(
            "UPDATE skills SET embedding = %s::vector WHERE id = %s AND current_version = %s",
            (vector_literal(vec), payload["skill_id"], payload["version"]),
        )
