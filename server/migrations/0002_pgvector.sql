-- Optional pgvector support. Degrades gracefully: if the extension is not
-- installable (or the runner sets ws.disable_vector=on), nothing is created and
-- search runs full-text only. Embedding dimension is fixed at 1536.
DO $$
BEGIN
    IF coalesce(current_setting('ws.disable_vector', true), 'off') = 'on' THEN
        RAISE NOTICE 'pgvector disabled by configuration';
        RETURN;
    END IF;
    BEGIN
        CREATE EXTENSION IF NOT EXISTS vector;
    EXCEPTION WHEN OTHERS THEN
        RAISE NOTICE 'pgvector unavailable (%), continuing with full-text search only', SQLERRM;
        RETURN;
    END;
    EXECUTE 'ALTER TABLE skills ADD COLUMN IF NOT EXISTS embedding vector(1536)';
    EXECUTE 'CREATE INDEX IF NOT EXISTS skills_embedding_idx ON skills USING hnsw (embedding vector_cosine_ops)';
END $$;
