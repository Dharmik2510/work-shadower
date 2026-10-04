"""Skill persistence, serialization, the central visibility predicate, publish and search.

VISIBILITY MODEL (single source of truth — every skill query goes through
`visibility_sql`): a user can see a skill iff
    they are an admin, OR they own it, OR
    status = 'published' AND (visibility = 'org'
                               OR (visibility = 'team' AND team_id IN user's teams))
So drafts-never-published and archived skills are owner/admin only, and `private`
skills are owner/admin only.
"""
from __future__ import annotations

import base64
import json
import re
from datetime import datetime, timezone
from typing import Any

import psycopg

from .auth import CurrentUser
from .errors import ApiError, not_found
from .models import SkillContent


# ------------------------------------------------------------------ helpers
def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def encode_cursor(*parts: Any) -> str:
    raw = json.dumps([p.isoformat() if isinstance(p, datetime) else str(p) for p in parts])
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(cursor: str | None, n: int) -> list[str] | None:
    if not cursor:
        return None
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        parts = json.loads(raw)
        if not isinstance(parts, list) or len(parts) != n:
            raise ValueError
        return [str(p) for p in parts]
    except (ValueError, json.JSONDecodeError):
        raise ApiError(400, "invalid_cursor", "cursor is malformed") from None


def clamp_limit(limit: int | None) -> int:
    if limit is None:
        return 20
    return max(1, min(int(limit), 100))


_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def valid_uuid(v: str | None) -> bool:
    return bool(v and _UUID_RE.match(v))


def require_uuid(v: str, what: str) -> str:
    if not valid_uuid(v):
        raise not_found(what)
    return v


def shas_in(content: dict | None) -> list[str]:
    if not content:
        return []
    return sorted({s.get("screenshot_sha256") for s in content.get("steps") or [] if s.get("screenshot_sha256")})


# ------------------------------------------------------------------ visibility
def visibility_sql(user: CurrentUser, alias: str = "s") -> tuple[str, dict[str, Any]]:
    """THE visibility predicate. Returns (sql, params) using named params."""
    if user.is_admin:
        return "TRUE", {}
    sql = (
        f"({alias}.owner_id = %(vis_uid)s::uuid OR ({alias}.status = 'published' AND ("
        f"{alias}.visibility = 'org' OR ({alias}.visibility = 'team' AND {alias}.team_id = ANY(%(vis_team_ids)s::uuid[])))))"
    )
    return sql, {"vis_uid": user.id, "vis_team_ids": user.team_ids}


def can_edit(user: CurrentUser, row: dict) -> bool:
    return user.is_admin or str(row["owner_id"]) == user.id


# ------------------------------------------------------------------ queries
_BASE_SELECT = """
SELECT s.id, s.owner_id, s.team_id, s.visibility, s.status, s.current_version, s.draft,
       s.source_recording_id, s.created_at, s.updated_at, s.run_count, s.run_success_count, s.last_run_at,
       o.name AS owner_name, o.email AS owner_email, t.name AS team_name,
       pv.content AS published
FROM skills s
JOIN users o ON o.id = s.owner_id
LEFT JOIN teams t ON t.id = s.team_id
LEFT JOIN skill_versions pv ON pv.skill_id = s.id AND pv.version = s.current_version
"""


def get_visible(conn: psycopg.Connection, user: CurrentUser, skill_id: str, for_update: bool = False) -> dict:
    require_uuid(skill_id, "skill")
    vis, params = visibility_sql(user)
    lock = " FOR UPDATE OF s" if for_update else ""
    row = conn.execute(f"{_BASE_SELECT} WHERE s.id = %(id)s AND {vis}{lock}", {**params, "id": skill_id}).fetchone()
    if not row:
        raise not_found("skill")
    return row


def get_editable(conn: psycopg.Connection, user: CurrentUser, skill_id: str) -> dict:
    row = get_visible(conn, user, skill_id, for_update=True)
    if not can_edit(user, row):
        raise ApiError(403, "forbidden", "only the owner or an admin can change this skill")
    return row


def _health(row: dict) -> dict:
    runs = row["run_count"] or 0
    return {
        "runs": runs,
        "success_rate": round(row["run_success_count"] / runs, 4) if runs else None,
        "last_run_at": iso(row["last_run_at"]),
    }


def _owner(row: dict) -> dict:
    return {"id": str(row["owner_id"]), "name": row["owner_name"], "email": row["owner_email"]}


def _team(row: dict) -> dict | None:
    return {"id": str(row["team_id"]), "name": row["team_name"]} if row["team_id"] else None


def serialize_skill(row: dict, user: CurrentUser) -> dict:
    return {
        "id": str(row["id"]),
        "owner": _owner(row),
        "team": _team(row),
        "visibility": row["visibility"],
        "status": row["status"],
        "current_version": row["current_version"],
        "draft": row["draft"] if can_edit(user, row) else None,
        "published": row["published"],
        "source_recording_id": str(row["source_recording_id"]) if row["source_recording_id"] else None,
        "created_at": iso(row["created_at"]),
        "updated_at": iso(row["updated_at"]),
        "health": _health(row),
    }


def serialize_summary(row: dict, user: CurrentUser) -> dict:
    content = row["published"] or (row["draft"] if can_edit(user, row) else None) or {}
    return {
        "id": str(row["id"]),
        "title": content.get("title", ""),
        "goal": content.get("goal", ""),
        "owner": _owner(row),
        "team": _team(row),
        "visibility": row["visibility"],
        "status": row["status"],
        "tags": content.get("tags", []),
        "apps": content.get("apps", []),
        "current_version": row["current_version"],
        "updated_at": iso(row["updated_at"]),
        "health": _health(row),
    }


def list_skills(
    conn: psycopg.Connection,
    user: CurrentUser,
    *,
    q: str | None,
    team_id: str | None,
    status: str | None,
    mine: bool,
    limit: int,
    cursor: str | None,
) -> dict:
    vis, params = visibility_sql(user)
    where = [vis]
    if team_id:
        if not valid_uuid(team_id):
            raise ApiError(422, "validation_error", "team_id must be a UUID")
        where.append("s.team_id = %(team_id)s")
        params["team_id"] = team_id
    if status:
        if status not in ("draft", "published", "archived"):
            raise ApiError(422, "validation_error", "status must be draft|published|archived")
        where.append("s.status = %(status)s")
        params["status"] = status
    if mine:
        where.append("s.owner_id = %(me)s")
        params["me"] = user.id
    if q and q.strip():
        tsq = to_prefix_tsquery(q)
        where.append(
            "((s.search_tsv @@ to_tsquery('english', %(tsq)s)) OR "
            "coalesce(pv.content->>'title', s.draft->>'title', '') ILIKE %(qlike)s)"
            if tsq else "coalesce(pv.content->>'title', s.draft->>'title', '') ILIKE %(qlike)s"
        )
        params["tsq"] = tsq
        params["qlike"] = "%" + q.strip().replace("%", r"\%").replace("_", r"\_") + "%"
    c = decode_cursor(cursor, 2)
    if c:
        where.append("(s.updated_at, s.id) < (%(c_ts)s::timestamptz, %(c_id)s::uuid)")
        params["c_ts"], params["c_id"] = c
    params["lim"] = limit + 1
    rows = conn.execute(
        f"{_BASE_SELECT} WHERE {' AND '.join(where)} ORDER BY s.updated_at DESC, s.id DESC LIMIT %(lim)s", params
    ).fetchall()
    nxt = encode_cursor(rows[limit - 1]["updated_at"], rows[limit - 1]["id"]) if len(rows) > limit else None
    return {"items": [serialize_summary(r, user) for r in rows[:limit]], "next_cursor": nxt}


# ------------------------------------------------------------------ mutations
def check_team_assignment(user: CurrentUser, team_id: str | None, visibility: str) -> None:
    if team_id is not None:
        if not valid_uuid(team_id):
            raise ApiError(422, "validation_error", "team_id must be a UUID")
        if not user.is_admin and team_id not in user.team_ids:
            raise ApiError(403, "forbidden", "you can only assign skills to teams you belong to")
    if visibility == "team" and not team_id:
        raise ApiError(422, "team_required", "visibility 'team' requires a team_id")


def create_skill(
    conn: psycopg.Connection,
    *,
    owner_id: str,
    content: dict,
    team_id: str | None,
    visibility: str,
    source_recording_id: str | None = None,
) -> str:
    row = conn.execute(
        """INSERT INTO skills (owner_id, team_id, visibility, status, draft, source_recording_id, asset_shas)
           VALUES (%s, %s, %s, 'draft', %s, %s, %s) RETURNING id::text""",
        (owner_id, team_id, visibility, psycopg.types.json.Jsonb(content), source_recording_id, shas_in(content)),
    ).fetchone()
    return row["id"]


def update_draft(conn: psycopg.Connection, row: dict, patch_fields: dict, user: CurrentUser) -> None:
    sets, params = ["updated_at = now()"], {"id": row["id"]}
    team_id = patch_fields["team_id"] if "team_id" in patch_fields else (str(row["team_id"]) if row["team_id"] else None)
    visibility = patch_fields.get("visibility") or row["visibility"]
    if "team_id" in patch_fields or "visibility" in patch_fields:
        if "team_id" in patch_fields:
            check_team_assignment(user, team_id, visibility)
        elif visibility == "team" and not team_id:
            raise ApiError(422, "team_required", "visibility 'team' requires a team_id")
        sets += ["team_id = %(team_id)s", "visibility = %(visibility)s"]
        params.update(team_id=team_id, visibility=visibility)
    if patch_fields.get("content") is not None:
        content = patch_fields["content"]
        sets += ["draft = %(draft)s", "asset_shas = ARRAY(SELECT DISTINCT unnest(asset_shas || %(shas)s::text[]))"]
        params.update(draft=psycopg.types.json.Jsonb(content), shas=shas_in(content))
    elif row["draft"] is None and row["published"] is not None and not ({"team_id", "visibility"} & set(patch_fields)):
        # contract: PATCH "creates draft from published if none" (an empty PATCH {} starts an edit);
        # visibility/team changes are skill-level, apply immediately and do not create a draft
        # contract: PATCH "creates draft from published if none"
        sets.append("draft = %(draft)s")
        params["draft"] = psycopg.types.json.Jsonb(row["published"])
    conn.execute(f"UPDATE skills SET {', '.join(sets)} WHERE id = %(id)s", params)


def publish(conn: psycopg.Connection, row: dict, user: CurrentUser, *, embeddings: bool) -> int:
    if row["draft"] is None:
        raise ApiError(409, "no_draft", "nothing to publish: the skill has no draft")
    draft = SkillContent.model_validate(row["draft"]).to_json()
    content = published_content(draft)
    if not content["steps"]:
        raise ApiError(422, "skill_has_no_steps", "a skill needs at least one step to be published"
                       + (" (every step is excluded)" if draft["steps"] else ""))
    if row["visibility"] == "team" and not row["team_id"]:
        raise ApiError(422, "team_required", "visibility 'team' requires a team_id")
    version = (row["current_version"] or 0) + 1
    conn.execute(
        "INSERT INTO skill_versions (skill_id, version, content, created_by) VALUES (%s, %s, %s, %s)",
        (row["id"], version, psycopg.types.json.Jsonb(content), user.id),
    )
    shas = shas_in(content)
    conn.execute(
        """UPDATE skills SET current_version = %(v)s, draft = NULL, status = 'published',
                  search_tsv = ws_skill_tsv(%(c)s::jsonb),
                  published_asset_shas = ARRAY(SELECT DISTINCT unnest(published_asset_shas || %(shas)s::text[])),
                  asset_shas = ARRAY(SELECT DISTINCT unnest(asset_shas || %(shas)s::text[])),
                  updated_at = now()
           WHERE id = %(id)s""",
        {"v": version, "c": psycopg.types.json.Jsonb(content), "shas": shas, "id": row["id"]},
    )
    record_review(conn, str(row["id"]), version, content)
    if embeddings:
        from .jobs import enqueue

        enqueue(conn, "embed_skill", {"skill_id": str(row["id"]), "version": version})
    return version


def published_content(draft: dict) -> dict:
    """What gets published: excluded steps removed, filter annotations dropped."""
    steps = [{**s, "filter": None, "excluded": False} for s in draft.get("steps") or [] if not s.get("excluded")]
    return SkillContent.model_validate({**draft, "steps": steps}).to_json()


def record_review(conn: psycopg.Connection, skill_id: str, version: int, content: dict) -> None:
    """Reviewer feedback for the relevance filter: an event is 'kept' iff a published step came from it.
    Events whose steps were excluded or deleted count as dropped."""
    kept = sorted({q for s in content.get("steps") or [] for q in s.get("source_seqs") or []})
    conn.execute(
        """UPDATE filter_decisions SET final_keep = (seq = ANY(%s::int[])), reviewed_version = %s,
                  reviewed_at = now()
           WHERE skill_id = %s""",
        (kept, version, skill_id),
    )

def embedding_text(content: dict) -> str:
    parts = [content.get("title", ""), content.get("goal", ""), " ".join(content.get("tags", [])),
             " ".join(content.get("apps", []))]
    parts += [f"{s.get('title', '')}: {s.get('instruction', '')}" for s in content.get("steps", [])[:40]]
    return "\n".join(p for p in parts if p)[:8000]


# ------------------------------------------------------------------ search
def to_prefix_tsquery(q: str) -> str:
    """'claim fnol' -> 'claim:* | fnol:*' (OR with prefix matching; RRF/rank does the ordering)."""
    tokens = re.findall(r"[^\W_]+", q.lower())[:12]
    return " | ".join(f"{t}:*" for t in dict.fromkeys(tokens))


RRF_K = 60


def search(
    conn: psycopg.Connection,
    user: CurrentUser,
    q: str,
    limit: int,
    query_vector: str | None,
) -> list[dict]:
    vis, params = visibility_sql(user)
    params.update(lim=limit, pool=max(limit * 3, 50))
    tsq = to_prefix_tsquery(q or "")
    if not tsq and not query_vector:
        rows = conn.execute(
            f"{_BASE_SELECT} WHERE s.status = 'published' AND {vis} ORDER BY s.updated_at DESC, s.id DESC LIMIT %(lim)s",
            params,
        ).fetchall()
        return [{**serialize_summary(r, user), "score": 0.0} for r in rows]

    branches = []
    if tsq:
        params["tsq"] = tsq
        branches.append(
            f"""SELECT s.id, row_number() OVER (ORDER BY ts_rank(s.search_tsv, query) DESC, s.updated_at DESC) AS rnk
                FROM skills s, to_tsquery('english', %(tsq)s) query
                WHERE s.status = 'published' AND {vis} AND s.search_tsv @@ query
                ORDER BY rnk LIMIT %(pool)s"""
        )
    if query_vector:
        params["qvec"] = query_vector
        branches.append(
            f"""SELECT s.id, row_number() OVER (ORDER BY s.embedding <=> %(qvec)s::vector) AS rnk
                FROM skills s
                WHERE s.status = 'published' AND {vis} AND s.embedding IS NOT NULL
                ORDER BY rnk LIMIT %(pool)s"""
        )
    union = " UNION ALL ".join(f"({b})" for b in branches)
    sql = f"""
        WITH ranked AS ({union}),
        fused AS (SELECT id, sum(1.0 / ({RRF_K} + rnk)) AS score FROM ranked GROUP BY id)
        {_BASE_SELECT.replace('SELECT s.id,', 'SELECT f.score, s.id,', 1)}
        JOIN fused f ON f.id = s.id
        ORDER BY f.score DESC, s.updated_at DESC
        LIMIT %(lim)s"""
    rows = conn.execute(sql, params).fetchall()
    return [{**serialize_summary(r, user), "score": round(float(r["score"]), 6)} for r in rows]
