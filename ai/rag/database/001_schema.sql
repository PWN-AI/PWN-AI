CREATE EXTENSION IF NOT EXISTS vector;
CREATE SCHEMA IF NOT EXISTS pwnai_rag;

CREATE TABLE IF NOT EXISTS pwnai_rag.documents (
    id text PRIMARY KEY,
    tool text NOT NULL,
    category text NOT NULL,
    content text NOT NULL,
    embedded_content_hash text,
    embedding vector(1024),
    search_text tsvector GENERATED ALWAYS AS (
        setweight(to_tsvector('english', tool || ' ' || category), 'A') ||
        setweight(to_tsvector('english', content), 'B')
    ) STORED
);

CREATE TABLE IF NOT EXISTS pwnai_rag.metadata (
    key text PRIMARY KEY,
    value jsonb NOT NULL
);

CREATE INDEX IF NOT EXISTS documents_tool_idx
    ON pwnai_rag.documents (tool);
CREATE INDEX IF NOT EXISTS documents_search_idx
    ON pwnai_rag.documents USING gin (search_text);
CREATE INDEX IF NOT EXISTS documents_embedding_hnsw
    ON pwnai_rag.documents USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);
