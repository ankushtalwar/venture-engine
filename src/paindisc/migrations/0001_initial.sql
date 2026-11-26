-- Core pipeline tables: the Postgres twin of store.SCHEMA (SQLite). MinHash is BYTEA.
-- Whole records (`data`) are JSON, not JSONB: JSON keeps the exact text and object key order (e.g. a cluster's
-- score components), so both backends return identical records. Nothing queries inside them in SQL.
-- JSONB is used for arrays (notes) where order is preserved anyway.
-- `seq` columns keep insertion order, which the SQLite store gets from rowid.

CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    seq BIGSERIAL,
    source TEXT NOT NULL,
    source_type TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    duplicate_of TEXT,
    arm_id TEXT,
    published_at TIMESTAMPTZ,
    data JSON NOT NULL,
    minhash BYTEA
);
CREATE INDEX IF NOT EXISTS ix_documents_hash ON documents(content_hash);
CREATE INDEX IF NOT EXISTS ix_documents_type ON documents(source_type);
CREATE INDEX IF NOT EXISTS ix_documents_arm ON documents(arm_id);

CREATE TABLE IF NOT EXISTS triage (
    document_id TEXT PRIMARY KEY,
    is_pain BOOLEAN NOT NULL,
    kind TEXT,
    reason TEXT,
    model TEXT,
    created_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS extraction_runs (
    document_id TEXT PRIMARY KEY,
    n_signals INTEGER,
    n_dropped INTEGER,
    notes JSONB,
    model TEXT,
    created_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS signals (
    id TEXT PRIMARY KEY,
    seq BIGSERIAL,
    document_id TEXT NOT NULL,
    data JSON NOT NULL,
    created_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS ix_signals_doc ON signals(document_id);
CREATE INDEX IF NOT EXISTS ix_signals_seq ON signals(seq);

CREATE TABLE IF NOT EXISTS job_runs (
    document_id TEXT PRIMARY KEY,
    data JSON,
    notes JSONB,
    model TEXT,
    created_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS price_runs (
    document_id TEXT PRIMARY KEY,
    data JSON,
    notes JSONB,
    model TEXT,
    created_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS clusters (
    id TEXT PRIMARY KEY,
    score DOUBLE PRECISION,
    data JSON NOT NULL,
    created_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS labels (
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    label TEXT NOT NULL,
    note TEXT,
    created_at TIMESTAMPTZ,
    PRIMARY KEY (target_type, target_id)
);

CREATE TABLE IF NOT EXISTS arms (
    arm_id TEXT PRIMARY KEY,
    source TEXT,
    family TEXT,
    segment TEXT,
    query TEXT,
    runs INTEGER NOT NULL DEFAULT 0,
    empty_runs INTEGER NOT NULL DEFAULT 0,
    last_run TIMESTAMPTZ
);
