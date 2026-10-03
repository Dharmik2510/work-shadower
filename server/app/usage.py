"""LLM usage accounting and the per-user daily call budget."""
from __future__ import annotations

import psycopg

from .llm import LLMProvider


def calls_today(conn: psycopg.Connection, user_id: str | None) -> int:
    if not user_id:
        return 0
    row = conn.execute(
        "SELECT count(*) AS n FROM llm_usage WHERE user_id = %s "
        "AND created_at >= date_trunc('day', now() AT TIME ZONE 'utc') AT TIME ZONE 'utc'",
        (user_id,),
    ).fetchone()
    return int(row["n"])


def within_budget(conn: psycopg.Connection, user_id: str | None, daily_limit: int) -> bool:
    return calls_today(conn, user_id) < daily_limit


def record(
    conn: psycopg.Connection,
    *,
    user_id: str | None,
    team_id: str | None,
    purpose: str,
    provider: LLMProvider | str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    est_cost_usd: float,
    ok: bool,
) -> None:
    conn.execute(
        """INSERT INTO llm_usage (user_id, team_id, purpose, provider, model, input_tokens, output_tokens, est_cost_usd, ok)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (user_id, team_id, purpose, provider if isinstance(provider, str) else provider.name, model,
         input_tokens, output_tokens, est_cost_usd, ok),
    )
