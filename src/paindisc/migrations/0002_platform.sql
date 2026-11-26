-- Review overrides, cluster snapshots (trend alerts), and the agentic deep-dive query log.

CREATE TABLE IF NOT EXISTS overrides (
    id BIGSERIAL PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('merge', 'split')),
    payload JSON NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS snapshot_runs (
    seq BIGSERIAL PRIMARY KEY,
    run_id TEXT NOT NULL UNIQUE,
    run_at TIMESTAMPTZ NOT NULL,
    n_clusters INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_snapshot_runs_at ON snapshot_runs(run_at);

CREATE TABLE IF NOT EXISTS cluster_snapshots (
    run_id TEXT NOT NULL REFERENCES snapshot_runs(run_id) ON DELETE CASCADE,
    cluster_id TEXT NOT NULL,
    score DOUBLE PRECISION,
    confidence TEXT,
    title TEXT,
    signal_ids JSONB NOT NULL,
    PRIMARY KEY (run_id, cluster_id)
);
CREATE INDEX IF NOT EXISTS ix_cluster_snapshots_cluster ON cluster_snapshots(cluster_id);

CREATE TABLE IF NOT EXISTS agent_queries (
    id BIGSERIAL PRIMARY KEY,
    run_id TEXT,
    cluster_id TEXT,
    source TEXT,
    query TEXT,
    n_results INTEGER,
    n_new INTEGER,
    note TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_agent_queries_run ON agent_queries(run_id);
