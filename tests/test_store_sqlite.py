"""SQLite-specific storage behaviour: in-place upgrade of older databases and backend selection."""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest
from test_store_support import make_cluster

from paindisc.store import SCHEMA, Store
from paindisc.storage import StoreProtocol, is_postgres_url, migrate, open_store, redact_url

# The schema as shipped before the platform tables existed.
OLD_SCHEMA = SCHEMA.split("-- Platform additions")[0]


def test_old_database_upgrades_in_place(tmp_path):
    path = tmp_path / "old.sqlite"
    db = sqlite3.connect(path)
    db.executescript(OLD_SCHEMA)
    cluster = make_cluster("c1", ["s1", "s2"], 0.6)
    db.execute("INSERT INTO clusters VALUES (?,?,?,?)", ("c1", 0.6, cluster.model_dump_json(), "2026-01-01T00:00:00"))
    db.execute("INSERT INTO labels VALUES ('cluster','c1','accept','kept','2026-01-01T00:00:00+00:00')")
    db.commit()
    db.close()

    store = Store(path)
    assert [c.id for c in store.clusters()] == ["c1"]
    assert store.get_label("cluster", "c1") == {"label": "accept", "note": "kept",
                                                "created_at": "2026-01-01T00:00:00+00:00"}
    assert store.snapshot_runs() == [] and store.overrides() == [] and store.agent_queries() == []
    run_id = store.replace_clusters([cluster])
    assert store.snapshot(run_id)[0]["signal_ids"] == ["s1", "s2"]
    assert store.add_override("split", {"signal_ids": ["s2"]}) == 1
    store.close()

    reopened = Store(path)  # opening again is a no-op for the schema
    assert len(reopened.snapshot_runs()) == 1 and len(reopened.overrides()) == 1
    reopened.close()


def test_open_store_defaults_to_sqlite(tmp_path):
    settings = SimpleNamespace(database_url=None, db_path=tmp_path / "a.sqlite")
    store = open_store(settings)
    assert isinstance(store, Store) and store.backend == "sqlite" and isinstance(store, StoreProtocol)
    assert (tmp_path / "a.sqlite").exists()


def test_open_store_accepts_sqlite_urls_and_rejects_unknown_schemes(tmp_path):
    store = open_store(SimpleNamespace(database_url=f"sqlite:///{tmp_path}/b.sqlite", db_path=tmp_path / "x"))
    assert isinstance(store, Store) and store.path == tmp_path / "b.sqlite"
    with pytest.raises(ValueError, match="unsupported"):
        open_store(SimpleNamespace(database_url="mysql://u:secret@h/db", db_path=tmp_path / "x"))
    with pytest.raises(ValueError):
        migrate("sqlite:///x.sqlite")


def test_url_helpers():
    assert is_postgres_url("postgresql://u@h/db") and is_postgres_url("postgres://h/db")
    assert is_postgres_url("postgresql+psycopg://h/db") and not is_postgres_url("sqlite:///x")
    assert not is_postgres_url(None)
    assert redact_url("postgresql://user:s3cr:et@host:5432/db") == "postgresql://user:***@host:5432/db"
    assert redact_url("postgresql://host/db") == "postgresql://host/db"
