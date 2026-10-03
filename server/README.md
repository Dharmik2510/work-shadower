# Work Shadower — server

FastAPI API and job worker. One codebase, two entrypoints. Implements `docs/CONTRACT.md`.

## Run locally

```bash
pip install -r requirements.txt -r requirements-dev.txt
scripts/dev_db.sh                         # local Postgres 16 (or point DATABASE_URL at any Postgres)
cp .env.example .env                      # defaults work: dev login, local storage, no LLM
uvicorn app.main:app --port 8000          # API (runs migrations on start)
python -m app.worker                      # worker (or set RUN_WORKER_IN_API=true for one process)
python -m app.seed                        # demo teams, users and 3 skills
```

Serve the web app from the same process: `cd ../web && npm run build`, then set `WEB_DIST_DIR=../web/dist`.

## Tests

```bash
pytest                                    # 35 tests against a real Postgres (workshadower_test DB)
python scripts/e2e_flow.py http://localhost:8000   # full Mac → worker → publish → search → run → kill switch flow
```

## How it's built

| Concern | Where | Notes |
|---|---|---|
| Config | `app/config.py` | All env vars; see `.env.example` |
| DB + migrations | `app/db.py`, `migrations/*.sql` | Applied at startup under an advisory lock, so many replicas can start at once |
| Auth | `app/auth.py` | `AUTH_MODE=dev` (opaque tokens) or `oidc` (JWT via issuer JWKS; groups → teams, auto-provisioned) |
| Visibility | `app/skills.py` → `visibility_sql()` | One central SQL predicate used by every skill, search and asset query |
| Storage | `app/storage.py` | `s3` (AWS / MinIO / any S3 API) or `local`. Uploads go straight to storage via presigned URLs; content-hash dedup |
| Redaction | `app/redact.py` | Re-checks everything the Mac sends: emails, card-like, SIN-like, phone numbers; secure fields dropped |
| Skill writer | `app/skillgen.py` | Cleanup in code first, then **one** LLM call → validated JSON. Any LLM failure or budget hit → rule-based writer (still produces a usable skill) |
| LLM | `app/llm.py` | `anthropic`, `openai` (any OpenAI-compatible URL), or `none`. Embeddings: `openai` or `none` |
| Search | `app/skills.py` | Postgres full-text + pgvector (if installed), merged with reciprocal rank fusion. Works without pgvector |
| Jobs | `app/jobs.py`, `app/worker.py` | Postgres queue, `FOR UPDATE SKIP LOCKED`, exponential backoff, `dead` after max attempts, stuck-job reclaim |
| Costs | `app/usage.py` | Every LLM call logged with tokens + estimated cost; per-user daily budget |
| Flags | `app/flags.py` | Kill switch for recording / replay, screenshot policy, max recording length |

## Scaling notes

- API and workers are stateless. Run as many as you need behind a load balancer.
- File bytes never pass through the API (presigned uploads).
- One Postgres handles this comfortably for thousands of users. Add a read replica for search before anything else.
- Swap the queue for SQS/Service Bus or pgvector for a search engine later without changing the API.

## Deploy

See `../infra/`: one Docker image (API + web), a worker service, Postgres with pgvector, and MinIO.
For cloud, use managed Postgres and S3/Blob; the same image works.
