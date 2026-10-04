# Architecture

Every component of Work Shadower and how they connect. All diagrams are Mermaid, so GitHub renders them in place.

1. [System overview](#1-system-overview)
2. [Deployment](#2-deployment)
3. [Mac app (Dot) components](#3-mac-app-dot-components)
4. [Dot states](#4-dot-states)
5. [Server components](#5-server-components)
6. [Web app components](#6-web-app-components)
7. [Data model](#7-data-model)
8. [Flow: record → draft skill](#8-flow-record--draft-skill)
9. [Flow: review, publish, search](#9-flow-review-publish-search)
10. [Flow: "Do it for me" replay](#10-flow-do-it-for-me-replay)
11. [Job queue lifecycle](#11-job-queue-lifecycle)
12. [Privacy and trust boundaries](#12-privacy-and-trust-boundaries)
13. [Intent and relevance filtering](#13-intent-and-relevance-filtering)

---

## 1. System overview

Three apps, one API contract (`docs/CONTRACT.md`).

```mermaid
flowchart LR
    subgraph Mac["Employee's Mac"]
        Dot["Dot app<br/>(Swift)"]
        Apps["Work apps<br/>Chrome, Outlook, ClaimCenter…"]
        Dot -- "reads controls (Accessibility)<br/>presses them on replay" --> Apps
    end

    subgraph Browser["Any browser"]
        Web["Web app<br/>(React SPA)"]
    end

    subgraph Backend["Backend (containers)"]
        API["API<br/>(FastAPI, stateless)"]
        Worker["Worker(s)"]
        PG[("PostgreSQL<br/>data · queue · search")]
        S3[("Object storage<br/>S3 / MinIO<br/>screenshots")]
    end

    LLM["LLM provider (optional)<br/>Anthropic / OpenAI-compatible / none"]
    IdP["Company SSO (OIDC)"]

    Dot -- "HTTPS JSON" --> API
    Dot -- "presigned PUT (screenshots)" --> S3
    Web -- "HTTPS JSON" --> API
    Web -. "sign in" .-> IdP
    API -. "verify JWT (JWKS)" .-> IdP
    API --> PG
    API -- "presign URLs" --> S3
    Worker --> PG
    Worker -- "1 call per recording<br/>+ repair, embeddings" --> LLM
    API -- "replay repair" --> LLM
    Web -- "workshadower://run" --> Dot
```

## 2. Deployment

The same image runs the API (and serves the web app) and the worker. Both scale out by adding containers.

```mermaid
flowchart TB
    subgraph Clients
        M1["Dot on Mac #1"]
        M2["Dot on Mac #N"]
        B["Browsers"]
    end

    MDM["MDM (Jamf / Intune)<br/>installs Dot.app<br/>+ PPPC permissions profile"]
    MDM -. "deploys" .-> M1
    MDM -. "deploys" .-> M2

    LB["Load balancer / TLS"]
    M1 --> LB
    M2 --> LB
    B --> LB

    subgraph Compute["Container platform (any cloud or on-prem)"]
        A1["api #1<br/>uvicorn + web/dist"]
        A2["api #N"]
        W1["worker #1<br/>python -m app.worker"]
        W2["worker #N"]
    end

    LB --> A1
    LB --> A2

    subgraph Managed["Managed services"]
        PG[("PostgreSQL 16<br/>+ pgvector (optional)")]
        OBJ[("S3 / Azure Blob / MinIO")]
    end

    A1 --> PG
    A2 --> PG
    W1 --> PG
    W2 --> PG
    A1 -. "presign" .-> OBJ
    M1 -- "direct upload" --> OBJ
    M2 -- "direct upload" --> OBJ

    subgraph Local["Local options"]
        L1["infra/docker-compose.yml<br/>db · minio · api · 2 workers"]
        L2["run-local-mac.sh<br/>Homebrew Postgres · local files<br/>API + worker in one process"]
    end
```

## 3. Mac app (Dot) components

`DotCore` is plain Swift and unit tested on any platform. `DotApp` is the AppKit/SwiftUI shell.

```mermaid
flowchart TB
    subgraph DotApp["DotApp (macOS 13+)"]
        AC["AppController<br/>wires everything · kill switch · workshadower:// links"]
        DV["DotView + DotModel<br/>the floating dot (60 fps)"]
        REC["Recorder<br/>event tap · AX lookups"]
        SHOT["Screenshotter<br/>ScreenCaptureKit / CGWindowList"]
        RP["Replayer<br/>replay ladder"]
        HUD["ReplayHUD + InputsForm"]
        SP["SearchPanel<br/>⌥⌘Space"]
        SW["SettingsWindow<br/>server · sign-in · permissions"]
        AXS["AXSupport<br/>AX tree read/press/type"]
        PERM["Permissions"]
        SET["SettingsStore<br/>UserDefaults + Keychain token"]
        NET["NetworkMonitor + HotKey"]
    end

    subgraph DotCore["DotCore (platform-neutral, tested)"]
        EM["EventModels<br/>RecordedEvent · RecordingPayload"]
        RED["Redactor<br/>email · card · SIN · phone"]
        CLEAN["EventCleaner<br/>merge typing · drop noise"]
        Q["UploadQueue<br/>SQLite"]
        UP["Uploader<br/>backoff + jitter"]
        API["APIClient"]
        TM["TargetMatcher<br/>score AX nodes"]
        TPL["Template<br/>{{inputs}}"]
        SM["SkillModels · APIModels"]
    end

    AC --> DV
    AC --> REC
    AC --> RP
    AC --> SP
    AC --> SW
    AC --> Q
    AC --> NET
    REC --> AXS
    REC --> SHOT
    REC --> CLEAN
    CLEAN --> RED
    CLEAN --> EM
    AC -- "enqueue payload + screenshots" --> Q
    Q --> UP
    UP --> API
    RP --> HUD
    RP --> AXS
    RP --> TM
    RP --> TPL
    RP --> API
    SP --> API
    SW --> API
    SW --> PERM
    SW --> SET
    API --> SM
```

## 4. Dot states

```mermaid
stateDiagram-v2
    [*] --> Sleeping
    Sleeping --> Awake: cursor near (eyes open, blink, follow cursor)
    Awake --> Sleeping: cursor away
    Awake --> Recording: click (signed in, permissions ok, recording enabled)
    Recording --> Uploading: click again / time limit
    Recording --> Sleeping: admin kill switch (recording discarded)
    Uploading --> Sleeping: queue drained
    Uploading --> Error: needs sign-in / offline (kept in queue)
    Error --> Uploading: back online / signed in / retry
    Awake --> Replaying: "Do it" from search or web link
    Replaying --> Sleeping: finished / stopped
```

| State | Look |
|---|---|
| Sleeping | Eyes closed, slow breathing |
| Awake | Eyes open, blinking, pupils follow cursor |
| Recording | Red pulsing ring, timer on hover |
| Uploading | Progress arc |
| Replaying | Green ring + step HUD |
| Error | Amber ring, reason in tooltip |

## 5. Server components

```mermaid
flowchart TB
    subgraph API["API process (app.main)"]
        MW["Middleware<br/>request id · JSON logs · CORS · errors"]
        AUTH["auth.py<br/>dev tokens / OIDC JWT"]
        subgraph Routers["routers/"]
            R1["auth · me · teams · config"]
            R2["assets<br/>presign · upload · get"]
            R3["recordings"]
            R4["skills · search · versions · suggest-fix"]
            R5["runs · replay/repair"]
            R6["admin<br/>flags · usage · jobs · filter stats/export"]
            R7["health"]
        end
        SPA["Static web app<br/>(WEB_DIST_DIR, SPA fallback)"]
    end

    subgraph Core["Shared modules"]
        SK["skills.py<br/>visibility_sql() · publish · hybrid search"]
        STO["storage.py<br/>s3 | local"]
        RED["redact.py"]
        JOBS["jobs.py<br/>queue: enqueue · claim · retry · dead"]
        FLAGS["flags.py"]
        USE["usage.py<br/>tokens · cost · daily budget"]
        DB["db.py<br/>pool · migrations (advisory lock)"]
        CFG["config.py"]
    end

    subgraph WorkerP["Worker process (app.worker)"]
        LOOP["poll loop"]
        H1["generate_skill"]
        H2["embed_skill"]
        H3["poll_llm_batch"]
        FLT["filtering.py<br/>local rules + Jev → keep · review · drop<br/>retries · circuit breaker · task segments"]
        GEN["skillgen.py<br/>cleanup → 1 LLM call per task (tool use) → validate<br/>↳ rule-based fallback"]
        LLM["llm.py<br/>anthropic | openai | none<br/>retries · prompt caching · batches"]
    end

    MW --> AUTH --> Routers
    R2 --> STO
    R3 --> RED
    R3 --> JOBS
    R3 --> FLAGS
    R4 --> SK
    R4 --> JOBS
    R5 --> LLM
    R5 --> USE
    R6 --> FLAGS
    R6 --> USE
    R6 --> JOBS
    LOOP --> JOBS
    JOBS --> H1 --> FLT
    H1 --> GEN --> LLM
    JOBS --> H2 --> LLM
    JOBS --> H3 --> LLM
    H1 --> USE
    H1 --> SK
    SK --> DB
    JOBS --> DB
    FLAGS --> DB
    USE --> DB
```

## 6. Web app components

```mermaid
flowchart TB
    subgraph Pages
        LOGIN["/login<br/>dev login or SSO"]
        CB["/auth/callback<br/>OIDC PKCE"]
        LIB["/ Library<br/>“How do I…?” search + filters"]
        DET["/skills/:id<br/>learn · screenshots · Do it for me"]
        EDIT["/skills/:id/edit · /skills/new<br/>review & publish"]
        RUNS["/skills/:id/runs<br/>run history"]
        RECS["/recordings<br/>status polling"]
        ADMIN["/admin<br/>kill switch · usage · dead jobs"]
    end

    subgraph Shared
        LAYOUT["Layout · theme (light/dark)"]
        AUTHC["AuthContext · session · oidc.ts"]
        IMG["AuthImage<br/>fetches screenshots with bearer"]
        DOTSVG["Dot (SVG brand mark)"]
        TOAST["Toast · bits"]
    end

    subgraph APIlayer["src/api"]
        TYPES["types.ts (= CONTRACT)"]
        CLIENT["HttpApiClient<br/>bearer · errors · Idempotency-Key"]
        MOCK["MockApiClient<br/>(VITE_USE_MOCK=1)"]
    end

    Pages --> LAYOUT
    Pages --> AUTHC
    DET --> IMG
    EDIT --> IMG
    Pages --> CLIENT
    CLIENT --> TYPES
    MOCK --> TYPES
    DET -- "workshadower://run?skill_id=…" --> DotApp["Dot app"]
    CLIENT -- "/api/v1" --> Server["API"]
```

## 7. Data model

```mermaid
erDiagram
    teams ||--o{ team_members : has
    users ||--o{ team_members : in
    users ||--o{ auth_tokens : holds
    users ||--o{ recordings : records
    users ||--o{ skills : owns
    teams |o--o{ skills : "shared with"
    recordings |o--o| skills : "source of"
    skills ||--o{ skill_versions : "immutable versions"
    skills ||--o{ skill_fix_suggestions : "repair proposals"
    skills ||--o{ runs : "replayed as"
    users ||--o{ runs : runs
    runs ||--o{ run_steps : "per-step result"
    users ||--o{ assets : uploaded
    users |o--o{ llm_usage : "billed to"

    teams { uuid id PK
        text name }
    users { uuid id PK
        text email
        text name
        text role "member | admin" }
    team_members { uuid team_id FK
        uuid user_id FK }
    auth_tokens { text token_hash PK
        uuid user_id FK
        timestamptz expires_at }
    assets { uuid id PK
        text sha256 "dedup key"
        text content_type
        int bytes
        bool uploaded }
    recordings { uuid id PK
        uuid user_id FK
        text idempotency_key "unique per user"
        jsonb events "redacted"
        text_array asset_shas
        text status "received | processing | ready | failed"
        uuid skill_id }
    skills { uuid id PK
        uuid owner_id FK
        uuid team_id FK
        text visibility "private | team | org"
        text status "draft | published | archived"
        int current_version
        jsonb draft
        tsvector search_tsv
        vector embedding "optional, 1536"
        int run_count
        int run_success_count }
    skill_versions { uuid skill_id FK
        int version
        jsonb content "steps, inputs, goal…" }
    skill_fix_suggestions { uuid id PK
        uuid skill_id FK
        int step_index
        jsonb new_target }
    runs { uuid id PK
        uuid skill_id FK
        int version
        text mode "guided | auto"
        text status "running | succeeded | failed | aborted"
        text idempotency_key }
    run_steps { uuid id PK
        uuid run_id FK
        int step_index
        text status "ok | repaired | failed | skipped | confirmed"
        text strategy "deterministic | llm_repair | vision | human" }
    jobs { uuid id PK
        text kind "generate_skill | embed_skill"
        text status "queued | running | succeeded | dead"
        int attempts
        timestamptz run_after }
    app_flags { int id PK "single row"
        bool recording_enabled
        bool replay_enabled
        bool llm_enabled
        int max_recording_minutes
        text screenshot_policy }
    llm_usage { uuid user_id FK
        text purpose "skillgen | repair | embed"
        int input_tokens
        int output_tokens
        numeric est_cost_usd }
```

## 8. Flow: record → draft skill

```mermaid
sequenceDiagram
    autonumber
    actor U as Employee
    participant D as Dot (Mac)
    participant Q as Local SQLite queue
    participant A as API
    participant S as Object storage
    participant P as Postgres
    participant W as Worker
    participant L as LLM (optional)

    U->>D: click dot (red ring)
    loop while recording
        D->>D: event tap + AX lookup → structured event
        D->>D: skip password fields, screenshot at key moments
    end
    U->>D: click again
    D->>D: clean + redact on device
    D->>Q: save payload + screenshots, held (survives offline / restart)
    D->>U: "What did you just do?" (Skip / Esc / 2 min timeout all continue)
    U-->>D: one line, e.g. "Added a driver to an auto policy"
    D->>Q: add redacted intent, release hold
    loop each screenshot
        D->>A: POST /assets/presign {sha256}
        A->>P: known hash?
        alt already stored
            A-->>D: exists: true (skip upload)
        else new
            A-->>D: presigned PUT URL
            D->>S: PUT bytes (bypasses API)
        end
    end
    D->>A: POST /recordings {intent, events} + Idempotency-Key
    A->>A: check kill switch, redact again
    A->>P: insert recording + job(generate_skill)
    A-->>D: 202 received (retries return same id)
    D->>Q: delete local copy
    W->>P: claim job (FOR UPDATE SKIP LOCKED)
    W->>W: cleanup in code (merge, dedupe, flag irreversible)
    W->>W: relevance filter: keep / review / drop per event, split into tasks (see §13)
    W->>P: store filter decisions (a retry reuses them)
    alt LLM on and within budget
        W->>L: one call per task: events + intent + filter notes → skill JSON (forced tool call)
        L-->>W: JSON (validated, safety-checked)
        W->>P: log tokens + cost
    else no LLM / failure / over budget
        W->>W: rule-based writer
    end
    W->>P: create one draft per task (dropped steps kept as excluded), recording = ready
```

## 9. Flow: review, publish, search

```mermaid
sequenceDiagram
    autonumber
    actor O as Author
    actor C as Colleague
    participant Web as Web app
    participant A as API
    participant P as Postgres
    participant W as Worker
    participant L as Embeddings (optional)

    O->>Web: My recordings → open draft
    Web->>A: GET /skills/:id
    O->>Web: edit steps, mark inputs, hide screenshots, set team/visibility
    Web->>A: PATCH /skills/:id (draft)
    O->>Web: Publish
    Web->>A: POST /skills/:id/publish
    A->>P: new immutable skill_versions row, update search_tsv
    A->>P: job(embed_skill)
    W->>L: embed title + goal + steps
    W->>P: store vector
    C->>Web: “how do I file an auto claim?”
    Web->>A: GET /search?q=…
    A->>P: full-text rank ⊕ vector rank (RRF), filtered by visibility_sql()
    A-->>Web: matching skills (only ones C may see)
```

## 10. Flow: "Do it for me" replay

```mermaid
sequenceDiagram
    autonumber
    actor C as Colleague
    participant D as Dot (Replayer)
    participant H as Step HUD
    participant X as Target app (AX)
    participant A as API
    participant L as LLM (optional)

    C->>D: “Do it” (search panel or workshadower:// link)
    D->>A: GET /config (replay enabled?)
    D->>A: GET skill version
    D->>C: inputs form ({{policy_number}}…) + guided/auto
    D->>A: POST /runs + Idempotency-Key
    loop each step
        opt guided mode OR irreversible step
            D->>H: wait for “Run step” / “I'll do it” / Skip / Stop
        end
        D->>X: activate app, snapshot AX tree (≤2000 nodes)
        alt 1. deterministic match (identifier > role+label > fuzzy > path)
            D->>X: AXPress / set value / key event
        else 2. not found and LLM on
            D->>A: POST /replay/repair (text-only UI tree ≤300 nodes)
            A->>L: which element matches?
            L-->>A: target + confidence
            A-->>D: target (used only if ≥ 0.7)
            D->>X: act
            D->>A: POST /skills/:id/suggest-fix (owner reviews)
        else 3. still not found
            D->>H: “Please do this step, then click Done”
        end
        D->>X: verify expected window/element (poll ≤5 s)
        D->>A: POST /runs/:id/steps {status, strategy}
    end
    D->>A: POST /runs/:id/finish → skill health updates
```

## 11. Job queue lifecycle

Postgres is the queue. Many workers can run safely because each claim uses `FOR UPDATE SKIP LOCKED`.

```mermaid
stateDiagram-v2
    [*] --> queued: enqueue (recording received / skill published)
    queued --> running: worker claims (SKIP LOCKED)
    running --> succeeded: handler ok
    running --> queued: error → retry later (exponential backoff)
    running --> queued: worker died → reclaimed after visibility timeout
    running --> dead: attempts ≥ max (default 5)
    dead --> queued: admin clicks Retry
    succeeded --> [*]
```

## 12. Privacy and trust boundaries

```mermaid
flowchart LR
    subgraph Device["On the Mac (trusted, user's own)"]
        CAP["Capture<br/>AX events, no video"]
        SEC["Password fields<br/>never read"]
        R1["Redact #1<br/>email · card · SIN · phone<br/>URL query strings stripped"]
        LOCAL["Local queue<br/>user can see pending count"]
        CAP --> SEC --> R1 --> LOCAL
    end

    subgraph Company["Company backend (trusted)"]
        R2["Redact #2<br/>server re-checks everything"]
        VIS["visibility_sql()<br/>private / team / org"]
        AUD["Run + step log<br/>LLM usage log"]
        KS["Kill switch<br/>recording · replay"]
    end

    subgraph External["External (only if enabled)"]
        LLMX["LLM provider<br/>text only: redacted events,<br/>compact UI tree — no screenshots"]
        JEVX["TypeSafe Jev (FILTER_PROVIDER=jev)<br/>redacted compact events + intent<br/>no screenshots"]
    end

    LOCAL -- "TLS" --> R2
    R2 --> VIS
    R2 -. "optional" .-> LLMX
    R2 -. "optional" .-> JEVX
    KS -. "polled every 10 min" .-> CAP
```

Rules that hold everywhere:

- Nothing is recorded unless the red ring is showing.
- Drafts are private until the author publishes.
- Replay never performs an irreversible step without a human click.
- Screenshots never go to the LLM or to Jev.
- The step filter never deletes anything: left-out steps stay visible in the draft until a human publishes.
- Logs contain counts and states, never typed text, labels or URLs.

## 13. Intent and relevance filtering

A recording captures everything the person did, including things that are not part of the task: a
glance at Slack, a misclick they undid, a menu they opened and closed. The pipeline separates the
task from the noise without ever silently losing a needed step.

```mermaid
flowchart LR
    I["Intent<br/>'What did you just do?'"] --> F
    C["Cleaned events"] --> L["Local rules (free)<br/>undo pairs · open-then-Esc<br/>back-navigation · A→X→A detours<br/>repeats · idle gaps"]
    C --> J["Jev (optional)<br/>per event, parallel:<br/>needed? · why? · irreversible? · new task?"]
    L --> F["Combine<br/>Jev p(not needed), strong local<br/>evidence wins, never drop a<br/>committing step or a password entry"]
    J --> F
    F --> T{"p ≥ drop cut-off?"}
    T -- yes --> D["drop: step kept in draft as<br/>excluded (greyed, restorable)"]
    T -- "no, ≥ flag cut-off" --> R["review: kept, flagged"]
    T -- no --> K["keep"]
    F --> S["Task split<br/>p(new task) ≥ 0.85 and ≥3 steps each side<br/>→ one draft per task"]
```

**Where each signal comes from**

| Signal | Local rules | Jev | LLM writer |
|---|---|---|---|
| Not part of the task (p_drop) | strong patterns only (undo 0.92+, detour 0.8–0.93, exploration 0.7) | `needed` (noul) → p_drop = 1 − p | may flag a step → at most **review** |
| Reason | rule name | `reason` (choice) | free text |
| Irreversible | keyword rules | `irreversible` (noul ≥ 0.5) | keyword rules re-applied |
| New task starts | idle gap > 3 min with disjoint apps | `new_task` (noul) | — |

**Safety rules (always)**

- A step that submits/sends/deletes/pays/approves/publishes, or a password entry, is never auto-dropped (capped at review).
- The LLM alone can never drop a step; it can only flag it for review.
- Nothing is deleted: dropped steps stay in the draft as `excluded` until publish, so a reviewer can put them back.
- If every step would be excluded, the LLM draft is rejected and the rule-based draft is used; publish refuses a skill with no included steps.

**Resilience**

- Jev calls: per-call timeout (5 s), retries with backoff on 429/5xx/529 (honours `Retry-After`), bounded concurrency, and a process-wide circuit breaker (opens after 5 consecutive failures, half-opens after 60 s).
- Any event Jev can't answer uses the local rules; a filter crash keeps every step. Filtering never blocks a recording.
- Decisions are persisted before the LLM call, so a retried job reuses them (no second bill, same result).
- Admin kill switch (`filter_enabled`) and cut-offs (`filter_drop_threshold`, `filter_review_threshold`) are flags: change them live, no redeploy.

**Learning loop**

```mermaid
flowchart LR
    P["filter_decisions<br/>(event, p_drop, decision)"] --> E["Editor: reviewer keeps,<br/>puts back or leaves out"]
    E --> PUB["Publish"] --> FK["final_keep per event<br/>(kept iff a published step came from it)"]
    FK --> ST["Admin: put-back rate, missed rate,<br/>threshold curve → tune cut-offs"]
    FK --> EX["NDJSON export → evaluate a new model<br/>or train an in-house filter"]
```

`python -m app.eval_filter` scores the filter on the hand-labelled recordings in `server/eval/recordings/`
(add exported, checked examples there over time). `--min-precision` makes it a CI gate.

**What leaves the company:** with `FILTER_PROVIDER=jev`, the redacted compact event (app, window title,
element role/label, typed text after redaction, URL without query string) plus the stated intent is sent
to TypeSafe. Screenshots never are. Get infosec sign-off before turning it on.
