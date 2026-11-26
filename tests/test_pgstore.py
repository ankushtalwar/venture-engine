"""Postgres-specific behaviour (migrations, pgvector, NULs, reconnects, parity with SQLite).

Skipped unless PAINDISC_TEST_DATABASE_URL is set; see tests/test_store_support.py.
"""

from __future__ import annotations

import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from types import SimpleNamespace

import psycopg
import pytest
from conftest import FIXTURES
from test_store_support import PG_ENV, _drop, _with_dbname, make_cluster, pg_url

from paindisc.models import Document
from paindisc.pgstore import PostgresStore, migrate, migration_files, normalize_url
from paindisc.pipeline import Pipeline
from paindisc.store import Store
from paindisc.storage import StoreProtocol, open_store


@pytest.fixture
def url():
    return pg_url()


@pytest.fixture
def pg(url):
    store = PostgresStore(url)
    store.reset()
    yield store
    store.close()


def test_migrations_are_recorded_and_idempotent(url):
    migrate(url)
    assert migrate(url) == []
    with psycopg.connect(url) as conn:
        versions = [r[0] for r in conn.execute("SELECT version FROM schema_migrations ORDER BY version")]
        assert versions == [v for v, _, _ in migration_files()]
        assert conn.execute("SELECT 1 FROM pg_extension WHERE extname = 'vector'").fetchone()
        types = dict(conn.execute("SELECT table_name || '.' || column_name, udt_name FROM information_schema.columns "
                                  "WHERE table_schema = current_schema()").fetchall())
    assert types["documents.data"] == "json" and types["documents.minhash"] == "bytea"  # json keeps key order
    assert types["cluster_snapshots.signal_ids"] == "jsonb" and types["overrides.payload"] == "json"
    assert types["signal_embeddings.embedding"] == "vector" and types["snapshot_runs.run_at"] == "timestamptz"


def test_concurrent_first_migration_is_serialised():
    """API and scheduler containers may start at once against an empty database."""
    pg_url()
    base = os.environ[PG_ENV]
    name = f"paindisc_mig_{uuid.uuid4().hex[:8]}"
    try:
        with psycopg.connect(base, autocommit=True) as conn:
            conn.execute(f'CREATE DATABASE "{name}"')
    except psycopg.Error:
        pytest.skip("role cannot create databases")
    try:
        fresh = _with_dbname(base, name)
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: migrate(fresh), range(4)))
        assert sorted(len(r) for r in results) == [0, 0, 0, len(migration_files())]
    finally:
        _drop(base, name)


def test_nul_characters_are_dropped_not_fatal(pg):
    now = datetime.now(timezone.utc)
    doc = Document(id="nul", source="test", source_type="forum", route="fixture", external_id="nul",
                   url="https://example.com/nul", text="We re-key\x00 invoices", published_at=now, captured_at=now)
    assert pg.add_document(doc)
    assert pg.get_document("nul").text == "We re-key invoices"
    pg.set_label("cluster", "c1", "accept", "odd\x00note")
    assert pg.get_label("cluster", "c1")["note"] == "oddnote"
    pg.log_agent_query("r", None, "hn", "q\x00", 0, 0, "n\x00")
    assert pg.agent_queries()[0]["query"] == "q"


def test_reads_reconnect_after_connection_loss(pg, url):
    pg.add_override("merge", {"signal_ids": ["a"]})
    pg.conn.close()
    assert len(pg.overrides()) == 1
    pid = pg.conn.info.backend_pid
    with psycopg.connect(url, autocommit=True) as admin:
        # with a timeout (PG 14+) the call returns only once the backend has exited, so the next read cannot race it
        assert admin.execute("SELECT pg_terminate_backend(%s, 5000)", (pid,)).fetchone()[0]
    assert len(pg.overrides()) == 1  # the read retries once on a fresh connection
    assert pg.conn.info.backend_pid != pid
    pg.set_label("cluster", "c1", "accept")
    assert pg.labels("cluster") == {"c1": "accept"}


def test_open_store_selects_postgres(url, tmp_path):
    store = open_store(SimpleNamespace(database_url=url, db_path=tmp_path / "unused.sqlite"))
    assert isinstance(store, PostgresStore) and store.backend == "postgres" and isinstance(store, StoreProtocol)
    store.close()
    assert not (tmp_path / "unused.sqlite").exists()
    assert normalize_url("postgresql+psycopg://u:p@h:5432/db") == "postgresql://u:p@h:5432/db"
    assert normalize_url("postgres+psycopg2://h/db") == "postgresql://h/db"


def _demo(store, settings, stem: str):
    p = Pipeline(settings, store=store, log=lambda _: None, sleep=lambda _: None)
    p.load_fixture(FIXTURES / "demo_docs.jsonl")
    p.triage()
    p.extract()
    p.jobs()
    p.prices()
    p.cluster(threshold=0.75)
    p.synthesize()
    md, _ = p.report(demo=True, stem=stem)
    clusters = [(c.id, round(c.score, 9), c.confidence, c.signal_ids, c.synthesis.title if c.synthesis else None)
                for c in store.clusters()]
    report = [line for line in md.read_text().splitlines() if not line.startswith("# Pain Report")]
    return clusters, report, store.source_stats(), store.triage_results()


def test_demo_gives_identical_results_on_sqlite_and_postgres(pg, settings, tmp_path):
    lite = Store(tmp_path / "parity.sqlite")
    sqlite_result = _demo(lite, settings, "parity-sqlite")
    postgres_result = _demo(pg, settings, "parity-postgres")
    assert sqlite_result[0] and sqlite_result == postgres_result
    lite.close()


def test_two_stores_see_each_others_writes(pg, url):
    """API process and pipeline process each hold their own store on the same database."""
    other = PostgresStore(url, run_migrations=False)
    try:
        run_id = other.replace_clusters([make_cluster("c1", ["s1"], 0.3)])
        assert [r["run_id"] for r in pg.snapshot_runs()] == [run_id]
        pg.set_label("cluster", "c1", "reject")
        assert other.get_label("cluster", "c1")["label"] == "reject"
    finally:
        other.close()
