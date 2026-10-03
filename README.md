# Work Shadower

A floating dot on every employee's Mac. Click it, do your task, click it again.
It turns what you did into a step-by-step skill your colleagues can search, learn from,
or have the dot run for them.

```
 Mac (Dot app)                    Server                               Web app
 ───────────────                  ──────                               ───────
 record AX events  ──presign──▶   API (FastAPI, stateless)  ◀──────    library + search
 redact on device  ──upload──▶    object storage (S3/MinIO)            review & publish
 SQLite queue      ──POST────▶    Postgres (+ queue, + pgvector)       run history
 replay ladder     ◀──skill───    worker: events → draft skill         admin: kill switch,
                   ──telemetry▶            (1 small LLM call or none)         usage & cost
```

| Folder | What | Status |
|---|---|---|
| `server/` | API + worker (Python, FastAPI, Postgres) | 35 tests + end-to-end script pass |
| `web/` | Library, search, editor, recordings, runs, admin (React + TS) | Builds clean; smoke test passes against the real API |
| `mac/` | The dot: recorder, upload queue, search, replay (Swift) | 45 core tests pass; app code needs its first compile on a Mac |
| `infra/` | Docker image + compose (Postgres/pgvector, MinIO, API, workers) | Config validated; not run here (no Docker daemon) |
| `docs/CONTRACT.md` | The API contract all three share | |

No Databricks anywhere. Cloud-agnostic: anything that runs containers, Postgres and S3-compatible storage.

## Try it

```bash
# Backend + web on a Mac, no Docker (uses Homebrew)
./run-local-mac.sh

# Or: backend + web, all in Docker
cd infra && cp .env.example .env && docker compose up -d --build
docker compose run --rm api python -m app.seed
open http://localhost:8000          # sign in as dharmik@example.com (admin)

# Mac app (on a Mac with Xcode)
cd mac && ./build.sh && open Dot.app   # Settings → server http://localhost:8000 → sign in → permissions
```

## Key decisions

- **Structure, not video.** Accessibility events are small and readable, so the AI step is cheap.
- **One LLM call per recording, optional.** With `LLM_PROVIDER=none` a rule-based writer still produces good drafts.
- **Local-first Mac.** Recordings survive offline and restarts; uploads retry with backoff.
- **Humans stay in control.** Drafts are reviewed before publishing. Replay always stops before anything that can't be undone.
- **Kill switch.** Admins can turn recording or replay off company-wide.
- **Privacy by default.** Passwords never read; PII redacted on the Mac and again on the server; screenshots optional.

## Before a real rollout

1. Compile the Mac app on a Mac, fix any compiler issues, sign with a Developer ID, deploy with MDM (`mac/mdm/`).
2. Switch `AUTH_MODE=oidc` and point it at company SSO.
3. Pick an approved LLM endpoint (or keep `none`).
4. Review with infosec: what gets captured, retention, who can publish, who can run skills.
