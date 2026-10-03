"""FastAPI application factory. Run: uvicorn app.main:app"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from .auth import make_oidc_verifier
from .config import Settings, get_settings
from .db import create_pool, run_migrations, vector_available
from .errors import ApiError, install_error_handlers
from .llm import Embedder, LLMProvider, make_embedder, make_llm
from .logging_setup import request_id_var, setup_logging
from .routers import admin, assets, auth, health, recordings, runs, skills
from .storage import make_storage

log = logging.getLogger("app")
API_PREFIX = "/api/v1"


def create_app(
    settings: Settings | None = None,
    *,
    llm: LLMProvider | None = None,
    embedder: Embedder | None = None,
    configure_logging: bool = True,
) -> FastAPI:
    settings = settings or get_settings()
    if configure_logging:
        setup_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if settings.secret_key == "dev-insecure-change-me" and settings.auth_mode != "dev":
            log.warning("SECRET_KEY is the insecure default; set it in production")
        run_migrations(settings.database_url, disable_vector=settings.disable_pgvector)
        pool = create_pool(settings)
        app.state.pool = pool
        app.state.vector_enabled = (not settings.disable_pgvector) and vector_available(pool)
        stop = threading.Event()
        worker_thread = None
        if settings.run_worker_in_api:
            from .worker import worker_loop

            ctx = _worker_ctx(app)
            worker_thread = threading.Thread(target=worker_loop, args=(ctx, stop), name="ws-worker", daemon=True)
            worker_thread.start()
        log.info("api started", extra={"vector_enabled": app.state.vector_enabled, "llm": app.state.llm.name,
                                       "storage": app.state.storage.kind, "auth_mode": settings.auth_mode})
        try:
            yield
        finally:
            stop.set()
            if worker_thread:
                worker_thread.join(timeout=10)
            pool.close()

    app = FastAPI(title="Work Shadower API", version="1.0.0", lifespan=lifespan,
                  docs_url=f"{API_PREFIX}/docs", openapi_url=f"{API_PREFIX}/openapi.json", redoc_url=None)
    app.state.settings = settings
    app.state.storage = make_storage(settings)
    app.state.llm = llm or make_llm(settings)
    app.state.embedder = embedder or make_embedder(settings)
    app.state.oidc = make_oidc_verifier(settings)
    app.state.vector_enabled = False

    install_error_handlers(app)

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex
        token = request_id_var.set(rid)
        start = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers["X-Request-ID"] = rid
            return response
        finally:
            if request.url.path != "/healthz":
                log.info("request", extra={
                    "method": request.method, "path": request.url.path, "status": status,
                    "duration_ms": round((time.perf_counter() - start) * 1000, 1),
                    "user_id": getattr(request.state, "user_id", None),
                })
            request_id_var.reset(token)

    origins = settings.cors_origin_list
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials="*" not in origins,
            allow_methods=["*"],
            allow_headers=["*"],
            expose_headers=["X-Request-ID"],
        )

    app.include_router(health.router)
    for r in (auth.router, assets.router, recordings.router, skills.router, runs.router, admin.router):
        app.include_router(r, prefix=API_PREFIX)

    if settings.web_dist_dir:
        _mount_spa(app, Path(settings.web_dist_dir))
    return app


def _mount_spa(app: FastAPI, dist: Path) -> None:
    dist = dist.resolve()
    index = dist / "index.html"

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        if full_path == "api" or full_path.startswith("api/"):
            raise ApiError(404, "not_found", "no such endpoint")
        candidate = (dist / full_path).resolve()
        if full_path and candidate.is_file() and candidate.is_relative_to(dist):
            cache = "public, max-age=31536000, immutable" if "/assets/" in f"/{full_path}" else "no-cache"
            return FileResponse(candidate, headers={"Cache-Control": cache})
        if not index.is_file():
            raise ApiError(404, "not_found", "web app not built")
        return FileResponse(index, headers={"Cache-Control": "no-cache"})


def _worker_ctx(app: FastAPI):
    from .jobs import WorkerContext

    return WorkerContext(pool=app.state.pool, settings=app.state.settings, llm=app.state.llm,
                         embedder=app.state.embedder, vector_enabled=app.state.vector_enabled)


def __getattr__(name: str):
    # lazy module-level `app` so importing app.main (e.g. in tests) has no side effects
    if name == "app":
        global app
        app = create_app()
        return app
    raise AttributeError(name)
