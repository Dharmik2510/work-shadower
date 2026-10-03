"""Feature flags / kill switches (single-row app_flags table)."""
from __future__ import annotations

from typing import Any

import psycopg

_FIELDS = ("recording_enabled", "replay_enabled", "llm_enabled", "max_recording_minutes", "screenshot_policy")


def get_flags(conn: psycopg.Connection) -> dict[str, Any]:
    row = conn.execute(f"SELECT {', '.join(_FIELDS)} FROM app_flags WHERE id = 1").fetchone()
    return {k: row[k] for k in _FIELDS}


def update_flags(conn: psycopg.Connection, changes: dict[str, Any], user_id: str) -> dict[str, Any]:
    changes = {k: v for k, v in changes.items() if k in _FIELDS and v is not None}
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
