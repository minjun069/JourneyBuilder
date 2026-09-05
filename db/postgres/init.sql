-- Application PostgreSQL bootstrap.
--
-- The official PostgreSQL image runs this file only when postgres_data is
-- created. POSTGRES_DB and POSTGRES_USER create and own the application
-- database before this script runs; Django owns application tables through
-- migrations, while the AI service creates its checkpoint tables at startup.

\set ON_ERROR_STOP on

CREATE EXTENSION IF NOT EXISTS vector;
