"""Skills CRUD, versions, publish/archive, fix suggestions, and hybrid search."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Query, Request
from psycopg.types.json import Jsonb

from .. import skills as svc
from ..auth import Conn, User
from ..errors import ApiError, not_found
from ..llm import LLMError, vector_literal
from ..models import SkillCreate, SkillPatch, SuggestFix

router = APIRouter()
log = logging.getLogger("app.search")


def _reload(conn, user, skill_id: str) -> dict:
    return svc.serialize_skill(svc.get_visible(conn, user, skill_id), user)


@router.get("/skills")
def list_skills(
    user: User,
    conn: Conn,
    q: str | None = Query(None),
    team_id: str | None = Query(None),
    status: str | None = Query(None),
    mine: bool = Query(False),
    limit: int | None = Query(None),
    cursor: str | None = Query(None),
):
    return svc.list_skills(conn, user, q=q, team_id=team_id, status=status, mine=mine,
                           limit=svc.clamp_limit(limit), cursor=cursor)


@router.get("/search")
def search(request: Request, user: User, conn: Conn, q: str = Query(""), limit: int | None = Query(None)):
    qvec = None
    embedder = request.app.state.embedder
    if q.strip() and request.app.state.vector_enabled and embedder.enabled:
        try:
            qvec = vector_literal(embedder.embed([q.strip()[:2000]])[0])
        except LLMError as e:
            log.warning("query embedding failed; full-text only", extra={"error": str(e)})
    return {"items": svc.search(conn, user, q, svc.clamp_limit(limit), qvec)}


@router.post("/skills", status_code=201)
def create_skill(body: SkillCreate, user: User, conn: Conn):
    svc.check_team_assignment(user, body.team_id, body.visibility)
    skill_id = svc.create_skill(conn, owner_id=user.id, content=body.content.to_json(), team_id=body.team_id,
                                visibility=body.visibility)
    return _reload(conn, user, skill_id)


@router.get("/skills/{skill_id}")
def get_skill(skill_id: str, user: User, conn: Conn):
    return _reload(conn, user, skill_id)


@router.patch("/skills/{skill_id}")
def patch_skill(skill_id: str, body: SkillPatch, user: User, conn: Conn):
    row = svc.get_editable(conn, user, skill_id)
    fields = {k: getattr(body, k) for k in body.model_fields_set}
    if "content" in fields and fields["content"] is not None:
        fields["content"] = fields["content"].to_json()
    if "visibility" in fields and fields["visibility"] is None:
        raise ApiError(422, "validation_error", "visibility cannot be null")
    svc.update_draft(conn, row, fields, user)
    return _reload(conn, user, skill_id)


@router.post("/skills/{skill_id}/publish")
def publish(skill_id: str, request: Request, user: User, conn: Conn):
    row = svc.get_editable(conn, user, skill_id)
    st = request.app.state
    svc.publish(conn, row, user, embeddings=bool(st.vector_enabled and st.embedder.enabled))
    return _reload(conn, user, skill_id)


@router.post("/skills/{skill_id}/archive")
def archive(skill_id: str, user: User, conn: Conn):
    svc.get_editable(conn, user, skill_id)
    conn.execute("UPDATE skills SET status = 'archived', updated_at = now() WHERE id = %s", (skill_id,))
    return _reload(conn, user, skill_id)


@router.get("/skills/{skill_id}/versions")
def versions(skill_id: str, user: User, conn: Conn):
    svc.get_visible(conn, user, skill_id)
    rows = conn.execute(
        """SELECT v.version, v.created_at, u.id::text AS uid, u.name, u.email
           FROM skill_versions v LEFT JOIN users u ON u.id = v.created_by
           WHERE v.skill_id = %s ORDER BY v.version DESC""",
        (skill_id,),
    ).fetchall()
    return {"items": [_version_meta(r) for r in rows]}


def _version_meta(r: dict) -> dict:
    by = {"id": r["uid"], "name": r["name"], "email": r["email"]} if r["uid"] else None
    return {"version": r["version"], "created_at": svc.iso(r["created_at"]), "created_by": by}


@router.get("/skills/{skill_id}/versions/{n}")
def version(skill_id: str, n: int, user: User, conn: Conn):
    svc.get_visible(conn, user, skill_id)
    r = conn.execute(
        """SELECT v.version, v.content, v.created_at, u.id::text AS uid, u.name, u.email
           FROM skill_versions v LEFT JOIN users u ON u.id = v.created_by
           WHERE v.skill_id = %s AND v.version = %s""",
        (skill_id, n),
    ).fetchone()
    if not r:
        raise not_found("version")
    return {**_version_meta(r), "content": r["content"]}


@router.post("/skills/{skill_id}/suggest-fix", status_code=201)
def suggest_fix(skill_id: str, body: SuggestFix, user: User, conn: Conn):
    svc.get_visible(conn, user, skill_id)
    row = conn.execute(
        """INSERT INTO skill_fix_suggestions (skill_id, step_index, new_target, note, created_by)
           VALUES (%s, %s, %s, %s, %s) RETURNING id::text AS id""",
        (skill_id, body.step_index, Jsonb(body.new_target.model_dump()), body.note, user.id),
    ).fetchone()
    return {"id": row["id"]}


@router.get("/skills/{skill_id}/suggestions")
def list_suggestions(skill_id: str, user: User, conn: Conn):
    """Extension (not in contract v1): owner/admin view of stored fix suggestions."""
    row = svc.get_visible(conn, user, skill_id)
    if not svc.can_edit(user, row):
        raise ApiError(403, "forbidden", "only the owner or an admin can view suggestions")
    rows = conn.execute(
        """SELECT f.id::text AS id, f.step_index, f.new_target, f.note, f.created_at,
                  u.id::text AS uid, u.name, u.email
           FROM skill_fix_suggestions f LEFT JOIN users u ON u.id = f.created_by
           WHERE f.skill_id = %s ORDER BY f.created_at DESC LIMIT 200""",
        (skill_id,),
    ).fetchall()
    return {"items": [
        {"id": r["id"], "step_index": r["step_index"], "new_target": r["new_target"], "note": r["note"],
         "created_at": svc.iso(r["created_at"]),
         "created_by": {"id": r["uid"], "name": r["name"], "email": r["email"]} if r["uid"] else None}
        for r in rows
    ]}

