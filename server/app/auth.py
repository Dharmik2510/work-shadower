"""Authentication: dev opaque tokens or OIDC JWT bearer tokens -> CurrentUser."""
from __future__ import annotations

import hashlib
import logging
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Annotated, Any

import httpx
import jwt
import psycopg
from fastapi import Depends, Request

from .config import Settings
from .db import get_conn
from .errors import ApiError

log = logging.getLogger("app.auth")

Conn = Annotated[psycopg.Connection, Depends(get_conn, scope="function")]


@dataclass
class CurrentUser:
    id: str
    email: str
    name: str
    role: str
    teams: list[dict[str, str]] = field(default_factory=list)
    avatar: str = "orb"

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def team_ids(self) -> list[str]:
        return [t["id"] for t in self.teams]

    @property
    def primary_team_id(self) -> str | None:
        return self.teams[0]["id"] if self.teams else None

    def to_json(self) -> dict[str, Any]:
        return {"id": self.id, "email": self.email, "name": self.name, "role": self.role, "teams": self.teams,
                "avatar": self.avatar}


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def load_user(conn: psycopg.Connection, user_id: str) -> CurrentUser | None:
    row = conn.execute("SELECT id::text, email, name, role, avatar FROM users WHERE id = %s", (user_id,)).fetchone()
    if not row:
        return None
    teams = conn.execute(
        "SELECT t.id::text AS id, t.name FROM team_members m JOIN teams t ON t.id = m.team_id "
        "WHERE m.user_id = %s ORDER BY t.name",
        (user_id,),
    ).fetchall()
    return CurrentUser(row["id"], row["email"], row["name"], row["role"], [dict(t) for t in teams], row["avatar"])


def ensure_team(conn: psycopg.Connection, name: str) -> str:
    row = conn.execute(
        "INSERT INTO teams (name) VALUES (%s) ON CONFLICT (lower(name)) DO UPDATE SET name = teams.name RETURNING id::text",
        (name.strip(),),
    ).fetchone()
    return row["id"]


def upsert_user(conn: psycopg.Connection, email: str, name: str, admin: bool | None) -> str:
    """Create or update a user; `admin=True` promotes, None leaves the role alone."""
    row = conn.execute(
        """
        INSERT INTO users (email, name, role) VALUES (%(email)s, %(name)s, %(role)s)
        ON CONFLICT (lower(email)) DO UPDATE
           SET name = EXCLUDED.name,
               role = CASE WHEN %(promote)s THEN 'admin' ELSE users.role END,
               updated_at = now()
        RETURNING id::text
        """,
        {"email": email.strip().lower(), "name": name.strip(), "role": "admin" if admin else "member", "promote": bool(admin)},
    ).fetchone()
    return row["id"]


def add_membership(conn: psycopg.Connection, user_id: str, team_id: str) -> None:
    conn.execute(
        "INSERT INTO team_members (team_id, user_id) VALUES (%s, %s) ON CONFLICT DO NOTHING", (team_id, user_id)
    )


def issue_token(conn: psycopg.Connection, user_id: str, ttl_hours: int) -> str:
    token = "wsd_" + secrets.token_urlsafe(32)
    conn.execute(
        "INSERT INTO auth_tokens (token_hash, user_id, expires_at) VALUES (%s, %s, now() + make_interval(hours => %s))",
        (hash_token(token), user_id, ttl_hours),
    )
    return token


# ------------------------------------------------------------------- OIDC
class _OidcVerifier:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._jwk_client: jwt.PyJWKClient | None = None
        self._lock = threading.Lock()
        self._sync_cache: dict[str, tuple[float, str]] = {}

    def _client(self) -> jwt.PyJWKClient:
        with self._lock:
            if self._jwk_client is None:
                issuer = self.settings.oidc_issuer.rstrip("/")
                meta = httpx.get(f"{issuer}/.well-known/openid-configuration", timeout=10).json()
                self._jwk_client = jwt.PyJWKClient(meta["jwks_uri"], cache_keys=True, lifespan=3600)
            return self._jwk_client

    def verify(self, token: str) -> dict[str, Any]:
        try:
            key = self._client().get_signing_key_from_jwt(token)
            return jwt.decode(
                token,
                key.key,
                algorithms=["RS256", "RS384", "RS512", "ES256", "ES384", "PS256"],
                audience=self.settings.oidc_client_id or None,
                issuer=self.settings.oidc_issuer.rstrip("/") or None,
                options={"verify_aud": bool(self.settings.oidc_client_id), "require": ["exp", "iat"]},
                leeway=30,
            )
        except (jwt.PyJWTError, httpx.HTTPError, KeyError, ValueError) as e:
            raise ApiError(401, "invalid_token", f"invalid bearer token: {e}") from None

    def user_from_claims(self, conn: psycopg.Connection, claims: dict[str, Any]) -> str:
        """Provision the user + team memberships from claims (cached ~5 min per subject)."""
        cache_key = f"{claims.get('iss')}|{claims.get('sub')}|{claims.get('iat')}"
        hit = self._sync_cache.get(cache_key)
        if hit and hit[0] > time.monotonic():
            return hit[1]
        email = claims.get("email") or claims.get("preferred_username") or claims.get("upn")
        if not email or "@" not in str(email):
            raise ApiError(401, "invalid_token", "token has no email claim")
        name = claims.get("name") or str(email).split("@")[0]
        groups = claims.get(self.settings.oidc_groups_claim) or []
        if isinstance(groups, str):
            groups = [groups]
        is_admin = (
            str(email).lower() in self.settings.admin_email_set
            or (bool(self.settings.oidc_admin_group) and self.settings.oidc_admin_group in groups)
        )
        user_id = upsert_user(conn, str(email), str(name), True if is_admin else None)
        if not is_admin and self.settings.oidc_admin_group:
            # the IdP group is the source of truth for admin when configured
            conn.execute("UPDATE users SET role = 'member' WHERE id = %s AND role = 'admin'", (user_id,))
        prefix = self.settings.oidc_team_group_prefix
        team_names = [g[len(prefix):] for g in groups if isinstance(g, str) and g.startswith(prefix) and g[len(prefix):]]
        team_ids = [ensure_team(conn, t) for t in team_names]
        conn.execute("DELETE FROM team_members WHERE user_id = %s AND NOT (team_id = ANY(%s::uuid[]))", (user_id, team_ids))
        for tid in team_ids:
            add_membership(conn, user_id, tid)
        if len(self._sync_cache) > 10_000:
            self._sync_cache.clear()
        self._sync_cache[cache_key] = (time.monotonic() + 300, user_id)
        return user_id


def _bearer(request: Request) -> str | None:
    h = request.headers.get("authorization", "")
    if h.lower().startswith("bearer "):
        return h[7:].strip() or None
    return None


def authenticate(request: Request, conn: psycopg.Connection, token: str | None) -> CurrentUser:
    if not token:
        raise ApiError(401, "unauthorized", "missing bearer token")
    settings: Settings = request.app.state.settings
    if settings.auth_mode == "dev":
        row = conn.execute(
            "SELECT user_id::text FROM auth_tokens WHERE token_hash = %s AND expires_at > now()", (hash_token(token),)
        ).fetchone()
        if not row:
            raise ApiError(401, "invalid_token", "invalid or expired token")
        user_id = row["user_id"]
    else:
        verifier: _OidcVerifier = request.app.state.oidc
        claims = verifier.verify(token)
        user_id = verifier.user_from_claims(conn, claims)
    user = load_user(conn, user_id)
    if not user:
        raise ApiError(401, "invalid_token", "user no longer exists")
    request.state.user_id = user.id
    return user


def current_user(request: Request, conn: Conn) -> CurrentUser:
    return authenticate(request, conn, _bearer(request))


def require_admin(user: Annotated[CurrentUser, Depends(current_user)]) -> CurrentUser:
    if not user.is_admin:
        raise ApiError(403, "forbidden", "admin role required")
    return user


User = Annotated[CurrentUser, Depends(current_user)]
Admin = Annotated[CurrentUser, Depends(require_admin)]


def make_oidc_verifier(settings: Settings) -> _OidcVerifier:
    return _OidcVerifier(settings)
