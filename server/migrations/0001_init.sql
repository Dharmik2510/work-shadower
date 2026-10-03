-- Core schema for Work Shadower.
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE teams (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name        text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX teams_name_uq ON teams (lower(name));

CREATE TABLE users (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    email       text NOT NULL,
    name        text NOT NULL,
    role        text NOT NULL DEFAULT 'member' CHECK (role IN ('member', 'admin')),
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX users_email_uq ON users (lower(email));

CREATE TABLE team_members (
    team_id     uuid NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (team_id, user_id)
);
CREATE INDEX team_members_user_idx ON team_members (user_id);

CREATE TABLE auth_tokens (
    token_hash  text PRIMARY KEY,           -- sha256 hex of the opaque token
    user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at  timestamptz NOT NULL DEFAULT now(),
    expires_at  timestamptz NOT NULL
);
CREATE INDEX auth_tokens_user_idx ON auth_tokens (user_id);

CREATE TABLE assets (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    sha256        text NOT NULL UNIQUE CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    content_type  text NOT NULL,
    bytes         integer NOT NULL,
    uploaded      boolean NOT NULL DEFAULT false,
    created_by    uuid REFERENCES users(id) ON DELETE SET NULL,
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE recordings (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id          uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    idempotency_key  text NOT NULL,
    title_hint       text,
    started_at       timestamptz,
    ended_at         timestamptz,
    client           jsonb NOT NULL DEFAULT '{}'::jsonb,
    events           jsonb NOT NULL,
    event_count      integer NOT NULL,
    asset_shas       text[] NOT NULL DEFAULT '{}',
    status           text NOT NULL DEFAULT 'received'
                     CHECK (status IN ('received', 'processing', 'ready', 'failed')),
    error            text,
    skill_id         uuid,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now(),
    UNIQUE (user_id, idempotency_key)
);
CREATE INDEX recordings_user_created_idx ON recordings (user_id, created_at DESC, id DESC);
CREATE INDEX recordings_asset_shas_idx ON recordings USING gin (asset_shas);

CREATE TABLE skills (
    id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id              uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    team_id               uuid REFERENCES teams(id) ON DELETE SET NULL,
    visibility            text NOT NULL DEFAULT 'private' CHECK (visibility IN ('private', 'team', 'org')),
    status                text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'published', 'archived')),
    current_version       integer,              -- NULL until first publish
    draft                 jsonb,
    source_recording_id   uuid REFERENCES recordings(id) ON DELETE SET NULL,
    search_tsv            tsvector,             -- of the current published version
    asset_shas            text[] NOT NULL DEFAULT '{}',   -- every screenshot ever referenced (draft + versions)
    published_asset_shas  text[] NOT NULL DEFAULT '{}',   -- screenshots referenced by published versions
    run_count             integer NOT NULL DEFAULT 0,      -- finished runs
    run_success_count     integer NOT NULL DEFAULT 0,
    last_run_at           timestamptz,
    created_at            timestamptz NOT NULL DEFAULT now(),
    updated_at            timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX skills_owner_idx ON skills (owner_id, updated_at DESC, id DESC);
CREATE INDEX skills_team_idx ON skills (team_id);
CREATE INDEX skills_updated_idx ON skills (updated_at DESC, id DESC);
CREATE INDEX skills_tsv_idx ON skills USING gin (search_tsv);
CREATE INDEX skills_asset_shas_idx ON skills USING gin (asset_shas);
CREATE INDEX skills_pub_asset_shas_idx ON skills USING gin (published_asset_shas);

ALTER TABLE recordings ADD CONSTRAINT recordings_skill_fk
    FOREIGN KEY (skill_id) REFERENCES skills(id) ON DELETE SET NULL;

CREATE TABLE skill_versions (
    skill_id    uuid NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
    version     integer NOT NULL CHECK (version >= 1),
    content     jsonb NOT NULL,
    created_by  uuid REFERENCES users(id) ON DELETE SET NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (skill_id, version)
);

-- Published versions are immutable.
CREATE FUNCTION ws_skill_versions_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'skill_versions rows are immutable';
END $$;
CREATE TRIGGER skill_versions_no_update BEFORE UPDATE ON skill_versions
    FOR EACH ROW EXECUTE FUNCTION ws_skill_versions_immutable();

CREATE TABLE skill_fix_suggestions (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    skill_id    uuid NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
    step_index  integer NOT NULL,
    new_target  jsonb NOT NULL,
    note        text,
    created_by  uuid REFERENCES users(id) ON DELETE SET NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX skill_fix_suggestions_skill_idx ON skill_fix_suggestions (skill_id, created_at DESC);

CREATE TABLE runs (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id          uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    skill_id         uuid NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
    version          integer NOT NULL,
    mode             text NOT NULL CHECK (mode IN ('guided', 'auto')),
    inputs           jsonb NOT NULL DEFAULT '{}'::jsonb,
    status           text NOT NULL DEFAULT 'running'
                     CHECK (status IN ('running', 'succeeded', 'failed', 'aborted')),
    error            text,
    idempotency_key  text NOT NULL,
    started_at       timestamptz NOT NULL DEFAULT now(),
    finished_at      timestamptz,
    UNIQUE (user_id, idempotency_key)
);
CREATE INDEX runs_skill_idx ON runs (skill_id, started_at DESC, id DESC);
CREATE INDEX runs_user_idx ON runs (user_id, started_at DESC, id DESC);

CREATE TABLE run_steps (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id       uuid NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    step_index   integer NOT NULL,
    status       text NOT NULL CHECK (status IN ('ok', 'repaired', 'failed', 'skipped', 'confirmed')),
    strategy     text NOT NULL CHECK (strategy IN ('deterministic', 'llm_repair', 'vision', 'human')),
    duration_ms  integer NOT NULL DEFAULT 0,
    detail       jsonb,
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX run_steps_run_idx ON run_steps (run_id, created_at);

-- Single-row feature flags table (kill switches).
CREATE TABLE app_flags (
    id                     integer PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    recording_enabled      boolean NOT NULL DEFAULT true,
    replay_enabled         boolean NOT NULL DEFAULT true,
    llm_enabled            boolean NOT NULL DEFAULT true,
    max_recording_minutes  integer NOT NULL DEFAULT 30 CHECK (max_recording_minutes > 0),
    screenshot_policy      text NOT NULL DEFAULT 'key_moments' CHECK (screenshot_policy IN ('key_moments', 'none')),
    updated_by             uuid REFERENCES users(id) ON DELETE SET NULL,
    updated_at             timestamptz NOT NULL DEFAULT now()
);
INSERT INTO app_flags (id) VALUES (1);

CREATE TABLE jobs (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    kind          text NOT NULL,
    payload       jsonb NOT NULL DEFAULT '{}'::jsonb,
    status        text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'succeeded', 'dead')),
    attempts      integer NOT NULL DEFAULT 0,
    max_attempts  integer NOT NULL DEFAULT 5,
    run_after     timestamptz NOT NULL DEFAULT now(),
    locked_at     timestamptz,
    locked_by     text,
    last_error    text,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX jobs_ready_idx ON jobs (run_after) WHERE status = 'queued';
CREATE INDEX jobs_running_idx ON jobs (locked_at) WHERE status = 'running';
CREATE INDEX jobs_status_idx ON jobs (status, updated_at DESC);

CREATE TABLE llm_usage (
    id             bigserial PRIMARY KEY,
    user_id        uuid REFERENCES users(id) ON DELETE SET NULL,
    team_id        uuid REFERENCES teams(id) ON DELETE SET NULL,
    purpose        text NOT NULL,          -- skillgen | repair | embed
    provider       text NOT NULL,
    model          text NOT NULL,
    input_tokens   integer NOT NULL DEFAULT 0,
    output_tokens  integer NOT NULL DEFAULT 0,
    est_cost_usd   numeric(12, 6) NOT NULL DEFAULT 0,
    ok             boolean NOT NULL,
    created_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX llm_usage_user_day_idx ON llm_usage (user_id, created_at);
CREATE INDEX llm_usage_created_idx ON llm_usage (created_at);

-- Weighted full-text document for a skill content JSON.
--   A: title   B: goal, tags   C: apps, step titles   D: step instructions
CREATE FUNCTION ws_skill_tsv(c jsonb) RETURNS tsvector LANGUAGE sql IMMUTABLE AS $$
    SELECT
        setweight(to_tsvector('english', coalesce(c->>'title', '')), 'A') ||
        setweight(to_tsvector('english', coalesce(c->>'goal', '') || ' ' ||
            coalesce((SELECT string_agg(t, ' ') FROM jsonb_array_elements_text(
                CASE WHEN jsonb_typeof(c->'tags') = 'array' THEN c->'tags' ELSE '[]'::jsonb END) t), '')), 'B') ||
        setweight(to_tsvector('english',
            coalesce((SELECT string_agg(a, ' ') FROM jsonb_array_elements_text(
                CASE WHEN jsonb_typeof(c->'apps') = 'array' THEN c->'apps' ELSE '[]'::jsonb END) a), '') || ' ' ||
            coalesce((SELECT string_agg(s->>'title', ' ') FROM jsonb_array_elements(
                CASE WHEN jsonb_typeof(c->'steps') = 'array' THEN c->'steps' ELSE '[]'::jsonb END) s), '')), 'C') ||
        setweight(to_tsvector('english',
            coalesce((SELECT string_agg(s->>'instruction', ' ') FROM jsonb_array_elements(
                CASE WHEN jsonb_typeof(c->'steps') = 'array' THEN c->'steps' ELSE '[]'::jsonb END) s), '')), 'D')
$$;
