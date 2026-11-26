"""SQLite store. Records are kept as JSON with a few indexed columns."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

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
"""

# Documents that go through pain triage. Job posts and pricing pages have their own pipelines.
PAIN_TYPES = ("forum", "issue_tracker", "search_snippet", "review")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    # ------------------------------------------------------------ documents

    def has_document(self, doc_id: str) -> bool:
        return self.db.execute("SELECT 1 FROM documents WHERE id=?", (doc_id,)).fetchone() is not None

    def find_by_content_hash(self, content_hash: str) -> str | None:
        row = self.db.execute(
            "SELECT id FROM documents WHERE content_hash=? AND duplicate_of IS NULL LIMIT 1", (content_hash,)
        ).fetchone()
        return row["id"] if row else None

    def add_document(self, doc: Document, minhash: bytes | None = None) -> bool:
        try:
            self.db.execute(
                "INSERT INTO documents (id, source, source_type, content_hash, duplicate_of, arm_id, published_at, data, minhash)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (doc.id, doc.source, doc.source_type, doc.content_hash, doc.duplicate_of, doc.arm_id,
                 doc.published_at.isoformat() if doc.published_at else None, doc.model_dump_json(), minhash),
            )
            self.db.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def get_document(self, doc_id: str) -> Document | None:
        row = self.db.execute("SELECT data FROM documents WHERE id=?", (doc_id,)).fetchone()
        return Document.model_validate_json(row["data"]) if row else None

    def minhashes(self) -> list[tuple[str, bytes]]:
        rows = self.db.execute(
            "SELECT id, minhash FROM documents WHERE minhash IS NOT NULL AND duplicate_of IS NULL"
        ).fetchall()
        return [(r["id"], r["minhash"]) for r in rows]

    def _docs(self, sql: str, params: tuple = ()) -> list[Document]:
        return [Document.model_validate_json(r["data"]) for r in self.db.execute(sql, params).fetchall()]

    def docs_for_triage(self, limit: int | None = None) -> list[Document]:
        marks = ",".join("?" for _ in PAIN_TYPES)
        sql = (f"SELECT data FROM documents WHERE source_type IN ({marks}) AND duplicate_of IS NULL "
               "AND id NOT IN (SELECT document_id FROM triage) ORDER BY published_at DESC")
        return self._docs(sql + (f" LIMIT {int(limit)}" if limit else ""), PAIN_TYPES)

    def docs_for_extraction(self, limit: int | None = None) -> list[Document]:
        sql = ("SELECT d.data FROM documents d JOIN triage t ON t.document_id = d.id WHERE t.is_pain = 1 "
               "AND d.id NOT IN (SELECT document_id FROM extraction_runs) ORDER BY d.published_at DESC")
        return self._docs(sql + (f" LIMIT {int(limit)}" if limit else ""))

    def docs_for_jobs(self, limit: int | None = None) -> list[Document]:
        sql = ("SELECT data FROM documents WHERE source_type = 'job_post' AND duplicate_of IS NULL "
               "AND id NOT IN (SELECT document_id FROM job_runs) ORDER BY published_at DESC")
        return self._docs(sql + (f" LIMIT {int(limit)}" if limit else ""))

    def docs_for_prices(self) -> list[Document]:
        return self._docs("SELECT data FROM documents WHERE source_type = 'pricing_page' "
                          "AND id NOT IN (SELECT document_id FROM price_runs) ORDER BY published_at")

    # ------------------------------------------------------------ triage / extraction

    def save_triage(self, doc_id: str, result: TriageResult, model: str) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO triage VALUES (?,?,?,?,?,?)",
            (doc_id, int(result.is_pain), result.kind, result.reason, model, _now()),
        )
        self.db.commit()

    def triage_results(self) -> dict[str, bool]:
        return {r["document_id"]: bool(r["is_pain"]) for r in self.db.execute("SELECT * FROM triage")}

    def save_extraction(self, doc_id: str, signals: list[PainSignal], dropped: int, notes: list[str],
                        model: str) -> None:
        for signal in signals:
            self.db.execute("INSERT OR REPLACE INTO signals VALUES (?,?,?,?)",
                            (signal.id, doc_id, signal.model_dump_json(), _now()))
        self.db.execute("INSERT OR REPLACE INTO extraction_runs VALUES (?,?,?,?,?,?)",
                        (doc_id, len(signals), dropped, json.dumps(notes), model, _now()))
        self.db.commit()

    def signals_with_docs(self) -> list[tuple[PainSignal, Document]]:
        rows = self.db.execute(
            "SELECT s.data AS s, d.data AS d FROM signals s JOIN documents d ON d.id = s.document_id"
        ).fetchall()
        return [(PainSignal.model_validate_json(r["s"]), Document.model_validate_json(r["d"])) for r in rows]

    def extraction_notes(self) -> list[str]:
        notes: list[str] = []
        for row in self.db.execute("SELECT notes FROM extraction_runs"):
            notes.extend(json.loads(row["notes"] or "[]"))
        return notes

    # ------------------------------------------------------------ jobs / prices

    def save_job(self, doc_id: str, job: JobExtraction | None, notes: list[str], model: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO job_runs VALUES (?,?,?,?,?)",
                        (doc_id, job.model_dump_json() if job else None, json.dumps(notes), model, _now()))
        self.db.commit()

    def jobs_with_docs(self) -> list[tuple[JobExtraction, Document]]:
        rows = self.db.execute("SELECT j.data AS j, d.data AS d FROM job_runs j JOIN documents d "
                               "ON d.id = j.document_id WHERE j.data IS NOT NULL").fetchall()
        return [(JobExtraction.model_validate_json(r["j"]), Document.model_validate_json(r["d"])) for r in rows]

    def save_prices(self, doc_id: str, prices: PriceExtraction | None, notes: list[str], model: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO price_runs VALUES (?,?,?,?,?)",
                        (doc_id, prices.model_dump_json() if prices else None, json.dumps(notes), model, _now()))
        self.db.commit()

    def prices_with_docs(self) -> list[tuple[PriceExtraction, Document]]:
        rows = self.db.execute("SELECT p.data AS p, d.data AS d FROM price_runs p JOIN documents d "
                               "ON d.id = p.document_id WHERE p.data IS NOT NULL").fetchall()
        return [(PriceExtraction.model_validate_json(r["p"]), Document.model_validate_json(r["d"])) for r in rows]

    # ------------------------------------------------------------ clusters / labels

    def replace_clusters(self, clusters: list[Cluster]) -> None:
        self.db.execute("DELETE FROM clusters")
        for c in clusters:
            self.db.execute("INSERT INTO clusters VALUES (?,?,?,?)", (c.id, c.score, c.model_dump_json(), _now()))
        self.db.commit()

    def save_cluster(self, cluster: Cluster) -> None:
        self.db.execute("INSERT OR REPLACE INTO clusters VALUES (?,?,?,?)",
                        (cluster.id, cluster.score, cluster.model_dump_json(), _now()))
        self.db.commit()

    def clusters(self) -> list[Cluster]:
        rows = self.db.execute("SELECT data FROM clusters ORDER BY score DESC").fetchall()
        return [Cluster.model_validate_json(r["data"]) for r in rows]

    def set_label(self, target_type: str, target_id: str, label: str, note: str = "") -> None:
        self.db.execute("INSERT OR REPLACE INTO labels VALUES (?,?,?,?,?)",
                        (target_type, target_id, label, note, _now()))
        self.db.commit()

    def labels(self, target_type: str) -> dict[str, str]:
        rows = self.db.execute("SELECT target_id, label FROM labels WHERE target_type=?", (target_type,))
        return {r["target_id"]: r["label"] for r in rows}

    # ------------------------------------------------------------ planner arms

    def upsert_arms(self, arms) -> None:
        self.db.executemany(
            "INSERT OR IGNORE INTO arms (arm_id, source, family, segment, query) VALUES (?,?,?,?,?)",
            [(a.arm_id, a.source, a.family, a.segment, a.query) for a in arms],
        )
        self.db.commit()

    def record_arm_run(self, arm_id: str, new_docs: int) -> None:
        self.db.execute("UPDATE arms SET runs = runs + 1, empty_runs = empty_runs + ?, last_run = ? WHERE arm_id = ?",
                        (1 if new_docs == 0 else 0, _now(), arm_id))
        self.db.commit()

    def arm_stats(self, source: str | None = None) -> dict[str, dict]:
        sql = """
        SELECT a.arm_id, a.source, a.query, a.runs, a.empty_runs,
          (SELECT COUNT(*) FROM documents d WHERE d.arm_id = a.arm_id AND d.duplicate_of IS NULL) AS docs,
          (SELECT COUNT(DISTINCT s.document_id) FROM signals s JOIN documents d ON d.id = s.document_id
             WHERE d.arm_id = a.arm_id) AS verified
        FROM arms a""" + (" WHERE a.source = ?" if source else "")
        rows = self.db.execute(sql, (source,) if source else ()).fetchall()
        return {r["arm_id"]: dict(r) for r in rows}

    # ------------------------------------------------------------ stats

    def source_stats(self) -> list[dict]:
        rows = self.db.execute("""
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
        GROUP BY d.source ORDER BY docs DESC""").fetchall()
        return [dict(r) for r in rows]

    def close(self) -> None:
        self.db.close()
