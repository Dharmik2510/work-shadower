-- Intent capture + relevance filtering (keep / review / drop) + reviewer feedback.

-- What the author said they did, asked on the Mac when recording stops ("What did you just do?").
ALTER TABLE recordings ADD COLUMN IF NOT EXISTS intent text;
-- How the recording was split into skills (one per detected task) and how it was filtered.
ALTER TABLE recordings ADD COLUMN IF NOT EXISTS filter_summary jsonb;

-- One row per cleaned event of a recording: what the filter predicted, and (after publish)
-- what the reviewer decided. This is the calibration / training set for the filter.
CREATE TABLE IF NOT EXISTS filter_decisions (
    id              bigserial PRIMARY KEY,
    recording_id    uuid NOT NULL REFERENCES recordings(id) ON DELETE CASCADE,
    skill_id        uuid REFERENCES skills(id) ON DELETE SET NULL,
    segment         integer NOT NULL DEFAULT 0,
    seq             integer NOT NULL,
    event           jsonb NOT NULL,          -- compact, redacted event (what the filter saw)
    decision        text NOT NULL CHECK (decision IN ('keep', 'review', 'drop')),
    reason          text NOT NULL,
    p_drop          real NOT NULL CHECK (p_drop >= 0 AND p_drop <= 1),
    source          text NOT NULL,           -- local | jev | jev+local
    model           text,
    -- filled on publish
    final_keep      boolean,
    reviewed_version integer,
    reviewed_at     timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (recording_id, seq)
);
CREATE INDEX IF NOT EXISTS filter_decisions_skill_idx ON filter_decisions (skill_id);
CREATE INDEX IF NOT EXISTS filter_decisions_reviewed_idx ON filter_decisions (reviewed_at) WHERE reviewed_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS filter_decisions_created_idx ON filter_decisions (created_at);

-- Admin-tunable filter settings (kill switch + thresholds), no redeploy needed.
ALTER TABLE app_flags ADD COLUMN IF NOT EXISTS filter_enabled boolean NOT NULL DEFAULT true;
ALTER TABLE app_flags ADD COLUMN IF NOT EXISTS filter_drop_threshold real NOT NULL DEFAULT 0.9
    CHECK (filter_drop_threshold > 0 AND filter_drop_threshold <= 1);
ALTER TABLE app_flags ADD COLUMN IF NOT EXISTS filter_review_threshold real NOT NULL DEFAULT 0.6
    CHECK (filter_review_threshold > 0 AND filter_review_threshold <= 1);
ALTER TABLE app_flags ADD COLUMN IF NOT EXISTS split_tasks_enabled boolean NOT NULL DEFAULT true;
ALTER TABLE app_flags ADD CONSTRAINT app_flags_filter_thresholds_ck
    CHECK (filter_review_threshold <= filter_drop_threshold);

-- A recording that contained several tasks becomes several drafts (in task order).
ALTER TABLE recordings ADD COLUMN IF NOT EXISTS skill_ids uuid[] NOT NULL DEFAULT '{}';

-- Anthropic Message Batches (LLM_BATCH_MODE): the pending batch for a recording.
ALTER TABLE recordings ADD COLUMN IF NOT EXISTS llm_batch_id text;
ALTER TABLE recordings ADD COLUMN IF NOT EXISTS llm_batch_submitted_at timestamptz;
CREATE INDEX IF NOT EXISTS skills_source_recording_idx ON skills (source_recording_id);
