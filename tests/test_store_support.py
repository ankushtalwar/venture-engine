"""Shared helpers for the storage tests (no tests here).

Postgres tests run only when PAINDISC_TEST_DATABASE_URL is set, e.g.
  docker run -d --name paindisc-pg-dbeng -e POSTGRES_PASSWORD=pg -p 127.0.0.1:55433:5432 pgvector/pgvector:pg17
  PAINDISC_TEST_DATABASE_URL=postgresql://postgres:pg@127.0.0.1:55433/postgres uv run pytest -q
When the role may create databases, the suite runs in a throwaway database (dropped at exit), so migrations
are exercised from scratch and nothing else on that server is touched; otherwise it uses the given database.
"""

from __future__ import annotations

import atexit
import functools
import os
import uuid
from types import SimpleNamespace
from urllib.parse import urlsplit, urlunsplit

import pytest

from paindisc.models import Cluster, Synthesis

PG_ENV = "PAINDISC_TEST_DATABASE_URL"


def _with_dbname(url: str, name: str) -> str:
    if "://" in url:
        parts = urlsplit(url)
        return urlunsplit(parts._replace(path=f"/{name}"))
    from psycopg.conninfo import make_conninfo
    return make_conninfo(url, dbname=name)


def _drop(base: str, name: str) -> None:
    import psycopg
    try:
        with psycopg.connect(base, autocommit=True) as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    except psycopg.Error:
        pass


@functools.lru_cache(maxsize=1)
def _test_database() -> str:
    import psycopg
    base = os.environ[PG_ENV]
    name = f"paindisc_test_{uuid.uuid4().hex[:10]}"
    try:
        with psycopg.connect(base, autocommit=True) as conn:
            conn.execute(f'CREATE DATABASE "{name}"')
    except psycopg.Error:
        return base  # no CREATEDB privilege: use the given database (tests call reset())
    atexit.register(_drop, base, name)
    return _with_dbname(base, name)


def pg_url() -> str:
    """URL of the Postgres test database, or skip the calling test."""
    if not os.environ.get(PG_ENV):
        pytest.skip(f"set {PG_ENV} to run Postgres tests")
    return _test_database()


def open_backend(kind: str, tmp_path):
    """A fresh, empty store of the given kind ("sqlite" or "postgres")."""
    if kind == "sqlite":
        from paindisc.store import Store
        return Store(tmp_path / f"{kind}.sqlite")
    from paindisc.pgstore import PostgresStore
    store = PostgresStore(pg_url())
    store.reset()
    return store


def make_cluster(cid: str, signal_ids: list[str], score: float, title: str | None = None,
                 confidence: str = "Low") -> Cluster:
    return Cluster(id=cid, canonical=f"canonical {cid}", signal_ids=signal_ids, n_signals=len(signal_ids),
                   n_authors=len(signal_ids), source_types=["forum"], score=score, confidence=confidence,
                   synthesis=Synthesis(title=title, summary="s", hypotheses=[]) if title else None)


def arm(arm_id: str, source: str = "hn", query: str = "invoice re-keying") -> SimpleNamespace:
    return SimpleNamespace(arm_id=arm_id, source=source, family="pain", segment="smb", query=query)
