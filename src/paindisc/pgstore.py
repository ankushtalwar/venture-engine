"""Postgres + pgvector store with the same interface and semantics as the SQLite `Store` (store.py).

Whole records are JSON (exact text and key order, so records round-trip exactly as in SQLite; JSONB would
reorder object keys), arrays are JSONB, MinHash is BYTEA, timestamps are TIMESTAMPTZ (returned as UTC ISO 8601
text, like SQLite). The schema lives in versioned SQL files under `migrations/`, applied by `migrate()`; opening a
store runs it, and it is idempotent. One connection (autocommit, explicit transactions for multi-statement
writes) is shared across threads behind an RLock, and reopened if the server drops it.
"""

from __future__ import annotations

import json
import re
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from importlib import resources
from typing import Iterable, Iterator, Mapping, Sequence

import numpy as np
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import set_json_loads

from .models import Cluster, Document, JobExtraction, PainSignal, PriceExtraction, TriageResult
from .store import (PAIN_TYPES, TABLES, _ts, check_override_kind, cluster_title, new_run_id)

MIGRATION_LOCK = 0x7061696E  # pg_advisory_lock key, so concurrent starters (API, scheduler) migrate once
_C = 'COLLATE "C"'  # byte-order text sorting, matching SQLite, for deterministic tie-breaks


def normalize_url(url: str) -> str:
    """Accept SQLAlchemy-style URLs too: postgresql+psycopg://... -> postgresql://..."""
    return re.sub(r"^postgres(?:ql)?\+\w+://", "postgresql://", url.strip())


def migration_files() -> list[tuple[str, str, str]]:
    """[(version, name, sql)] in apply order, from paindisc/migrations/NNNN_name.sql."""
    out = []
    for item in resources.files("paindisc.migrations").iterdir():
        m = re.fullmatch(r"(\d{4})_(\w+)\.sql", item.name)
        if m:
            out.append((m.group(1), m.group(2), item.read_text()))
    return sorted(out)


def migrate(database_url: str) -> list[str]:
    """Apply pending migrations; returns the versions applied (empty when up to date)."""
    applied: list[str] = []
    with psycopg.connect(normalize_url(database_url), autocommit=True) as conn:
        conn.execute("SELECT pg_advisory_lock(%s)", (MIGRATION_LOCK,))
        try:
            conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, name TEXT NOT NULL, "
                         "applied_at TIMESTAMPTZ NOT NULL DEFAULT now())")
            done = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
            for version, name, sql in migration_files():
                if version in done:
                    continue
                with conn.transaction():
                    conn.execute(sql)  # no parameters: sent as a simple query, so several statements are fine
                    conn.execute("INSERT INTO schema_migrations (version, name) VALUES (%s, %s)", (version, name))
                applied.append(f"{version}_{name}")
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (MIGRATION_LOCK,))
    return applied


def _raw_json(data: bytes) -> bytes:
    return data  # JSONB comes back as text and is parsed by pydantic / json.loads, exactly like SQLite


def _clean(text: str | None) -> str | None:
    """Postgres text cannot hold NUL characters."""
    return text.replace("\x00", "") if text else text


def _strip_nul(value):
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, list):
        return [_strip_nul(v) for v in value]
    if isinstance(value, dict):
        return {_strip_nul(k): _strip_nul(v) for k, v in value.items()}
    return value


def _json_text(text: str) -> str:
    """Drop NULs on the (rare) records that contain them: JSONB rejects \\u0000 and JSON operators fail on it."""
    return json.dumps(_strip_nul(json.loads(text))) if "\\u0000" in text else text


def _vector(value) -> np.ndarray:
    """pgvector returns a Vector object (or an ndarray in older releases)."""
    return np.asarray(value.to_numpy() if hasattr(value, "to_numpy") else value, dtype=np.float32)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return _ts(value) if value else None


class PostgresStore:
    backend = "postgres"

    def __init__(self, database_url: str, run_migrations: bool = True):
        self.url = normalize_url(database_url)
        self._lock = threading.RLock()
        if run_migrations:
            migrate(self.url)
        self.conn = self._connect()

    def _connect(self) -> psycopg.Connection:
        conn = psycopg.connect(self.url, autocommit=True, row_factory=dict_row)
        conn.execute("SET TIME ZONE 'UTC'")
        set_json_loads(_raw_json, conn)
        try:
            from pgvector.psycopg import register_vector
            register_vector(conn)
        except psycopg.ProgrammingError:  # extension not installed yet (store opened without migrations)
            pass
        return conn

    # ------------------------------------------------------------ connection helpers

    def _c(self) -> psycopg.Connection:
        if self.conn.closed or self.conn.broken:
            self.conn = self._connect()
        return self.conn

    def _read(self, sql: str, params: Sequence | None, many: bool):
        """SELECTs only: if the server dropped the connection, reconnect and retry once. Writes never retry
        (after a dropped connection it is unknown whether the commit happened)."""
        with self._lock:
            try:
                cur = self._c().execute(sql, params)
            except psycopg.OperationalError:
                if not (self.conn.closed or self.conn.broken):
                    raise
                cur = self._c().execute(sql, params)
            return cur.fetchall() if many else cur.fetchone()

    def _all(self, sql: str, params: Sequence | None = None) -> list[dict]:
        return self._read(sql, params, many=True)

    def _one(self, sql: str, params: Sequence | None = None) -> dict | None:
        return self._read(sql, params, many=False)

    def _write(self, sql: str, params: Sequence | None = None) -> int:
        with self._lock:
            return self._c().execute(sql, params).rowcount

    def _returning(self, sql: str, params: Sequence) -> dict:
        """A write with RETURNING (no retry)."""
        with self._lock:
            return self._c().execute(sql, params).fetchone()

    @contextmanager
    def _tx(self) -> Iterator[psycopg.Connection]:
        with self._lock:
            conn = self._c()
            with conn.transaction():
                yield conn

    # ------------------------------------------------------------ documents

    def has_document(self, doc_id: str) -> bool:
        return self._one("SELECT 1 FROM documents WHERE id=%s", (doc_id,)) is not None

    def find_by_content_hash(self, content_hash: str) -> str | None:
        row = self._one("SELECT id FROM documents WHERE content_hash=%s AND duplicate_of IS NULL ORDER BY seq LIMIT 1",
                        (content_hash,))
        return row["id"] if row else None

    def add_document(self, doc: Document, minhash: bytes | None = None) -> bool:
        return self._write(
            "INSERT INTO documents (id, source, source_type, content_hash, duplicate_of, arm_id, published_at, data, "
            "minhash) VALUES (%s,%s,%s,%s,%s,%s,%s,%s::json,%s) ON CONFLICT (id) DO NOTHING",
            (doc.id, doc.source, doc.source_type, doc.content_hash, doc.duplicate_of, doc.arm_id,
             _aware(doc.published_at) if doc.published_at else None, _json_text(doc.model_dump_json()), minhash),
        ) == 1

    def get_document(self, doc_id: str) -> Document | None:
        row = self._one("SELECT data FROM documents WHERE id=%s", (doc_id,))
        return Document.model_validate_json(row["data"]) if row else None

    def document_urls(self) -> dict[str, str]:
        rows = self._all(f"SELECT id, data->>'url' AS url FROM documents ORDER BY id {_C}")
        return {r["url"]: r["id"] for r in rows}

    def minhashes(self) -> list[tuple[str, bytes]]:
        rows = self._all("SELECT id, minhash FROM documents WHERE minhash IS NOT NULL AND duplicate_of IS NULL "
                         "ORDER BY seq")
        return [(r["id"], bytes(r["minhash"])) for r in rows]

    def _docs(self, sql: str, params: Sequence | None = None) -> list[Document]:
        return [Document.model_validate_json(r["data"]) for r in self._all(sql, params)]

    @staticmethod
    def _limit(limit: int | None) -> str:
        return f" LIMIT {int(limit)}" if limit else ""

    def docs_for_triage(self, limit: int | None = None) -> list[Document]:
        sql = ("SELECT data FROM documents d WHERE source_type = ANY(%s) AND duplicate_of IS NULL "
               "AND NOT EXISTS (SELECT 1 FROM triage t WHERE t.document_id = d.id) "
               f"ORDER BY published_at DESC NULLS LAST, id {_C}")
        return self._docs(sql + self._limit(limit), (list(PAIN_TYPES),))

    def docs_for_extraction(self, limit: int | None = None) -> list[Document]:
        sql = ("SELECT d.data FROM documents d JOIN triage t ON t.document_id = d.id WHERE t.is_pain "
               "AND NOT EXISTS (SELECT 1 FROM extraction_runs e WHERE e.document_id = d.id) "
               f"ORDER BY d.published_at DESC NULLS LAST, d.id {_C}")
        return self._docs(sql + self._limit(limit))

    def docs_for_jobs(self, limit: int | None = None) -> list[Document]:
        sql = ("SELECT data FROM documents d WHERE source_type = 'job_post' AND duplicate_of IS NULL "
               "AND NOT EXISTS (SELECT 1 FROM job_runs j WHERE j.document_id = d.id) "
               f"ORDER BY published_at DESC NULLS LAST, id {_C}")
        return self._docs(sql + self._limit(limit))

    def docs_for_prices(self) -> list[Document]:
        return self._docs("SELECT data FROM documents d WHERE source_type = 'pricing_page' "
                          "AND NOT EXISTS (SELECT 1 FROM price_runs p WHERE p.document_id = d.id) "
                          f"ORDER BY published_at NULLS FIRST, id {_C}")

    # ------------------------------------------------------------ triage / extraction

    def save_triage(self, doc_id: str, result: TriageResult, model: str) -> None:
        self._write("INSERT INTO triage (document_id, is_pain, kind, reason, model, created_at) "
                    "VALUES (%s,%s,%s,%s,%s,now()) ON CONFLICT (document_id) DO UPDATE SET is_pain=EXCLUDED.is_pain, "
                    "kind=EXCLUDED.kind, reason=EXCLUDED.reason, model=EXCLUDED.model, created_at=EXCLUDED.created_at",
                    (doc_id, bool(result.is_pain), result.kind, _clean(result.reason), model))

    def triage_results(self) -> dict[str, bool]:
        return {r["document_id"]: bool(r["is_pain"]) for r in self._all("SELECT document_id, is_pain FROM triage")}

    def save_extraction(self, doc_id: str, signals: list[PainSignal], dropped: int, notes: list[str],
                        model: str) -> None:
        with self._tx() as conn:
            if signals:
                conn.cursor().executemany(
                    "INSERT INTO signals (id, document_id, data, created_at) VALUES (%s,%s,%s::json,now()) "
                    "ON CONFLICT (id) DO UPDATE SET document_id=EXCLUDED.document_id, data=EXCLUDED.data, "
                    "created_at=EXCLUDED.created_at",
                    [(s.id, doc_id, _json_text(s.model_dump_json())) for s in signals])
            conn.execute("INSERT INTO extraction_runs (document_id, n_signals, n_dropped, notes, model, created_at) "
                         "VALUES (%s,%s,%s,%s::jsonb,%s,now()) ON CONFLICT (document_id) DO UPDATE SET "
                         "n_signals=EXCLUDED.n_signals, n_dropped=EXCLUDED.n_dropped, notes=EXCLUDED.notes, "
                         "model=EXCLUDED.model, created_at=EXCLUDED.created_at",
                         (doc_id, len(signals), dropped, _json_text(json.dumps(notes)), model))

    def signals_with_docs(self, signal_ids: Iterable[str] | None = None) -> list[tuple[PainSignal, Document]]:
        sql = "SELECT s.data AS s, d.data AS d FROM signals s JOIN documents d ON d.id = s.document_id"
        if signal_ids is None:
            rows = self._all(sql + " ORDER BY s.seq")
        else:
            ids = list(dict.fromkeys(signal_ids))
            rows = self._all(sql + " WHERE s.id = ANY(%s) ORDER BY s.seq", (ids,)) if ids else []
        return [(PainSignal.model_validate_json(r["s"]), Document.model_validate_json(r["d"])) for r in rows]

    def extraction_notes(self) -> list[str]:
        notes: list[str] = []
        for row in self._all(f"SELECT notes FROM extraction_runs ORDER BY document_id {_C}"):
            notes.extend(json.loads(row["notes"] or b"[]"))
        return notes

    # ------------------------------------------------------------ jobs / prices

    def _save_run(self, table: str, doc_id: str, data: str | None, notes: list[str], model: str) -> None:
        self._write(f"INSERT INTO {table} (document_id, data, notes, model, created_at) "
                    "VALUES (%s,%s::json,%s::jsonb,%s,now()) ON CONFLICT (document_id) DO UPDATE SET "
                    "data=EXCLUDED.data, notes=EXCLUDED.notes, model=EXCLUDED.model, created_at=EXCLUDED.created_at",
                    (doc_id, _json_text(data) if data else None, _json_text(json.dumps(notes)), model))

    def save_job(self, doc_id: str, job: JobExtraction | None, notes: list[str], model: str) -> None:
        self._save_run("job_runs", doc_id, job.model_dump_json() if job else None, notes, model)

    def jobs_with_docs(self) -> list[tuple[JobExtraction, Document]]:
        rows = self._all("SELECT j.data AS j, d.data AS d FROM job_runs j JOIN documents d "
                         f"ON d.id = j.document_id WHERE j.data IS NOT NULL ORDER BY j.document_id {_C}")
        return [(JobExtraction.model_validate_json(r["j"]), Document.model_validate_json(r["d"])) for r in rows]

    def save_prices(self, doc_id: str, prices: PriceExtraction | None, notes: list[str], model: str) -> None:
        self._save_run("price_runs", doc_id, prices.model_dump_json() if prices else None, notes, model)

    def prices_with_docs(self) -> list[tuple[PriceExtraction, Document]]:
        rows = self._all("SELECT p.data AS p, d.data AS d FROM price_runs p JOIN documents d "
                         f"ON d.id = p.document_id WHERE p.data IS NOT NULL ORDER BY p.document_id {_C}")
        return [(PriceExtraction.model_validate_json(r["p"]), Document.model_validate_json(r["d"])) for r in rows]

    # ------------------------------------------------------------ clusters + snapshots

    def replace_clusters(self, clusters: list[Cluster], snapshot: bool = True,
                         run_at: datetime | None = None) -> str | None:
        run_id = None
        with self._tx() as conn:
            conn.execute("DELETE FROM clusters")
            if clusters:
                conn.cursor().executemany(
                    "INSERT INTO clusters (id, score, data, created_at) VALUES (%s,%s,%s::json,now())",
                    [(c.id, c.score, _json_text(c.model_dump_json())) for c in clusters])
            if snapshot:
                at = _aware(run_at or datetime.now(timezone.utc))
                run_id = new_run_id(at)
                conn.execute("INSERT INTO snapshot_runs (run_id, run_at, n_clusters) VALUES (%s,%s,%s)",
                             (run_id, at, len(clusters)))
                if clusters:
                    conn.cursor().executemany(
                        "INSERT INTO cluster_snapshots (run_id, cluster_id, score, confidence, title, signal_ids) "
                        "VALUES (%s,%s,%s,%s,%s,%s::jsonb)",
                        [(run_id, c.id, c.score, c.confidence, _clean(cluster_title(c)), json.dumps(c.signal_ids))
                         for c in clusters])
        return run_id

    def save_cluster(self, cluster: Cluster) -> None:
        with self._tx() as conn:
            conn.execute("INSERT INTO clusters (id, score, data, created_at) VALUES (%s,%s,%s::json,now()) "
                         "ON CONFLICT (id) DO UPDATE SET score=EXCLUDED.score, data=EXCLUDED.data, "
                         "created_at=EXCLUDED.created_at",
                         (cluster.id, cluster.score, _json_text(cluster.model_dump_json())))
            conn.execute("UPDATE cluster_snapshots SET title=%s WHERE cluster_id=%s AND run_id=(SELECT run_id FROM "
                         "snapshot_runs ORDER BY run_at DESC, seq DESC LIMIT 1)",
                         (_clean(cluster_title(cluster)), cluster.id))

    def delete_cluster(self, cluster_id: str) -> bool:
        return self._write("DELETE FROM clusters WHERE id=%s", (cluster_id,)) > 0

    def clusters(self) -> list[Cluster]:
        rows = self._all(f"SELECT data FROM clusters ORDER BY score DESC, id {_C}")
        return [Cluster.model_validate_json(r["data"]) for r in rows]

    def get_cluster(self, cluster_id: str) -> Cluster | None:
        row = self._one("SELECT data FROM clusters WHERE id=%s", (cluster_id,))
        return Cluster.model_validate_json(row["data"]) if row else None

    def snapshot_runs(self) -> list[dict]:
        rows = self._all("SELECT run_id, run_at, n_clusters FROM snapshot_runs ORDER BY run_at DESC, seq DESC")
        return [{**r, "run_at": _iso(r["run_at"])} for r in rows]

    def snapshot(self, run_id: str) -> list[dict]:
        rows = self._all("SELECT c.run_id, r.run_at, c.cluster_id, c.score, c.confidence, c.title, c.signal_ids "
                         "FROM cluster_snapshots c JOIN snapshot_runs r ON r.run_id = c.run_id WHERE c.run_id=%s "
                         f"ORDER BY c.score DESC, c.cluster_id {_C}", (run_id,))
        return [{**r, "run_at": _iso(r["run_at"]), "signal_ids": json.loads(r["signal_ids"])} for r in rows]

    # ------------------------------------------------------------ labels

    def set_label(self, target_type: str, target_id: str, label: str, note: str = "") -> None:
        self._write("INSERT INTO labels (target_type, target_id, label, note, created_at) VALUES (%s,%s,%s,%s,now()) "
                    "ON CONFLICT (target_type, target_id) DO UPDATE SET label=EXCLUDED.label, note=EXCLUDED.note, "
                    "created_at=EXCLUDED.created_at", (target_type, target_id, label, _clean(note) or ""))

    def labels(self, target_type: str) -> dict[str, str]:
        rows = self._all("SELECT target_id, label FROM labels WHERE target_type=%s", (target_type,))
        return {r["target_id"]: r["label"] for r in rows}

    def get_label(self, target_type: str, target_id: str) -> dict | None:
        row = self._one("SELECT label, note, created_at FROM labels WHERE target_type=%s AND target_id=%s",
                        (target_type, target_id))
        if row is None:
            return None
        return {"label": row["label"], "note": row["note"] or "", "created_at": _iso(row["created_at"])}

    def delete_label(self, target_type: str, target_id: str) -> bool:
        return self._write("DELETE FROM labels WHERE target_type=%s AND target_id=%s", (target_type, target_id)) > 0

    def all_labels(self, target_type: str) -> list[dict]:
        rows = self._all("SELECT target_id, label, note, created_at FROM labels WHERE target_type=%s "
                         f"ORDER BY created_at, target_id {_C}", (target_type,))
        return [{**r, "note": r["note"] or "", "created_at": _iso(r["created_at"])} for r in rows]

    def move_label(self, target_type: str, old_id: str, new_id: str) -> bool:
        if old_id == new_id:
            return False
        with self._tx() as conn:
            if conn.execute("SELECT 1 FROM labels WHERE target_type=%s AND target_id=%s",
                            (target_type, new_id)).fetchone():
                return False
            return conn.execute("UPDATE labels SET target_id=%s WHERE target_type=%s AND target_id=%s",
                                (new_id, target_type, old_id)).rowcount > 0

    # ------------------------------------------------------------ review overrides

    def add_override(self, kind: str, payload: dict) -> int:
        check_override_kind(kind)
        row = self._returning("INSERT INTO overrides (kind, payload, created_at) VALUES (%s,%s::json,now()) "
                              "RETURNING id", (kind, _json_text(json.dumps(payload))))
        return int(row["id"])

    def overrides(self) -> list[dict]:
        rows = self._all("SELECT id, kind, payload, created_at FROM overrides ORDER BY id")
        return [{**r, "payload": json.loads(r["payload"]), "created_at": _iso(r["created_at"])} for r in rows]

    def delete_override(self, override_id: int) -> bool:
        return self._write("DELETE FROM overrides WHERE id=%s", (int(override_id),)) > 0

    # ------------------------------------------------------------ agentic deep dive log

    def log_agent_query(self, run_id: str, cluster_id: str | None, source: str, query: str, n_results: int,
                        n_new: int, note: str = "") -> int:
        row = self._returning("INSERT INTO agent_queries (run_id, cluster_id, source, query, n_results, n_new, note, "
                              "created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,now()) RETURNING id",
                              (run_id, cluster_id, source, _clean(query), int(n_results), int(n_new),
                               _clean(note) or ""))
        return int(row["id"])

    def agent_queries(self, run_id: str | None = None) -> list[dict]:
        sql = "SELECT id, run_id, cluster_id, source, query, n_results, n_new, note, created_at FROM agent_queries"
        rows = (self._all(sql + " WHERE run_id=%s ORDER BY id", (run_id,)) if run_id
                else self._all(sql + " ORDER BY id"))
        return [{**r, "created_at": _iso(r["created_at"])} for r in rows]

    # ------------------------------------------------------------ embedding cache (pgvector)

    def save_embeddings(self, model: str, vectors: Mapping[str, Sequence[float]]) -> int:
        rows = [(sid, model, np.asarray(v, dtype=np.float32).ravel()) for sid, v in vectors.items()]
        if rows:
            with self._tx() as conn:
                conn.cursor().executemany(
                    "INSERT INTO signal_embeddings (signal_id, model, embedding) VALUES (%s,%s,%s) "
                    "ON CONFLICT (signal_id, model) DO UPDATE SET embedding=EXCLUDED.embedding, created_at=now()",
                    rows)
        return len(rows)

    def embeddings(self, model: str, signal_ids: Iterable[str] | None = None) -> dict[str, np.ndarray]:
        sql = "SELECT signal_id, embedding FROM signal_embeddings WHERE model=%s"
        if signal_ids is None:
            rows = self._all(sql, (model,))
        else:
            ids = list(dict.fromkeys(signal_ids))
            rows = self._all(sql + " AND signal_id = ANY(%s)", (model, ids)) if ids else []
        return {r["signal_id"]: _vector(r["embedding"]) for r in rows}

    # ------------------------------------------------------------ planner arms

    def upsert_arms(self, arms) -> None:
        rows = [(a.arm_id, a.source, a.family, a.segment, _clean(a.query)) for a in arms]
        if rows:
            with self._tx() as conn:
                conn.cursor().executemany("INSERT INTO arms (arm_id, source, family, segment, query) "
                                          "VALUES (%s,%s,%s,%s,%s) ON CONFLICT (arm_id) DO NOTHING", rows)

    def record_arm_run(self, arm_id: str, new_docs: int) -> None:
        self._write("UPDATE arms SET runs = runs + 1, empty_runs = empty_runs + %s, last_run = now() WHERE arm_id = %s",
                    (1 if new_docs == 0 else 0, arm_id))

    def arm_stats(self, source: str | None = None) -> dict[str, dict]:
        sql = """
        SELECT a.arm_id, a.source, a.query, a.runs, a.empty_runs,
          (SELECT COUNT(*) FROM documents d WHERE d.arm_id = a.arm_id AND d.duplicate_of IS NULL) AS docs,
          (SELECT COUNT(DISTINCT s.document_id) FROM signals s JOIN documents d ON d.id = s.document_id
             WHERE d.arm_id = a.arm_id) AS verified
        FROM arms a""" + (" WHERE a.source = %s" if source else "") + f" ORDER BY a.arm_id {_C}"
        rows = self._all(sql, (source,) if source else None)
        return {r["arm_id"]: r for r in rows}

    # ------------------------------------------------------------ stats

    def source_stats(self) -> list[dict]:
        return self._all(f"""
        SELECT d.source,
               COUNT(*) AS docs,
               COUNT(d.duplicate_of) AS duplicates,
               COUNT(t.document_id) AS triaged,
               SUM(COALESCE(t.is_pain::int, 0)) AS pain,
               SUM(COALESCE(e.n_signals, 0)) AS signals,
               SUM(COALESCE(e.n_dropped, 0)) AS dropped
        FROM documents d
        LEFT JOIN triage t ON t.document_id = d.id
        LEFT JOIN extraction_runs e ON e.document_id = d.id
        GROUP BY d.source ORDER BY docs DESC, d.source {_C}""")

    # ------------------------------------------------------------ admin

    def reset(self) -> None:
        """Delete every row (demo seeding and tests only). Schema and schema_migrations stay."""
        with self._tx() as conn:
            conn.execute(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY")

    def close(self) -> None:
        with self._lock:
            self.conn.close()
