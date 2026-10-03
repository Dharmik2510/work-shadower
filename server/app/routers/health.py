from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()
log = logging.getLogger("app.health")


@router.get("/healthz")
def healthz(request: Request):
    db_ok = storage_ok = False
    try:
        with request.app.state.pool.connection(timeout=5) as conn:
            conn.execute("SELECT 1")
        db_ok = True
    except Exception as e:  # noqa: BLE001
        log.error("health: db check failed", extra={"error": str(e)})
    try:
        storage_ok = bool(request.app.state.storage.health())
    except Exception as e:  # noqa: BLE001
        log.error("health: storage check failed", extra={"error": str(e)})
    ok = db_ok and storage_ok
    return JSONResponse({"ok": ok, "db": db_ok, "storage": storage_ok}, status_code=200 if ok else 503)
