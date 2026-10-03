"""Worker entrypoint: `python -m app.worker`. Polls the Postgres job queue."""
from __future__ import annotations

import logging
import signal
import threading

from .config import get_settings
from .db import create_pool, run_migrations, vector_available
from .jobs import WorkerContext, run_once
from .llm import make_embedder, make_llm
from .logging_setup import setup_logging

log = logging.getLogger("app.worker")


def worker_loop(ctx: WorkerContext, stop: threading.Event) -> None:
    log.info("worker started", extra={"worker_id": ctx.worker_id, "llm": ctx.llm.name})
    while not stop.is_set():
        try:
            busy = run_once(ctx)
        except Exception:  # noqa: BLE001 - e.g. db briefly unavailable; keep the loop alive
            log.exception("worker poll failed")
            busy = False
        if not busy:
            stop.wait(ctx.settings.worker_poll_seconds)
    log.info("worker stopped", extra={"worker_id": ctx.worker_id})


def main() -> None:
    settings = get_settings()
    setup_logging(settings.log_level)
    run_migrations(settings.database_url, disable_vector=settings.disable_pgvector)
    pool = create_pool(settings)
    ctx = WorkerContext(
        pool=pool, settings=settings, llm=make_llm(settings), embedder=make_embedder(settings),
        vector_enabled=(not settings.disable_pgvector) and vector_available(pool),
    )
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    try:
        worker_loop(ctx, stop)
    finally:
        pool.close()


if __name__ == "__main__":
    main()
