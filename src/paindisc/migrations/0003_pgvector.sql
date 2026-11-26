-- pgvector embedding cache for signals. Clustering does not read it yet (TF-IDF is computed on the fly),
-- so this stays minimal: no fixed dimension and no ANN index until a model is chosen.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS signal_embeddings (
    signal_id TEXT NOT NULL,
    model TEXT NOT NULL,
    embedding vector NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (signal_id, model)
);
