# Work Shadower — web

React 18 + TypeScript + Vite SPA for the skill library, draft review, run history and admin.

## Run

```bash
npm install
npm run dev:mock     # in-memory mock API (VITE_USE_MOCK=1), no backend needed
npm run dev          # real API: proxies /api and /healthz to http://localhost:8000 (override with API_PROXY_TARGET)
npm run build        # typecheck + build to web/dist (serve with the backend's WEB_DIST_DIR)
npm run typecheck
```

The backend must serve `index.html` for unknown non-`/api` paths (client-side routes such as `/skills/:id` and `/auth/callback`).

### Tests and screenshots

```bash
BASE_URL=http://localhost:8000 node e2e/smoke.mjs    # dev-login, search, open a skill (needs AUTH_MODE=dev)
BASE_URL=http://localhost:5173 node e2e/screens.mjs  # every screen, light/dark, 1280px/390px, into screenshots/
```

Set `PLAYWRIGHT_BROWSERS_PATH` if Chromium lives outside Playwright's default cache.

## Layout

- `src/api/types.ts`: contract types. `client.ts`: `ApiClient` interface, `HttpApiClient` (bearer header, error envelope → `ApiError`, `newIdempotencyKey()`). `mock.ts`: in-memory `MockApiClient` with seed data. `index.ts`: picks the client.
- `src/auth/`: token storage, `AuthContext`, and `oidc.ts` (authorization code + PKCE; the id_token becomes the bearer).
- `src/pages/`: Login, OidcCallback, Library, SkillDetail, SkillEditor (review and new), Recordings, Runs, Admin.
- `src/styles/tokens.css`: design tokens for light and dark. Theme follows the OS and can be overridden from the header (stored in `localStorage`).

## How the web app reads the contract

Where `docs/CONTRACT.md` leaves something open, the web app does this:

1. **Search vs. filters.** `/search` only takes `q` and `limit` and returns published skills only. With a query, the Library calls `/search` and applies team and "mine" filters on the client, and the status filter is locked to Published. With no query, it calls `GET /skills` with `team_id`, `mine` and `status`.
2. **Default status.** `GET /skills` without `status` is assumed to return drafts and published skills and leave out archived ones. The mock does this.
3. **Run list shape** (`GET /runs?skill_id=`, only described as "paged"): assumed `{id, skill_id, version, mode, status: running|succeeded|failed|aborted, user?, started_at, finished_at?, error?, steps?: [RunStepReport]}`.
4. **`/admin/usage.by_team` items:** assumed `{team_id?, team_name? | team?: string|{id,name}, llm_calls?, est_cost_usd?, input_tokens?, output_tokens?}`. The chart reads these fields defensively and falls back to call counts if there's no cost.
5. **`/admin/jobs?status=dead`:** accepted as either `{items: Job[]}` or a bare array. Job fields read: `id, kind|type, attempts, max_attempts?, last_error|error, created_at, updated_at`.
6. **`created_by` on versions:** handled as either a user object or a string.
7. **`health.success_rate`:** treated as nullable when `runs == 0`. **`current_version`:** `0` means never published.
8. **Recording list items:** may include an optional `title_hint`, which is shown if present. Otherwise the recording shows as "Untitled recording".
9. **Manual steps:** steps added by hand get `action: {type: "wait"}` and no target, meaning the person does this step themselves. `apps` is edited directly as tags rather than worked out from the steps.
10. **Publish with unsaved edits:** the editor sends `PATCH` first, then `POST /publish`. A new skill is `POST /skills`, then publish.
11. **Screenshots:** fetched with `fetch` and the bearer token, then shown through blob object URLs that are cached per sha256. The browser follows the 302 and drops `Authorization` on a cross-origin redirect, which is what presigned URLs need. **Presigned storage must allow CORS GET from the web origin.** "Hide screenshot" sets the step's `screenshot_sha256` to `null` in the draft.
12. **OIDC:** discovery comes from `${issuer}/.well-known/openid-configuration`. The flow is a public client with `redirect_uri = ${origin}/auth/callback` and `scope=openid email profile`. The SPA only checks state and nonce. Signature validation is left to the server.
13. **"Do it for me":** opens `workshadower://run?skill_id=…&version=…`. If the page doesn't lose focus within about 1.6 s, it shows the "Mac app not installed" fallback. The button is disabled when `/config` says `replay_enabled=false`, or when the skill has never been published.
