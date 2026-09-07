-- Application PostgreSQL bootstrap.
--
-- The official PostgreSQL image runs this file only when postgres_data is
-- created. POSTGRES_DB and POSTGRES_USER create and own the application
-- database before this script runs; Django owns application tables through
-- migrations, while the AI service creates its checkpoint tables at startup.

\set ON_ERROR_STOP on

CREATE EXTENSION IF NOT EXISTS vector;

-- F06 approved-knowledge indexes. The indexing command also applies these
-- idempotent definitions so an existing volume can be upgraded explicitly.
CREATE TABLE IF NOT EXISTS rag_index_manifests (
    physical_collection text PRIMARY KEY,
    logical_collection text NOT NULL,
    manifest jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    retired_at timestamptz NULL
);

CREATE TABLE IF NOT EXISTS rag_index_chunks (
    physical_collection text NOT NULL,
    chunk_id text NOT NULL,
    source_id text NOT NULL,
    chunk jsonb NOT NULL,
    embedding vector NOT NULL,
    PRIMARY KEY (physical_collection, chunk_id)
);

CREATE INDEX IF NOT EXISTS rag_index_chunks_source_idx
    ON rag_index_chunks (physical_collection, source_id);

CREATE TABLE IF NOT EXISTS rag_active_indexes (
    logical_collection text PRIMARY KEY,
    physical_collection text NOT NULL,
    activated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);
