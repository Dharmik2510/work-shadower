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
from .filtering import RelevanceFilter
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
    filter: RelevanceFilter | None = None

    def __post_init__(self):
        if self.filter is None:
            self.filter = RelevanceFilter(self.settings)
        if not self.worker_id:
            self.worker_id = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"


class Reschedule(Exception):
    """Raised by a handler to run the job again later without consuming an attempt (polling)."""

    def __init__(self, delay_seconds: float, note: str = ""):
        super().__init__(note or f"reschedule in {delay_seconds}s")
        self.delay_seconds = delay_seconds


Handler = Callable[[WorkerContext, dict], None]
HANDLERS: dict[str, Handler] = {}


def handler(kind: str) -> Callable[[Handler], Handler]:
    def deco(fn: Handler) -> Handler:
        HANDLERS[kind] = fn
        return fn

    return deco


def enqueue(conn: psycopg.Connection, kind: str, payload: dict[str, Any], max_attempts: int | None = None,
            delay_seconds: float = 0.0) -> str:
    row = conn.execute(
        "INSERT INTO jobs (kind, payload, max_attempts, run_after) "
        "VALUES (%s, %s, coalesce(%s, 5), now() + make_interval(secs => %s)) RETURNING id::text",
        (kind, Jsonb(payload), max_attempts, delay_seconds),
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
    except Reschedule as r:
        with ctx.pool.connection() as conn:
            conn.execute(
                """UPDATE jobs SET status = 'queued', attempts = greatest(attempts - 1, 0), locked_at = NULL,
                          locked_by = NULL, last_error = NULL, run_after = now() + make_interval(secs => %s),
                          updated_at = now()
                   WHERE id = %s AND locked_by = %s""",
                (r.delay_seconds, job["id"], ctx.worker_id),
            )
        log.info("job rescheduled", extra={**log_extra, "delay_s": r.delay_seconds})
        return True
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

    rec_id = payload["recording_id"]
    with ctx.pool.connection() as conn:
        rec = conn.execute(
            """UPDATE recordings SET status = 'processing', error = NULL, updated_at = now()
               WHERE id = %s AND skill_id IS NULL
               RETURNING id::text AS id, user_id::text AS user_id, events, title_hint, intent, llm_batch_id""",
            (rec_id,),
        ).fetchone()
        if not rec:
            return  # deleted, or already turned into a skill (idempotent)
        if rec["llm_batch_id"]:
            return  # a batch is pending; its poll job finishes the recording
        team_id = _team_of(conn, rec["user_id"])
        flags = get_flags(conn)
        allow_llm = bool(flags["llm_enabled"]) and usage.within_budget(
            conn, rec["user_id"], ctx.settings.llm_daily_calls_per_user
        )
        stored = _load_decisions(conn, rec_id)

    cleaned = skillgen.cleanup(rec["events"])
    if stored is None:  # first attempt: run the filter and persist it (retries reuse it)
        decisions, segments = _run_filter(ctx, rec, team_id, flags, cleaned)
    else:
        decisions, segments = stored
    by_seq = {e["seq"]: e for e in cleaned}
    seg_events = [[by_seq[q] for q in seg if q in by_seq] for seg in segments] or [cleaned]
    n = len(seg_events)

    # ---- async batch path (cheaper; drafts appear when the batch ends)
    if allow_llm and ctx.settings.llm_batch_mode and ctx.llm.enabled and ctx.llm.supports_batch:
        reqs = []
        for i, evs in enumerate(seg_events):
            if 0 < len(evs) <= skillgen.MAX_LLM_EVENTS:
                hint = rec["title_hint"] if n == 1 else None
                system, prompt, schema = skillgen.llm_request(evs, hint, rec["intent"], decisions, (i + 1, n))
                reqs.append((f"seg-{i}", ctx.llm.params(system, prompt, schema=schema)))
        if reqs:
            try:
                batch_id = ctx.llm.submit_batch(reqs)
            except Exception as e:  # noqa: BLE001 - fall back to the synchronous path
                log.warning("batch submit failed; generating synchronously", extra={"error": str(e)})
            else:
                with ctx.pool.connection() as conn:
                    conn.execute("UPDATE recordings SET llm_batch_id = %s, llm_batch_submitted_at = now(), "
                                 "updated_at = now() WHERE id = %s", (batch_id, rec_id))
                    enqueue(conn, "poll_llm_batch", {"recording_id": rec_id, "batch_id": batch_id},
                            max_attempts=ctx.settings.job_max_attempts,
                            delay_seconds=ctx.settings.llm_batch_poll_seconds)
                log.info("llm batch submitted", extra={"recording_id": rec_id, "batch_id": batch_id,
                                                       "requests": len(reqs)})
                return

    # ---- synchronous path
    def on_usage(itok: int, otok: int, ok: bool) -> None:
        with ctx.pool.connection() as c:
            usage.record(c, user_id=rec["user_id"], team_id=team_id, purpose="skillgen", provider=ctx.llm,
                         model=ctx.llm.model, input_tokens=itok, output_tokens=otok,
                         est_cost_usd=ctx.llm.estimate_cost(itok, otok), ok=ok)

    results = []
    for i, evs in enumerate(seg_events):
        hint = rec["title_hint"] if n == 1 else None
        results.append(skillgen.generate(evs, hint, ctx.llm, allow_llm=allow_llm, on_usage=on_usage,
                                         intent=rec["intent"], decisions=decisions, cleaned=True, part=(i + 1, n)))
    _create_skills(ctx, rec, team_id, [r.content for r in results])
    log.info("skill generated", extra={"recording_id": rec_id, "skills": len(results),
                                        "methods": [r.method for r in results],
                                        "llm_errors": [r.error for r in results if r.error]})


@handler("poll_llm_batch")
def _poll_llm_batch(ctx: WorkerContext, payload: dict) -> None:
    from datetime import datetime, timezone

    from . import skillgen, usage
    from .llm import LLMError

    rec_id, batch_id = payload["recording_id"], payload["batch_id"]
    with ctx.pool.connection() as conn:
        rec = conn.execute(
            """SELECT id::text AS id, user_id::text AS user_id, events, title_hint, intent, skill_id,
                      llm_batch_id, llm_batch_submitted_at FROM recordings WHERE id = %s""", (rec_id,)).fetchone()
        if not rec or rec["skill_id"] or rec["llm_batch_id"] != batch_id:
            return
        team_id = _team_of(conn, rec["user_id"])
        stored = _load_decisions(conn, rec_id)
    decisions, segments = stored if stored else ({}, [])
    cleaned = skillgen.cleanup(rec["events"])
    by_seq = {e["seq"]: e for e in cleaned}
    seg_events = [[by_seq[q] for q in seg if q in by_seq] for seg in segments] or [cleaned]
    n = len(seg_events)

    batch = ctx.llm.get_batch(batch_id)
    results: dict = {}
    if batch.get("processing_status") != "ended":
        age_h = (datetime.now(timezone.utc) - rec["llm_batch_submitted_at"]).total_seconds() / 3600
        if age_h < ctx.settings.llm_batch_max_hours:
            raise Reschedule(ctx.settings.llm_batch_poll_seconds)
        ctx.llm.cancel_batch(batch_id)
        log.warning("llm batch expired; using heuristic drafts", extra={"recording_id": rec_id})
    else:
        results = ctx.llm.batch_results(batch)

    contents = []
    for i, evs in enumerate(seg_events):
        hint = rec["title_hint"] if n == 1 else None
        res = results.get(f"seg-{i}")
        content = None
        if isinstance(res, LLMError) or res is None:
            if res is not None:
                with ctx.pool.connection() as c:
                    usage.record(c, user_id=rec["user_id"], team_id=team_id, purpose="skillgen",
                                 provider=ctx.llm, model=ctx.llm.model, input_tokens=0, output_tokens=0,
                                 est_cost_usd=0, ok=False)
        else:
            with ctx.pool.connection() as c:
                usage.record(c, user_id=rec["user_id"], team_id=team_id, purpose="skillgen", provider=ctx.llm,
                             model=ctx.llm.model, input_tokens=res.input_tokens, output_tokens=res.output_tokens,
                             est_cost_usd=round(ctx.llm.estimate_cost(res.input_tokens, res.output_tokens) * 0.5, 6),
                             ok=True)
            try:
                content = skillgen.from_llm_output(res.data, evs, hint, rec["intent"], decisions,
                                                   use_intent_title=n == 1)
            except ValueError as e:
                log.warning("batch output unusable; heuristic", extra={"error": str(e)})
        if content is None:
            content = skillgen.heuristic_skill(evs, hint, cleaned=True, intent=rec["intent"] if n == 1 else None,
                                               decisions=decisions, use_intent_title=n == 1)
        contents.append(content)
    _create_skills(ctx, rec, team_id, contents)


def _team_of(conn: psycopg.Connection, user_id: str) -> str | None:
    team = conn.execute(
        "SELECT t.id::text AS id FROM team_members m JOIN teams t ON t.id = m.team_id "
        "WHERE m.user_id = %s ORDER BY t.name LIMIT 1",
        (user_id,),
    ).fetchone()
    return team["id"] if team else None


def _load_decisions(conn: psycopg.Connection, rec_id: str) -> tuple[dict, list[list[int]]] | None:
    rows = conn.execute(
        """SELECT seq, segment, decision, reason, p_drop, source FROM filter_decisions
           WHERE recording_id = %s ORDER BY seq""", (rec_id,)).fetchall()
    if not rows:
        return None
    summary = conn.execute("SELECT filter_summary FROM recordings WHERE id = %s", (rec_id,)).fetchone()
    extra = ((summary or {}).get("filter_summary") or {}).get("irreversible_seqs") or []
    decisions = {r["seq"]: {"seq": r["seq"], "decision": r["decision"], "reason": r["reason"],
                            "p_drop": float(r["p_drop"]), "source": r["source"],
                            "p_irreversible": 1.0 if r["seq"] in extra else None} for r in rows}
    segs: dict[int, list[int]] = {}
    for r in rows:
        segs.setdefault(r["segment"], []).append(r["seq"])
    return decisions, [segs[k] for k in sorted(segs)]


def _run_filter(ctx: WorkerContext, rec: dict, team_id: str | None, flags: dict,
                cleaned: list[dict]) -> tuple[dict, list[list[int]]]:
    from dataclasses import asdict

    from . import usage
    from .filtering import Thresholds, decisions_to_rows, keep_all
    from .skillgen import is_generic_hint

    goal = rec["intent"] or (None if is_generic_hint(rec["title_hint"]) else rec["title_hint"])
    if flags.get("filter_enabled", True) and cleaned:
        th = Thresholds(drop=flags["filter_drop_threshold"], review=flags["filter_review_threshold"],
                        split=ctx.settings.filter_split_threshold,
                        min_segment_steps=ctx.settings.filter_min_segment_steps)
        try:
            res = ctx.filter.run(cleaned, goal, th, split_tasks=bool(flags.get("split_tasks_enabled", True)))
        except Exception as e:  # noqa: BLE001 - the filter must never block a recording
            log.exception("relevance filter crashed; keeping every step")
            res = keep_all(cleaned)
            res.notes.append(f"filter error: {type(e).__name__}")
    else:
        res = keep_all(cleaned)
    rows = decisions_to_rows(res, cleaned)
    summary = res.summary()
    summary["irreversible_seqs"] = [d.seq for d in res.decisions.values() if (d.p_irreversible or 0) >= 0.5]
    with ctx.pool.connection() as conn:
        if res.jev_calls or res.jev_failures:
            usage.record(conn, user_id=rec["user_id"], team_id=team_id, purpose="filter", provider="jev",
                         model=res.model or "", input_tokens=res.input_tokens, output_tokens=res.output_tokens,
                         est_cost_usd=ctx.filter.estimate_cost(res), ok=res.jev_failures == 0)
        conn.execute("DELETE FROM filter_decisions WHERE recording_id = %s", (rec["id"],))
        if rows:
            with conn.cursor() as cur:
                cur.executemany(
                    """INSERT INTO filter_decisions (recording_id, segment, seq, event, decision, reason, p_drop,
                                                     source, model)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    [(rec["id"], r["segment"], r["seq"], Jsonb(r["event"]), r["decision"], r["reason"][:200],
                      r["p_drop"], r["source"], res.model) for r in rows],
                )
        conn.execute("UPDATE recordings SET filter_summary = %s, updated_at = now() WHERE id = %s",
                     (Jsonb(summary), rec["id"]))
    log.info("filtered", extra={"recording_id": rec["id"], **{k: summary[k] for k in ("source", "counts", "segments")}})
    return {seq: asdict(d) for seq, d in res.decisions.items()}, res.segments or [[e["seq"] for e in cleaned]]


def _create_skills(ctx: WorkerContext, rec: dict, team_id: str | None, contents: list[dict]) -> None:
    from .skills import create_skill

    rec_id = rec["id"]
    with ctx.pool.connection() as conn:
        locked = conn.execute("SELECT skill_id FROM recordings WHERE id = %s FOR UPDATE", (rec_id,)).fetchone()
        if not locked or locked["skill_id"]:
            return
        ids = []
        for i, content in enumerate(contents):
            sid = create_skill(conn, owner_id=rec["user_id"], content=content, team_id=team_id,
                               visibility="team" if team_id else "private", source_recording_id=rec_id)
            ids.append(sid)
            conn.execute("UPDATE filter_decisions SET skill_id = %s WHERE recording_id = %s AND segment = %s",
                         (sid, rec_id, i))
        conn.execute(
            """UPDATE recordings SET status = 'ready', skill_id = %s, skill_ids = %s::uuid[], error = NULL,
                      llm_batch_id = NULL, updated_at = now() WHERE id = %s""",
            (ids[0], ids, rec_id),
        )


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
