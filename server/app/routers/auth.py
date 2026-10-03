"""Auth, current user, teams and client config."""
from __future__ import annotations

from fastapi import APIRouter, Request

from ..auth import Conn, User, add_membership, ensure_team, issue_token, load_user, upsert_user
from ..errors import ApiError
from ..flags import effective_config
from ..models import DevLogin
from ..skills import valid_uuid

router = APIRouter()


@router.post("/auth/dev-login")
def dev_login(body: DevLogin, request: Request, conn: Conn):
    settings = request.app.state.settings
    if settings.auth_mode != "dev":
        raise ApiError(404, "not_found", "dev login is disabled")
    admin = body.email.strip().lower() in settings.admin_email_set
    user_id = upsert_user(conn, body.email, body.name, True if admin else None)
    if body.team and body.team.strip():
        team = body.team.strip()
        if valid_uuid(team):
            row = conn.execute("SELECT id::text AS id FROM teams WHERE id = %s", (team,)).fetchone()
            if not row:
                raise ApiError(422, "unknown_team", "no team with that id")
            team_id = row["id"]
        else:
            team_id = ensure_team(conn, team)
        add_membership(conn, user_id, team_id)
    token = issue_token(conn, user_id, settings.token_ttl_hours)
    return {"token": token, "user": load_user(conn, user_id).to_json()}


@router.get("/me")
def me(user: User):
    return user.to_json()


@router.get("/teams")
def teams(user: User, conn: Conn):
    rows = conn.execute("SELECT id::text AS id, name FROM teams ORDER BY name").fetchall()
    return {"items": rows}


@router.get("/config/public")
def config_public(request: Request):
    s = request.app.state.settings
    oidc = {"issuer": s.oidc_issuer, "client_id": s.oidc_client_id} if s.auth_mode == "oidc" else None
    return {"auth_mode": s.auth_mode, "oidc": oidc}


@router.get("/config")
def config(request: Request, user: User, conn: Conn):
    return effective_config(conn, request.app.state.llm.enabled)
