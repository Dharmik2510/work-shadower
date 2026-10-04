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
| `server/` | API + worker (Python, FastAPI, Postgres) | 57 tests + end-to-end script pass (the pgvector test needs the extension installed) |
| `web/` | Library, search, editor, recordings, runs, admin (React + TS) | Builds clean; smoke test passes against the real API |
| `mac/` | The dot: recorder, intent prompt, upload queue, search, replay (Swift) | Core tests written; app code needs its first compile on a Mac |
| `infra/` | Docker image + compose (Postgres/pgvector, MinIO, API, workers) | Config validated; not run here (no Docker daemon) |
| `docs/ARCHITECTURE.md` | Diagrams of every component and flow | |
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
- **Ask what the task was.** When recording stops, the dot asks "What did you just do?" (one line, skippable). That becomes the title and goal.
- **Leave out what isn't the task.** A relevance filter scores every action (local rules, plus the TypeSafe Jev model if enabled). Side trips, undone mistakes and aimless clicks are greyed out in the draft, never deleted; reviewers can put them back. A recording with two unrelated tasks becomes two drafts.
- **One LLM call per task, optional.** Structured output via a forced tool call, retries with backoff, prompt caching, optional batch mode at half the cost. With `LLM_PROVIDER=none` a rule-based writer still produces good drafts.
- **Pick your dot.** Five characters (Orb, Sprout, Ember, Nimbus, Pixel). Your choice is your floating dot on the Mac and your picture next to the skills you share. They wake up and smile when you hover.
- **Learns from reviewers.** Every publish records which steps people kept. Admins see the put-back rate and a threshold table, can tune the cut-offs live, and can export labelled examples.
- **Local-first Mac.** Recordings survive offline and restarts; uploads retry with backoff.
- **Humans stay in control.** Drafts are reviewed before publishing. Replay always stops before anything that can't be undone.
- **Kill switch.** Admins can turn recording or replay off company-wide.
- **Privacy by default.** Passwords never read; PII redacted on the Mac and again on the server; screenshots optional.

## Before a real rollout

1. Compile the Mac app on a Mac, fix any compiler issues, sign with a Developer ID, deploy with MDM (`mac/mdm/`).
2. Switch `AUTH_MODE=oidc` and point it at company SSO.
3. Pick an approved LLM endpoint (or keep `none`).
4. Decide on the step filter: `FILTER_PROVIDER=local` (rules, nothing leaves the company) or `jev` (better recall; sends redacted event text to TypeSafe — needs infosec sign-off). Run `python -m app.eval_filter` on a few dozen labelled recordings from your own teams before rollout.
5. Review with infosec: what gets captured, retention, who can publish, who can run skills.
