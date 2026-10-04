# Work Shadower — Shared Contract (v1)

Single source of truth for the API, data shapes, and conventions shared by
`server/` (Python FastAPI), `web/` (React SPA), and `mac/` (Swift "Dot" app).
If something here is ambiguous, prefer the simplest reading and note it in your
component README.

## Product in one paragraph
An always-on floating dot on each employee's Mac. Click it to record a workflow.
The Mac captures **structured accessibility events** (not video) plus a few
screenshots, redacts locally, queues in SQLite, and uploads. A server worker turns
the event log into a **draft skill** (one cheap LLM call, or a no-AI heuristic
fallback). The author reviews/edits it in the web app and publishes it to a shared,
searchable library. Colleagues can read it ("learn") or have the Mac app replay it
("do it for me") via a fallback ladder: deterministic AX replay → LLM repair → stop
and ask. Irreversible steps always require a human click.

## Repo layout
```
work-shadower/
  docs/            CONTRACT.md, ARCHITECTURE.md
  server/          FastAPI API + worker (same codebase, two entrypoints)
  web/             React + TypeScript + Vite SPA
  mac/             Swift Package "Dot" (recorder, uploader, replayer, UI)
  infra/           docker-compose.yml, .env.example
```

## Stack
- PostgreSQL 16 (+ `pgvector` if available; must degrade gracefully to full-text only)
- Object storage: S3-compatible (AWS S3 / MinIO / Azure via S3 gateway). Dev fallback: local filesystem driver served by the API.
- Job queue: Postgres `jobs` table, `SELECT … FOR UPDATE SKIP LOCKED`, exponential backoff, max attempts, dead state.
- LLM provider (env `LLM_PROVIDER`): `anthropic` | `openai` (any OpenAI-compatible base URL) | `none` (deterministic heuristic). Embeddings (env `EMBED_PROVIDER`): `openai` | `none`.
- Auth: `AUTH_MODE=dev` (dev-login endpoint, opaque tokens) or `AUTH_MODE=oidc` (validate JWT bearer via issuer JWKS; email + groups claims → user/teams, auto-provisioned).

## Conventions
- Base path: `/api/v1`. JSON everywhere. Times: ISO-8601 UTC strings. IDs: UUID strings.
- Auth header: `Authorization: Bearer <token>` on every call except `/healthz`, `/api/v1/auth/dev-login`, `/api/v1/config/public`.
- Errors: HTTP status + `{"error": {"code": "snake_case", "message": "human text"}}`.
- Pagination: `?limit=` (default 20, max 100) and `?cursor=`; responses `{"items": [...], "next_cursor": string|null}`.
- Idempotency: `POST /recordings` and `POST /runs` require header `Idempotency-Key` (client-generated UUID). Same key + same user → return the original resource with 200 (not a duplicate).
- Roles: `member` (default), `admin`. Team membership controls visibility.
- Visibility of a skill: `private` (owner only), `team` (members of skill's team), `org` (everyone).

## Data shapes

### User
```json
{"id":"uuid","email":"a@b.com","name":"Dharmik","role":"member|admin","teams":[{"id":"uuid","name":"UBI"}]}
```

### Recorded event (Mac → server), inside a recording
```json
{
  "seq": 1,
  "ts": "2026-10-03T14:01:02.123Z",
  "type": "app_activate|click|type|key|menu|window_open|url_change|scroll",
  "app": {"bundle_id": "com.google.Chrome", "name": "Google Chrome"},
  "window": {"title": "Claims – Guidewire"},
  "element": {
    "role": "AXButton", "subrole": null,
    "label": "Submit claim", "identifier": "submitBtn",
    "path": ["AXWindow:Claims", "AXGroup", "AXButton:Submit claim"],
    "value_kind": "text|secure|none"
  },
  "text": "string typed (omitted/redacted if secure or PII-detected)",
  "key": "cmd+s",
  "url": "https://… (browsers only, query string stripped)",
  "screenshot_sha256": "hex or null"
}
```
Redaction rule (client AND server re-check): never send text for `value_kind=secure`;
replace emails, card-like digit runs (13–19 digits), SIN-like `\d{3}[- ]?\d{3}[- ]?\d{3}`,
and phone numbers with `[REDACTED:<kind>]`.

### Skill content (JSONB `skill_versions.content`)
```json
{
  "title": "Create a new auto claim",
  "goal": "One sentence of what this accomplishes",
  "apps": ["Google Chrome", "Outlook"],
  "prerequisites": ["Access to ClaimCenter"],
  "inputs": [{"name":"policy_number","description":"Policy to file against","example":"P-123456"}],
  "steps": [
    {
      "index": 1,
      "title": "Open ClaimCenter",
      "instruction": "Human-readable instruction, may reference {{policy_number}}",
      "app": "Google Chrome",
      "action": {
        "type": "open_app|open_url|click|type|key|menu|wait",
        "target": {"role":"AXButton","label":"Submit claim","identifier":null,"path":[],"window_title":"Claims"},
        "text": "{{policy_number}}",
        "key": "cmd+s",
        "url": "https://…"
      },
      "expect": {"window_title_contains": "Claim #", "element_present": {"role":"AXStaticText","label":"Saved"}},
      "screenshot_sha256": "hex|null",
      "irreversible": false,
      "excluded": false,
      "filter": {"decision":"keep|review|drop","reason":"detour|mistake_undone|exploration|duplicate|idle_or_noise|unclear_relevance|on_task|needed_navigation|<free text>","p_drop":0.0,"source":"local|jev|jev+local|llm|human"},
      "source_seqs": [12, 13]
    }
  ],
  "tags": ["claims","onboarding"]
}
```

`excluded` steps exist only in drafts: the editor greys them out and the reviewer can put them back.
`POST /skills/{id}/publish` removes excluded steps and clears `filter`, so published versions (and replay)
never contain them. `filter` explains why the relevance filter thinks a step may not be needed;
`source_seqs` ties a step to the recorded events it came from (used for reviewer feedback).

### Skill (API response)
```json
{
  "id":"uuid","owner":{"id":"uuid","name":"…","email":"…"},"team":{"id":"uuid","name":"…"}|null,
  "visibility":"private|team|org","status":"draft|published|archived",
  "current_version":3,"draft":{…skill content…}|null,"published":{…skill content…}|null,
  "source_recording_id":"uuid|null","created_at":"…","updated_at":"…",
  "health":{"runs":12,"success_rate":0.91,"last_run_at":"…"}
}
```
Search/list returns a lighter `SkillSummary`: `id,title,goal,owner,team,visibility,status,tags,apps,current_version,updated_at,health`.

## Endpoints (`/api/v1`)

Auth & config
- `POST /auth/dev-login` `{email,name,team?}` → `{token,user}` (only when AUTH_MODE=dev)
- `GET /me` → User
- `GET /teams` → `{items:[{id,name}]}`
- `GET /config/public` → `{auth_mode, oidc:{issuer,client_id}|null}`
- `GET /config` → flags `{recording_enabled, replay_enabled, llm_enabled, max_recording_minutes, screenshot_policy:"key_moments|none", filter_enabled, filter_drop_threshold, filter_review_threshold, split_tasks_enabled}`

Assets (screenshots)
- `POST /assets/presign` `{sha256, content_type:"image/jpeg|image/png", bytes}` → `{asset_id, exists:bool, upload:{method:"PUT", url, headers:{}}|null}` (if `exists`, skip upload — content-hash dedup). Max 2 MB.
- `PUT  /assets/upload/{asset_id}` (local storage driver only; the presign URL points here)
- `GET  /assets/{sha256}` → 302 redirect to presigned GET (or streams bytes in local mode). Access: caller must be able to see a skill/recording referencing it.

Recordings
- `POST /recordings` (Idempotency-Key) `{title_hint?, intent?, started_at, ended_at, client:{app_version, os_version, device_id}, events:[Event…]}` → `{id, status:"received"}` (202). Max 5,000 events. `intent` (≤1000 chars) is the author's answer to "What did you just do?"; redacted on the Mac and again on the server.
- `GET  /recordings` / `GET /recordings/{id}` → `{id,status:"received|processing|ready|failed",error?,skill_id?,skill_ids:[…],event_count,title_hint,intent,filter:{source,counts:{keep,review,drop},segments,task_type}|null,created_at}`. A recording that contained several unrelated tasks becomes several drafts (`skill_ids`, in task order; `skill_id` is the first).

Skills
- `GET    /skills?q=&team_id=&status=&mine=true&limit=&cursor=` → paged SkillSummary
- `GET    /search?q=&limit=` → `{items:[SkillSummary + {score}]}` (hybrid: vector if available + full-text, published & visible only)
- `POST   /skills` `{content, team_id?, visibility}` → Skill (manual authoring, draft)
- `GET    /skills/{id}` → Skill
- `PATCH  /skills/{id}` `{content?, team_id?, visibility?}` → edits the draft (creates draft from published if none)
- `POST   /skills/{id}/publish` → creates immutable new version, clears draft, embeds
- `POST   /skills/{id}/archive`
- `GET    /skills/{id}/versions` → `{items:[{version,created_at,created_by}]}`
- `GET    /skills/{id}/versions/{n}` → `{version, content, created_at, created_by}`
- `POST   /skills/{id}/suggest-fix` `{step_index, new_target, note}` → stores a suggested repair for the owner (from self-healing replay)
Only owner (or admin) may edit/publish/archive.

Runs (replay telemetry)
- `POST /runs` (Idempotency-Key) `{skill_id, version, mode:"guided|auto", inputs:{}}` → `{id}`
- `POST /runs/{id}/steps` `{step_index, status:"ok|repaired|failed|skipped|confirmed", strategy:"deterministic|llm_repair|vision|human", duration_ms, detail?}`
- `POST /runs/{id}/finish` `{status:"succeeded|failed|aborted", error?}`
- `GET  /runs?skill_id=` → paged
- `POST /replay/repair` `{skill_id, version, step_index, ui_tree:[{role,label,identifier,path}] (≤300 nodes)}` → `{target:{…}|null, confidence:0..1}` (LLM repair; 503 `llm_disabled` if none)

Admin (role=admin)
- `GET /admin/flags`, `PUT /admin/flags` (same shape as `/config`; kill switch = set `recording_enabled`/`replay_enabled` false)
- `GET /admin/usage?days=30` → `{llm_calls, input_tokens, output_tokens, est_cost_usd, by_team:[…], skills_created, runs, run_success_rate}`
- `GET /admin/filter/stats?days=30` → how the relevance filter agrees with reviewers: decision counts, `matrix` (decision × final_keep), `wrongly_dropped_rate`, `missed_rate`, `by_reason`, `by_source`, `threshold_curve:[{threshold,flagged,precision,recall}]`, Jev cost
- `GET /admin/filter/export?days=90&reviewed_only=true` → NDJSON of labelled examples `{recording_id, seq, segment, event (redacted), decision, reason, p_drop, source, model, final_keep, goal}`
- `PUT /admin/flags` also takes `filter_enabled`, `filter_drop_threshold`, `filter_review_threshold` (review ≤ drop, both in (0,1]), `split_tasks_enabled`
- `GET /admin/jobs?status=dead` → failed jobs for ops
- `POST /admin/jobs/{id}/retry`

Ops
- `GET /healthz` → `{ok:true, db:true, storage:true}`
- Rate limits / budgets: env `LLM_DAILY_CALLS_PER_USER` (default 50); exceed → recording is still processed with heuristic fallback (never fails because of budget). Relevance-filter (Jev) calls are logged in `llm_usage` with purpose `filter` and do not count toward this budget.
