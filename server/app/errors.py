"""Uniform error shape: {"error": {"code": "snake_case", "message": "human text"}}."""
from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("app.errors")

_STATUS_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "payload_too_large",
    415: "unsupported_media_type",
    422: "validation_error",
    429: "rate_limited",
    503: "service_unavailable",
}


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, headers: dict | None = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.headers = headers


def error_body(code: str, message: str) -> dict:
    return {"error": {"code": code, "message": message}}


def not_found(what: str = "resource") -> ApiError:
    return ApiError(404, "not_found", f"{what} not found")


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError):
        return JSONResponse(error_body(exc.code, exc.message), status_code=exc.status, headers=exc.headers)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException):
        code = _STATUS_CODES.get(exc.status_code, "error")
        msg = exc.detail if isinstance(exc.detail, str) else code
        return JSONResponse(error_body(code, msg), status_code=exc.status_code, headers=getattr(exc, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError):
        parts = []
        for e in exc.errors()[:5]:
            loc = ".".join(str(p) for p in e.get("loc", []) if p != "body")
            parts.append(f"{loc}: {e.get('msg')}" if loc else str(e.get("msg")))
        return JSONResponse(error_body("validation_error", "; ".join(parts) or "invalid request"), status_code=422)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        log.exception("unhandled error", extra={"path": request.url.path})
        return JSONResponse(error_body("internal_error", "internal server error"), status_code=500)
