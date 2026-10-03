"""Screenshot assets: presign (content-hash dedup), local upload, access-checked download."""
from __future__ import annotations

import hashlib

from fastapi import APIRouter, Query, Request
from fastapi.responses import RedirectResponse, Response
from starlette.concurrency import run_in_threadpool

from ..auth import Conn, CurrentUser, User, authenticate
from ..errors import ApiError, not_found
from ..models import PresignRequest
from ..skills import valid_uuid, visibility_sql

router = APIRouter()


def _base_url(request: Request) -> str:
    return request.app.state.settings.public_base_url or str(request.base_url).rstrip("/")


@router.post("/assets/presign")
def presign(body: PresignRequest, request: Request, user: User, conn: Conn):
    settings = request.app.state.settings
    storage = request.app.state.storage
    if body.bytes > settings.max_asset_bytes:
        raise ApiError(413, "asset_too_large", f"assets are limited to {settings.max_asset_bytes} bytes")
    row = conn.execute(
        """INSERT INTO assets (sha256, content_type, bytes, created_by) VALUES (%s, %s, %s, %s)
           ON CONFLICT (sha256) DO UPDATE SET sha256 = EXCLUDED.sha256
           RETURNING id::text AS id, uploaded, content_type""",
        (body.sha256, body.content_type, body.bytes, user.id),
    ).fetchone()
    exists = row["uploaded"]
    if not exists and storage.kind == "s3" and storage.exists(body.sha256):
        conn.execute("UPDATE assets SET uploaded = true WHERE id = %s", (row["id"],))
        exists = True
    upload = None if exists else storage.presign_put(body.sha256, row["id"], body.content_type, _base_url(request))
    return {"asset_id": row["id"], "exists": exists, "upload": upload}


@router.put("/assets/upload/{asset_id}")
async def upload(asset_id: str, request: Request, expires: int = Query(0), sig: str = Query("")):
    storage = request.app.state.storage
    settings = request.app.state.settings
    if storage.kind != "local":
        raise ApiError(404, "not_found", "direct upload is only available with the local storage driver")
    if not valid_uuid(asset_id) or not storage.verify_upload(asset_id, expires, sig):
        raise ApiError(403, "invalid_signature", "upload URL is invalid or expired")
    data = await request.body()
    if len(data) > settings.max_asset_bytes:
        raise ApiError(413, "asset_too_large", f"assets are limited to {settings.max_asset_bytes} bytes")
    await run_in_threadpool(_store_upload, request.app.state.pool, storage, asset_id, data)
    return Response(status_code=200)


def _store_upload(pool, storage, asset_id: str, data: bytes) -> None:
    with pool.connection() as conn:
        row = conn.execute("SELECT sha256, content_type FROM assets WHERE id = %s", (asset_id,)).fetchone()
        if not row:
            raise not_found("asset")
        if hashlib.sha256(data).hexdigest() != row["sha256"]:
            raise ApiError(422, "sha256_mismatch", "uploaded bytes do not match the presigned sha256")
        storage.write(row["sha256"], data, row["content_type"])
        conn.execute("UPDATE assets SET uploaded = true, bytes = %s WHERE id = %s", (len(data), asset_id))


def _can_access(conn, user: CurrentUser, sha: str, created_by: str | None) -> bool:
    if user.is_admin or created_by == user.id:
        return True
    vis, params = visibility_sql(user)
    row = conn.execute(
        f"""SELECT EXISTS (SELECT 1 FROM recordings r WHERE r.user_id = %(vis_uid_r)s AND %(sha)s = ANY(r.asset_shas))
              OR EXISTS (SELECT 1 FROM skills s WHERE s.owner_id = %(vis_uid_r)s AND %(sha)s = ANY(s.asset_shas))
              OR EXISTS (SELECT 1 FROM skills s WHERE {vis} AND %(sha)s = ANY(s.published_asset_shas)) AS ok""",
        {**params, "sha": sha, "vis_uid_r": user.id},
    ).fetchone()
    return bool(row["ok"])


@router.get("/assets/{sha256}")
def get_asset(sha256: str, request: Request, conn: Conn, access_token: str | None = Query(None)):
    # Bearer header as usual; `?access_token=` is accepted so <img src> works in the web app.
    hdr = request.headers.get("authorization", "")
    token = hdr[7:].strip() if hdr.lower().startswith("bearer ") else access_token
    user = authenticate(request, conn, token)
    sha = sha256.lower()
    row = conn.execute(
        "SELECT sha256, content_type, uploaded, created_by::text AS created_by FROM assets WHERE sha256 = %s", (sha,)
    ).fetchone()
    if not row or not row["uploaded"] or not _can_access(conn, user, sha, row["created_by"]):
        raise not_found("asset")
    storage = request.app.state.storage
    url = storage.presign_get(sha, row["content_type"])
    if url:
        return RedirectResponse(url, status_code=302)
    try:
        data = storage.read(sha)
    except FileNotFoundError:
        raise not_found("asset") from None
    return Response(data, media_type=row["content_type"], headers={"Cache-Control": "private, max-age=86400",
                                                                     "ETag": f'"{sha}"'})
