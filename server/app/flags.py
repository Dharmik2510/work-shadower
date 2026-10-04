"""Feature flags / kill switches (single-row app_flags table)."""
from __future__ import annotations

from typing import Any

import psycopg

_FIELDS = ("recording_enabled", "replay_enabled", "llm_enabled", "max_recording_minutes", "screenshot_policy",
           "filter_enabled", "filter_drop_threshold", "filter_review_threshold", "split_tasks_enabled")


def get_flags(conn: psycopg.Connection) -> dict[str, Any]:
    row = conn.execute(f"SELECT {', '.join(_FIELDS)} FROM app_flags WHERE id = 1").fetchone()
    out = {k: row[k] for k in _FIELDS}
    for k in ("filter_drop_threshold", "filter_review_threshold"):
        out[k] = round(float(out[k]), 4)
    return out


def update_flags(conn: psycopg.Connection, changes: dict[str, Any], user_id: str) -> dict[str, Any]:
    changes = {k: v for k, v in changes.items() if k in _FIELDS and v is not None}
    if "filter_drop_threshold" in changes or "filter_review_threshold" in changes:
        cur = get_flags(conn)
        drop = changes.get("filter_drop_threshold", cur["filter_drop_threshold"])
        review = changes.get("filter_review_threshold", cur["filter_review_threshold"])
        if review > drop:
            from .errors import ApiError

            raise ApiError(422, "validation_error", "filter_review_threshold must be <= filter_drop_threshold")
    if changes:
        sets = ", ".join(f"{k} = %({k})s" for k in changes)
        conn.execute(
            f"UPDATE app_flags SET {sets}, updated_by = %(uid)s, updated_at = now() WHERE id = 1",
            {**changes, "uid": user_id},
        )
    return get_flags(conn)


def effective_config(conn: psycopg.Connection, llm_provider_enabled: bool) -> dict[str, Any]:
    """Flags as seen by clients: llm_enabled is false when no LLM provider is configured."""
    f = get_flags(conn)
    f["llm_enabled"] = bool(f["llm_enabled"] and llm_provider_enabled)
    return f
