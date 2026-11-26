"""SQLite store. Records are kept as JSON with a few indexed columns.

Thread-safe: one connection is shared across threads (FastAPI runs sync endpoints in a threadpool) and
every statement or transaction runs under an RLock. `PostgresStore` (pgstore.py) has the same interface
and semantics; `storage.open_store(settings)` picks one. New tables use CREATE IF NOT EXISTS, so older
databases upgrade in place when they are opened.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator, Mapping, Sequence

import numpy as np

from .models import Cluster, Document, JobExtraction, PainSignal, PriceExtraction, TriageResult

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    source_type TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    duplicate_of TEXT,
    arm_id TEXT,
    published_at TEXT,
    data TEXT NOT NULL,
    minhash BLOB
);
CREATE INDEX IF NOT EXISTS ix_documents_hash ON documents(content_hash);
CREATE INDEX IF NOT EXISTS ix_documents_type ON documents(source_type);
CREATE INDEX IF NOT EXISTS ix_documents_arm ON documents(arm_id);
CREATE TABLE IF NOT EXISTS triage (
    document_id TEXT PRIMARY KEY, is_pain INTEGER NOT NULL, kind TEXT, reason TEXT, model TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS extraction_runs (
    document_id TEXT PRIMARY KEY, n_signals INTEGER, n_dropped INTEGER, notes TEXT, model TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS signals (
    id TEXT PRIMARY KEY, document_id TEXT NOT NULL, data TEXT NOT NULL, created_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_signals_doc ON signals(document_id);
CREATE TABLE IF NOT EXISTS job_runs (document_id TEXT PRIMARY KEY, data TEXT, notes TEXT, model TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS price_runs (document_id TEXT PRIMARY KEY, data TEXT, notes TEXT, model TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS clusters (id TEXT PRIMARY KEY, score REAL, data TEXT NOT NULL, created_at TEXT);
CREATE TABLE IF NOT EXISTS labels (
    target_type TEXT NOT NULL, target_id TEXT NOT NULL, label TEXT NOT NULL, note TEXT, created_at TEXT,
    PRIMARY KEY (target_type, target_id)
);
CREATE TABLE IF NOT EXISTS arms (
    arm_id TEXT PRIMARY KEY, source TEXT, family TEXT, segment TEXT, query TEXT,
    runs INTEGER DEFAULT 0, empty_runs INTEGER DEFAULT 0, last_run TEXT
);
-- Platform additions: review overrides, cluster snapshots (trends), agentic deep-dive log, embedding cache.
CREATE TABLE IF NOT EXISTS overrides (
    id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS snapshot_runs (
    seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL UNIQUE, run_at TEXT NOT NULL,
    n_clusters INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS cluster_snapshots (
    run_id TEXT NOT NULL, cluster_id TEXT NOT NULL, score REAL, confidence TEXT, title TEXT,
    signal_ids TEXT NOT NULL, PRIMARY KEY (run_id, cluster_id)
);
CREATE INDEX IF NOT EXISTS ix_cluster_snapshots_cluster ON cluster_snapshots(cluster_id);
CREATE TABLE IF NOT EXISTS agent_queries (
    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, cluster_id TEXT, source TEXT, query TEXT,
    n_results INTEGER, n_new INTEGER, note TEXT, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_agent_queries_run ON agent_queries(run_id);
CREATE TABLE IF NOT EXISTS signal_embeddings (
    signal_id TEXT NOT NULL, model TEXT NOT NULL, dim INTEGER NOT NULL, embedding BLOB NOT NULL, created_at TEXT,
    PRIMARY KEY (signal_id, model)
);
"""

# Every table reset() empties, children first.
TABLES = ("signal_embeddings", "agent_queries", "cluster_snapshots", "snapshot_runs", "overrides", "labels",
          "clusters", "price_runs", "job_runs", "signals", "extraction_runs", "triage", "arms", "documents")

# Documents that go through pain triage. Job posts and pricing pages have their own pipelines.
PAIN_TYPES = ("forum", "issue_tracker", "search_snippet", "review")

OVERRIDE_KINDS = ("merge", "split")

_CHUNK = 500  # ids per IN (...) list; stays well under SQLite's bound-parameter limit


def _now() -> str:
    return _ts(datetime.now(timezone.utc))


def _ts(value: datetime) -> str:
    """UTC ISO 8601 with microseconds: one sortable text format for every timestamp both stores return."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def new_run_id(run_at: datetime) -> str:
    return f"{run_at.astimezone(timezone.utc):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:6]}"


TITLE_LIMIT = 90  # characters of canonical text shown when a cluster has no synthesized title


def cluster_title(cluster: Cluster) -> str:
    """The one display title for a cluster, used by snapshots/trends, the API and the report.

    The synthesized title when there is one, otherwise the canonical statement with whitespace collapsed and
    cut to TITLE_LIMIT characters (report._clean), so trend rows show the same title as the card they link to.
    """
    from .report import _clean
    return cluster.synthesis.title if cluster.synthesis else _clean(cluster.canonical, TITLE_LIMIT)


def snapshot_row(run_id: str, cluster: Cluster) -> tuple:
    return (run_id, cluster.id, cluster.score, cluster.confidence, cluster_title(cluster),
            json.dumps(list(cluster.signal_ids)))


def check_override_kind(kind: str) -> None:
    if kind not in OVERRIDE_KINDS:
        raise ValueError(f"override kind must be one of {OVERRIDE_KINDS}, got {kind!r}")


def _chunks(items: list, size: int = _CHUNK) -> Iterator[list]:
    for i in range(0, len(items), size):
        yield items[i:i + size]


class Store:
    backend = "sqlite"

    def __init__(self, path: Path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    # ------------------------------------------------------------ connection helpers

    def _all(self, sql: str, params: Sequence = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self.db.execute(sql, params).fetchall()

    def _one(self, sql: str, params: Sequence = ()) -> sqlite3.Row | None:
        with self._lock:
            return self.db.execute(sql, params).fetchone()

    def _write(self, sql: str, params: Sequence = ()) -> int:
        """Run one statement in its own transaction; returns the row count."""
        with self._lock, self.db:
            return self.db.execute(sql, params).rowcount

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        """Several statements in one transaction (commit on success, rollback on error)."""
        with self._lock, self.db:
            yield self.db

    # ------------------------------------------------------------ documents

    def has_document(self, doc_id: str) -> bool:
        return self._one("SELECT 1 FROM documents WHERE id=?", (doc_id,)) is not None

    def find_by_content_hash(self, content_hash: str) -> str | None:
        row = self._one("SELECT id FROM documents WHERE content_hash=? AND duplicate_of IS NULL ORDER BY rowid "
                        "LIMIT 1", (content_hash,))
        return row["id"] if row else None

    def add_document(self, doc: Document, minhash: bytes | None = None) -> bool:
        try:
            self._write(
                "INSERT INTO documents (id, source, source_type, content_hash, duplicate_of, arm_id, published_at, data, "
                "minhash) VALUES (?,?,?,?,?,?,?,?,?)",
                (doc.id, doc.source, doc.source_type, doc.content_hash, doc.duplicate_of, doc.arm_id,
                 doc.published_at.isoformat() if doc.published_at else None, doc.model_dump_json(), minhash),
            )
            return True
        except sqlite3.IntegrityError:
            return False

    def get_document(self, doc_id: str) -> Document | None:
        row = self._one("SELECT data FROM documents WHERE id=?", (doc_id,))
        return Document.model_validate_json(row["data"]) if row else None

    def document_urls(self) -> dict[str, str]:
        """url -> document id (used by evaluate.py to resolve gold items given by URL)."""
        rows = self._all("SELECT id, json_extract(data, '$.url') AS url FROM documents ORDER BY id")
        return {r["url"]: r["id"] for r in rows}

    def minhashes(self) -> list[tuple[str, bytes]]:
        rows = self._all("SELECT id, minhash FROM documents WHERE minhash IS NOT NULL AND duplicate_of IS NULL "
                         "ORDER BY rowid")
        return [(r["id"], r["minhash"]) for r in rows]

    def _docs(self, sql: str, params: Sequence = ()) -> list[Document]:
        return [Document.model_validate_json(r["data"]) for r in self._all(sql, params)]

    def docs_for_triage(self, limit: int | None = None) -> list[Document]:
        marks = ",".join("?" for _ in PAIN_TYPES)
        sql = (f"SELECT data FROM documents WHERE source_type IN ({marks}) AND duplicate_of IS NULL "
               "AND id NOT IN (SELECT document_id FROM triage) ORDER BY published_at DESC, id")
        return self._docs(sql + (f" LIMIT {int(limit)}" if limit else ""), PAIN_TYPES)

    def docs_for_extraction(self, limit: int | None = None) -> list[Document]:
        sql = ("SELECT d.data FROM documents d JOIN triage t ON t.document_id = d.id WHERE t.is_pain = 1 "
               "AND d.id NOT IN (SELECT document_id FROM extraction_runs) ORDER BY d.published_at DESC, d.id")
        return self._docs(sql + (f" LIMIT {int(limit)}" if limit else ""))

    def docs_for_jobs(self, limit: int | None = None) -> list[Document]:
        sql = ("SELECT data FROM documents WHERE source_type = 'job_post' AND duplicate_of IS NULL "
               "AND id NOT IN (SELECT document_id FROM job_runs) ORDER BY published_at DESC, id")
        return self._docs(sql + (f" LIMIT {int(limit)}" if limit else ""))

    def docs_for_prices(self) -> list[Document]:
        return self._docs("SELECT data FROM documents WHERE source_type = 'pricing_page' "
                          "AND id NOT IN (SELECT document_id FROM price_runs) ORDER BY published_at, id")

    # ------------------------------------------------------------ triage / extraction

    def save_triage(self, doc_id: str, result: TriageResult, model: str) -> None:
        self._write("INSERT OR REPLACE INTO triage VALUES (?,?,?,?,?,?)",
                    (doc_id, int(result.is_pain), result.kind, result.reason, model, _now()))

    def triage_results(self) -> dict[str, bool]:
        return {r["document_id"]: bool(r["is_pain"]) for r in self._all("SELECT document_id, is_pain FROM triage")}

    def save_extraction(self, doc_id: str, signals: list[PainSignal], dropped: int, notes: list[str],
                        model: str) -> None:
        with self._tx() as db:
            for signal in signals:  # upsert keeps the row's position, so signal order stays stable
                db.execute("INSERT INTO signals VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                           "document_id=excluded.document_id, data=excluded.data, created_at=excluded.created_at",
                           (signal.id, doc_id, signal.model_dump_json(), _now()))
            db.execute("INSERT OR REPLACE INTO extraction_runs VALUES (?,?,?,?,?,?)",
                       (doc_id, len(signals), dropped, json.dumps(notes), model, _now()))

    def signals_with_docs(self, signal_ids: Iterable[str] | None = None) -> list[tuple[PainSignal, Document]]:
        """Verified signals with their documents, in storage order. `signal_ids` filters (unknown ids are skipped)."""
        sql = "SELECT s.rowid AS seq, s.data AS s, d.data AS d FROM signals s JOIN documents d ON d.id = s.document_id"
        if signal_ids is None:
            rows = self._all(sql + " ORDER BY s.rowid")
        else:
            rows = []
            for chunk in _chunks(list(dict.fromkeys(signal_ids))):
                rows += self._all(sql + f" WHERE s.id IN ({','.join('?' for _ in chunk)})", chunk)
            rows.sort(key=lambda r: r["seq"])
        return [(PainSignal.model_validate_json(r["s"]), Document.model_validate_json(r["d"])) for r in rows]

    def extraction_notes(self) -> list[str]:
        notes: list[str] = []
        for row in self._all("SELECT notes FROM extraction_runs ORDER BY document_id"):
            notes.extend(json.loads(row["notes"] or "[]"))
        return notes

    # ------------------------------------------------------------ jobs / prices

    def save_job(self, doc_id: str, job: JobExtraction | None, notes: list[str], model: str) -> None:
        self._write("INSERT OR REPLACE INTO job_runs VALUES (?,?,?,?,?)",
                    (doc_id, job.model_dump_json() if job else None, json.dumps(notes), model, _now()))

    def jobs_with_docs(self) -> list[tuple[JobExtraction, Document]]:
        rows = self._all("SELECT j.data AS j, d.data AS d FROM job_runs j JOIN documents d "
                         "ON d.id = j.document_id WHERE j.data IS NOT NULL ORDER BY j.document_id")
        return [(JobExtraction.model_validate_json(r["j"]), Document.model_validate_json(r["d"])) for r in rows]

    def save_prices(self, doc_id: str, prices: PriceExtraction | None, notes: list[str], model: str) -> None:
        self._write("INSERT OR REPLACE INTO price_runs VALUES (?,?,?,?,?)",
                    (doc_id, prices.model_dump_json() if prices else None, json.dumps(notes), model, _now()))

    def prices_with_docs(self) -> list[tuple[PriceExtraction, Document]]:
        rows = self._all("SELECT p.data AS p, d.data AS d FROM price_runs p JOIN documents d "
                         "ON d.id = p.document_id WHERE p.data IS NOT NULL ORDER BY p.document_id")
        return [(PriceExtraction.model_validate_json(r["p"]), Document.model_validate_json(r["d"])) for r in rows]

    # ------------------------------------------------------------ clusters + snapshots

    def replace_clusters(self, clusters: list[Cluster], snapshot: bool = True,
                         run_at: datetime | None = None) -> str | None:
        """Replace every cluster. With `snapshot`, also record the run for trends; returns its run_id."""
        run_id = None
        with self._tx() as db:
            db.execute("DELETE FROM clusters")
            db.executemany("INSERT INTO clusters VALUES (?,?,?,?)",
                           [(c.id, c.score, c.model_dump_json(), _now()) for c in clusters])
            if snapshot:
                at = run_at or datetime.now(timezone.utc)
                run_id = new_run_id(at)
                db.execute("INSERT INTO snapshot_runs (run_id, run_at, n_clusters) VALUES (?,?,?)",
                           (run_id, _ts(at), len(clusters)))
                db.executemany("INSERT INTO cluster_snapshots VALUES (?,?,?,?,?,?)",
                               [snapshot_row(run_id, c) for c in clusters])
        return run_id

    def save_cluster(self, cluster: Cluster) -> None:
        """Insert or update one cluster (e.g. after synthesis). Does not start a snapshot run; it refreshes the
        cluster's title in the latest run if the cluster is part of it."""
        with self._tx() as db:
            db.execute("INSERT OR REPLACE INTO clusters VALUES (?,?,?,?)",
                       (cluster.id, cluster.score, cluster.model_dump_json(), _now()))
            db.execute("UPDATE cluster_snapshots SET title=? WHERE cluster_id=? AND run_id=(SELECT run_id FROM "
                       "snapshot_runs ORDER BY run_at DESC, seq DESC LIMIT 1)", (cluster_title(cluster), cluster.id))

    def delete_cluster(self, cluster_id: str) -> bool:
        return self._write("DELETE FROM clusters WHERE id=?", (cluster_id,)) > 0

    def clusters(self) -> list[Cluster]:
        rows = self._all("SELECT data FROM clusters ORDER BY score DESC, id")
        return [Cluster.model_validate_json(r["data"]) for r in rows]

    def get_cluster(self, cluster_id: str) -> Cluster | None:
        row = self._one("SELECT data FROM clusters WHERE id=?", (cluster_id,))
        return Cluster.model_validate_json(row["data"]) if row else None

    def snapshot_runs(self) -> list[dict]:
        """[{run_id, run_at, n_clusters}], newest first."""
        rows = self._all("SELECT run_id, run_at, n_clusters FROM snapshot_runs ORDER BY run_at DESC, seq DESC")
        return [dict(r) for r in rows]

    def snapshot(self, run_id: str) -> list[dict]:
        """[{run_id, run_at, cluster_id, score, confidence, title, signal_ids}], highest score first."""
        rows = self._all("SELECT c.run_id, r.run_at, c.cluster_id, c.score, c.confidence, c.title, c.signal_ids "
                         "FROM cluster_snapshots c JOIN snapshot_runs r ON r.run_id = c.run_id WHERE c.run_id=? "
                         "ORDER BY c.score DESC, c.cluster_id", (run_id,))
        return [{**dict(r), "signal_ids": json.loads(r["signal_ids"])} for r in rows]

    # ------------------------------------------------------------ labels

    def set_label(self, target_type: str, target_id: str, label: str, note: str = "") -> None:
        self._write("INSERT OR REPLACE INTO labels VALUES (?,?,?,?,?)",
                    (target_type, target_id, label, note or "", _now()))

    def labels(self, target_type: str) -> dict[str, str]:
        rows = self._all("SELECT target_id, label FROM labels WHERE target_type=?", (target_type,))
        return {r["target_id"]: r["label"] for r in rows}

    def get_label(self, target_type: str, target_id: str) -> dict | None:
        """{label, note, created_at} or None."""
        row = self._one("SELECT label, note, created_at FROM labels WHERE target_type=? AND target_id=?",
                        (target_type, target_id))
        return {"label": row["label"], "note": row["note"] or "", "created_at": row["created_at"]} if row else None

    def delete_label(self, target_type: str, target_id: str) -> bool:
        return self._write("DELETE FROM labels WHERE target_type=? AND target_id=?", (target_type, target_id)) > 0

    def all_labels(self, target_type: str) -> list[dict]:
        """[{target_id, label, note, created_at}], oldest first."""
        rows = self._all("SELECT target_id, label, note, created_at FROM labels WHERE target_type=? "
                         "ORDER BY created_at, target_id", (target_type,))
        return [{**dict(r), "note": r["note"] or ""} for r in rows]

    def move_label(self, target_type: str, old_id: str, new_id: str) -> bool:
        """Re-key a label (e.g. a cluster whose id changed between runs), keeping note and created_at.
        No-op (False) when the old id has no label or the new id already has one."""
        if old_id == new_id:
            return False
        with self._tx() as db:
            if db.execute("SELECT 1 FROM labels WHERE target_type=? AND target_id=?",
                          (target_type, new_id)).fetchone():
                return False
            return db.execute("UPDATE labels SET target_id=? WHERE target_type=? AND target_id=?",
                              (new_id, target_type, old_id)).rowcount > 0

    # ------------------------------------------------------------ review overrides

    def add_override(self, kind: str, payload: dict) -> int:
        """Store a manual merge/split (payload semantics live in cluster.apply_overrides). Returns its id."""
        check_override_kind(kind)
        with self._tx() as db:
            return db.execute("INSERT INTO overrides (kind, payload, created_at) VALUES (?,?,?)",
                              (kind, json.dumps(payload), _now())).lastrowid

    def overrides(self) -> list[dict]:
        """[{id, kind, payload, created_at}], oldest first."""
        rows = self._all("SELECT id, kind, payload, created_at FROM overrides ORDER BY id")
        return [{**dict(r), "payload": json.loads(r["payload"])} for r in rows]

    def delete_override(self, override_id: int) -> bool:
        return self._write("DELETE FROM overrides WHERE id=?", (int(override_id),)) > 0

    # ------------------------------------------------------------ agentic deep dive log

    def log_agent_query(self, run_id: str, cluster_id: str | None, source: str, query: str, n_results: int,
                        n_new: int, note: str = "") -> int:
        with self._tx() as db:
            return db.execute("INSERT INTO agent_queries (run_id, cluster_id, source, query, n_results, n_new, note, "
                              "created_at) VALUES (?,?,?,?,?,?,?,?)",
                              (run_id, cluster_id, source, query, int(n_results), int(n_new), note or "",
                               _now())).lastrowid

    def agent_queries(self, run_id: str | None = None) -> list[dict]:
        """[{id, run_id, cluster_id, source, query, n_results, n_new, note, created_at}], oldest first."""
        sql = "SELECT id, run_id, cluster_id, source, query, n_results, n_new, note, created_at FROM agent_queries"
        rows = self._all(sql + " WHERE run_id=? ORDER BY id", (run_id,)) if run_id else self._all(sql + " ORDER BY id")
        return [dict(r) for r in rows]

    # ------------------------------------------------------------ embedding cache

    def save_embeddings(self, model: str, vectors: Mapping[str, Sequence[float]]) -> int:
        rows = []
        for signal_id, vector in vectors.items():
            arr = np.asarray(vector, dtype=np.float32).ravel()
            rows.append((signal_id, model, int(arr.size), arr.tobytes(), _now()))
        with self._tx() as db:
            db.executemany("INSERT OR REPLACE INTO signal_embeddings VALUES (?,?,?,?,?)", rows)
        return len(rows)

    def embeddings(self, model: str, signal_ids: Iterable[str] | None = None) -> dict[str, np.ndarray]:
        sql = "SELECT signal_id, embedding FROM signal_embeddings WHERE model=?"
        if signal_ids is None:
            rows = self._all(sql, (model,))
        else:
            rows = []
            for chunk in _chunks(list(dict.fromkeys(signal_ids))):
                rows += self._all(sql + f" AND signal_id IN ({','.join('?' for _ in chunk)})", (model, *chunk))
        return {r["signal_id"]: np.frombuffer(r["embedding"], dtype=np.float32).copy() for r in rows}

    # ------------------------------------------------------------ planner arms

    def upsert_arms(self, arms) -> None:
        with self._tx() as db:
            db.executemany(
                "INSERT OR IGNORE INTO arms (arm_id, source, family, segment, query) VALUES (?,?,?,?,?)",
                [(a.arm_id, a.source, a.family, a.segment, a.query) for a in arms],
            )

    def record_arm_run(self, arm_id: str, new_docs: int) -> None:
        self._write("UPDATE arms SET runs = runs + 1, empty_runs = empty_runs + ?, last_run = ? WHERE arm_id = ?",
                    (1 if new_docs == 0 else 0, _now(), arm_id))

    def arm_stats(self, source: str | None = None) -> dict[str, dict]:
        sql = """
        SELECT a.arm_id, a.source, a.query, a.runs, a.empty_runs,
          (SELECT COUNT(*) FROM documents d WHERE d.arm_id = a.arm_id AND d.duplicate_of IS NULL) AS docs,
          (SELECT COUNT(DISTINCT s.document_id) FROM signals s JOIN documents d ON d.id = s.document_id
             WHERE d.arm_id = a.arm_id) AS verified
        FROM arms a""" + (" WHERE a.source = ?" if source else "") + " ORDER BY a.arm_id"
        rows = self._all(sql, (source,) if source else ())
        return {r["arm_id"]: dict(r) for r in rows}

    # ------------------------------------------------------------ stats

    def source_stats(self) -> list[dict]:
        rows = self._all("""
        SELECT d.source,
               COUNT(*) AS docs,
               SUM(d.duplicate_of IS NOT NULL) AS duplicates,
               SUM(t.document_id IS NOT NULL) AS triaged,
               SUM(COALESCE(t.is_pain, 0)) AS pain,
               SUM(COALESCE(e.n_signals, 0)) AS signals,
               SUM(COALESCE(e.n_dropped, 0)) AS dropped
        FROM documents d
        LEFT JOIN triage t ON t.document_id = d.id
        LEFT JOIN extraction_runs e ON e.document_id = d.id
        GROUP BY d.source ORDER BY docs DESC, d.source""")
        return [dict(r) for r in rows]

    # ------------------------------------------------------------ admin

    def reset(self) -> None:
        """Delete every row (demo seeding and tests only). The schema stays."""
        with self._tx() as db:
            for table in TABLES:
                db.execute(f"DELETE FROM {table}")
            db.execute("DELETE FROM sqlite_sequence")

    def close(self) -> None:
        with self._lock:
            self.db.close()
