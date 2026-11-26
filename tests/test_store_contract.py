"""Store contract: one suite run against SQLite (always) and Postgres (when PAINDISC_TEST_DATABASE_URL is set).

Both backends must return the same values in the same order. Anything that differs is a bug in one of them.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest
from conftest import FIXTURES, ev, make_pair
from test_store_support import arm, make_cluster, open_backend

from paindisc.models import Document, JobExtraction, PriceExtraction, PriceTier, Synthesis, TriageResult
from paindisc.pipeline import Pipeline
from paindisc.trends import compute_trends
from paindisc.verify import normalize, quote_in_text

NOW = datetime.now(timezone.utc).replace(microsecond=123456)
PAIN = TriageResult(is_pain=True, kind="business_pain", reason="recurring")
NOT_PAIN = TriageResult(is_pain=False, kind="other", reason="no")


@pytest.fixture(params=["sqlite", "postgres"])
def store(request, tmp_path):
    s = open_backend(request.param, tmp_path)
    yield s
    s.close()


def doc(ext: str, source_type: str = "forum", days_ago: int | None = 1, text: str | None = None,
        duplicate_of: str | None = None, arm_id: str | None = None, source: str = "test") -> Document:
    at = NOW - timedelta(days=days_ago or 0)
    return Document(id=f"doc-{ext}", source=source, source_type=source_type, route="fixture", external_id=ext,
                    url=f"https://example.com/{ext}", title=f"title {ext}",
                    text=text or f"We re-key invoices by hand every week, case {ext}.", author_hash=f"a-{ext}",
                    published_at=at if days_ago is not None else None, captured_at=at, duplicate_of=duplicate_of,
                    arm_id=arm_id, metrics={"points": 3, "nested": {"k": [1, 2]}})


def sig(d: Document, n: int = 0):
    base, _ = make_pair(d.external_id)
    return base.model_copy(update={"id": f"s-{d.external_id}-{n}", "document_id": d.id})


def iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    assert parsed.tzinfo is not None
    return parsed


# ------------------------------------------------------------------ documents, triage, extraction


def test_backend_name(store):
    assert store.backend in ("sqlite", "postgres")


def test_documents_round_trip_and_work_queues(store):
    a, b, undated = doc("a", days_ago=3), doc("b", days_ago=1), doc("c", days_ago=None)
    job, price = doc("j", "job_post"), doc("p", "pricing_page", days_ago=2)
    dup = doc("dup", text=a.text, duplicate_of=a.id)
    for d, blob in [(a, b"\x01" * 16), (b, None), (undated, None), (job, None), (price, None), (dup, b"\x02" * 16)]:
        assert store.add_document(d, blob)
    assert not store.add_document(a)  # same id: ignored, like an IntegrityError in SQLite

    assert store.has_document(a.id) and not store.has_document("nope")
    assert store.get_document(a.id).model_dump() == a.model_dump()
    assert store.get_document(undated.id).published_at is None
    assert store.get_document("nope") is None
    assert store.find_by_content_hash(a.content_hash) == a.id  # the duplicate shares the hash but is skipped
    assert store.find_by_content_hash("missing") is None
    assert store.minhashes() == [(a.id, b"\x01" * 16)]
    assert store.document_urls() == {d.url: d.id for d in (a, b, undated, job, price, dup)}

    # newest first, undated last; no duplicates, job posts or pricing pages
    assert [d.id for d in store.docs_for_triage()] == [b.id, a.id, undated.id]
    assert [d.id for d in store.docs_for_triage(limit=1)] == [b.id]
    assert [d.id for d in store.docs_for_jobs()] == [job.id]
    assert [d.id for d in store.docs_for_prices()] == [price.id]

    store.save_triage(a.id, PAIN, "mock")
    store.save_triage(b.id, PAIN, "mock")
    store.save_triage(undated.id, NOT_PAIN, "mock")
    assert store.triage_results() == {a.id: True, b.id: True, undated.id: False}
    assert store.docs_for_triage() == []
    assert [d.id for d in store.docs_for_extraction()] == [b.id, a.id]
    store.save_triage(undated.id, PAIN, "mock")  # re-triage overwrites
    assert store.triage_results()[undated.id] is True


def test_signals_keep_storage_order_and_filter(store):
    a, b = doc("a", days_ago=3), doc("b", days_ago=1)
    for d in (a, b):
        store.add_document(d)
        store.save_triage(d.id, PAIN, "mock")
    sa0, sa1, sb0 = sig(a, 0), sig(a, 1), sig(b, 0)
    store.save_extraction(b.id, [sb0], 0, ["note b"], "mock")
    store.save_extraction(a.id, [sa0, sa1], 2, ["dropped: fabricated quote", "workaround: downgraded"], "mock")

    pairs = store.signals_with_docs()
    assert [(s.id, d.id) for s, d in pairs] == [(sb0.id, b.id), (sa0.id, a.id), (sa1.id, a.id)]
    assert pairs[0][0].model_dump() == sb0.model_dump()
    assert [s.id for s, _ in store.signals_with_docs([sa1.id, "nope", sb0.id, sa1.id])] == [sb0.id, sa1.id]
    assert store.signals_with_docs([]) == []
    assert sorted(store.extraction_notes()) == sorted(["note b", "dropped: fabricated quote",
                                                       "workaround: downgraded"])
    assert store.docs_for_extraction() == []

    store.save_extraction(b.id, [sb0], 0, [], "mock")  # re-extraction upserts in place
    assert [s.id for s, _ in store.signals_with_docs()] == [sb0.id, sa0.id, sa1.id]


def test_jobs_and_prices(store):
    job, page = doc("j", "job_post"), doc("p", "pricing_page")
    store.add_document(job)
    store.add_document(page)
    store.save_job(job.id, JobExtraction(role_title="AP clerk", company="Acme", pay=ev(), tasks=[]), ["n"], "mock")
    assert [(j.role_title, d.id) for j, d in store.jobs_with_docs()] == [("AP clerk", job.id)]
    assert store.docs_for_jobs() == []

    store.save_prices(page.id, None, ["no tiers found"], "mock")
    assert store.prices_with_docs() == [] and store.docs_for_prices() == []
    tiers = [PriceTier(name="Pro", price="$49", unit="per user / month", quote="Pro $49 per user / month")]
    store.save_prices(page.id, PriceExtraction(has_public_pricing=True, contact_sales_only=False, tiers=tiers), [],
                      "mock")
    [(prices, d)] = store.prices_with_docs()
    assert d.id == page.id and prices.tiers[0].price == "$49"


# ------------------------------------------------------------------ clusters and snapshots


def test_clusters_and_snapshots(store):
    c1, c2, c0 = (make_cluster("c1", ["s1", "s2"], 0.5), make_cluster("c2", ["s3"], 0.7),
                  make_cluster("c0", ["s4"], 0.5, title="Month-end close"))
    run1 = store.replace_clusters([c1, c2, c0], run_at=NOW - timedelta(days=8))
    assert run1
    assert [c.id for c in store.clusters()] == ["c2", "c0", "c1"]  # score desc, then id
    assert store.get_cluster("c1").model_dump() == c1.model_dump()
    assert store.get_cluster("nope") is None

    [run] = store.snapshot_runs()
    assert run == {"run_id": run1, "run_at": run["run_at"], "n_clusters": 3}
    assert iso(run["run_at"]) == NOW - timedelta(days=8)
    snap = store.snapshot(run1)
    assert [r["cluster_id"] for r in snap] == ["c2", "c0", "c1"]
    assert snap[1] == {"run_id": run1, "run_at": run["run_at"], "cluster_id": "c0", "score": 0.5,
                       "confidence": "Low", "title": "Month-end close", "signal_ids": ["s4"]}
    assert snap[2]["title"] == "canonical c1"  # no synthesis yet: canonical statement
    assert store.snapshot("nope") == []

    assert store.replace_clusters([c1], snapshot=False) is None
    assert len(store.snapshot_runs()) == 1 and [c.id for c in store.clusters()] == ["c1"]

    run2 = store.replace_clusters([c1, c2], run_at=NOW)
    run3 = store.replace_clusters([c1, c2], run_at=NOW - timedelta(days=1))  # recorded late, sorts by run_at
    assert [r["run_id"] for r in store.snapshot_runs()] == [run2, run3, run1]

    # save_cluster (after synthesis) refreshes the title in the latest run only and never adds a run
    c1.synthesis = Synthesis(title="Invoice re-keying", summary="s", hypotheses=["Hypothesis: x"])
    store.save_cluster(c1)
    assert len(store.snapshot_runs()) == 3
    assert {r["cluster_id"]: r["title"] for r in store.snapshot(run2)}["c1"] == "Invoice re-keying"
    assert {r["cluster_id"]: r["title"] for r in store.snapshot(run3)}["c1"] == "canonical c1"
    assert store.get_cluster("c1").synthesis.title == "Invoice re-keying"

    store.save_cluster(make_cluster("c9", ["s9"], 0.1))  # e.g. an API merge: not part of any snapshot
    assert "c9" not in {r["cluster_id"] for r in store.snapshot(run2)}
    assert store.delete_cluster("c9") and not store.delete_cluster("c9")

    empty = store.replace_clusters([])
    assert store.clusters() == [] and store.snapshot(empty) == []
    assert store.snapshot_runs()[0]["n_clusters"] == 0


# ------------------------------------------------------------------ labels, overrides, agent log


def test_labels(store):
    store.set_label("cluster", "c1", "accept", "good one")
    store.set_label("cluster", "c2", "reject")
    store.set_label("signal", "s1", "valid")
    assert store.labels("cluster") == {"c1": "accept", "c2": "reject"}
    got = store.get_label("cluster", "c1")
    assert (got["label"], got["note"]) == ("accept", "good one") and iso(got["created_at"])
    assert store.get_label("cluster", "nope") is None
    rows = store.all_labels("cluster")
    assert [r["target_id"] for r in rows] == ["c1", "c2"]
    assert set(rows[0]) == {"target_id", "label", "note", "created_at"} and rows[1]["note"] == ""

    store.set_label("cluster", "c1", "reject")  # overwrite clears the note
    assert store.get_label("cluster", "c1")["label"] == "reject"
    assert store.get_label("cluster", "c1")["note"] == ""

    c2_created = store.get_label("cluster", "c2")["created_at"]
    assert store.move_label("cluster", "c2", "c3")
    assert store.labels("cluster") == {"c1": "reject", "c3": "reject"}
    assert store.get_label("cluster", "c3")["created_at"] == c2_created
    assert not store.move_label("cluster", "c3", "c1")  # target already labelled: untouched
    assert not store.move_label("cluster", "nope", "c4")
    assert store.labels("cluster") == {"c1": "reject", "c3": "reject"}

    assert store.delete_label("cluster", "c1") and not store.delete_label("cluster", "c1")
    assert store.labels("signal") == {"s1": "valid"}


def test_overrides(store):
    first = store.add_override("merge", {"signal_ids": ["s1", "s3"]})
    second = store.add_override("split", {"signal_ids": ["s2"], "note": "different job"})
    assert isinstance(first, int) and second > first
    rows = store.overrides()
    assert [(r["id"], r["kind"]) for r in rows] == [(first, "merge"), (second, "split")]
    assert rows[1]["payload"] == {"signal_ids": ["s2"], "note": "different job"} and iso(rows[1]["created_at"])
    with pytest.raises(ValueError):
        store.add_override("rename", {})
    assert store.delete_override(first) and not store.delete_override(first)
    assert [r["id"] for r in store.overrides()] == [second]


def test_agent_query_log(store):
    store.log_agent_query("run-a", "c1", "hn", "invoice ocr errors", 12, 3, "corroborate")
    store.log_agent_query("run-a", None, "github", "ap automation", 0, 0)
    store.log_agent_query("run-b", "c2", "hn", "month end close", 1, 1)
    rows = store.agent_queries()
    assert [r["query"] for r in rows] == ["invoice ocr errors", "ap automation", "month end close"]
    assert set(rows[0]) == {"id", "run_id", "cluster_id", "source", "query", "n_results", "n_new", "note",
                            "created_at"}
    run_a = store.agent_queries("run-a")
    assert [(r["cluster_id"], r["n_results"], r["n_new"], r["note"]) for r in run_a] == [
        ("c1", 12, 3, "corroborate"), (None, 0, 0, "")]
    assert store.agent_queries("nope") == []


def test_embeddings(store):
    assert store.save_embeddings("tfidf-test", {"s1": [0.1, 0.2, 0.3], "s2": np.ones(3)}) == 2
    store.save_embeddings("tfidf-test", {"s1": [1, 0, 0]})  # replaces
    store.save_embeddings("other", {"s1": [9, 9]})
    got = store.embeddings("tfidf-test")
    assert set(got) == {"s1", "s2"} and got["s1"].dtype == np.float32
    assert np.allclose(got["s1"], [1, 0, 0]) and np.allclose(got["s2"], [1, 1, 1])
    assert set(store.embeddings("tfidf-test", ["s2", "nope"])) == {"s2"}
    assert store.embeddings("tfidf-test", []) == {}
    assert np.allclose(store.embeddings("other")["s1"], [9, 9])


# ------------------------------------------------------------------ planner arms and stats


def test_arms_and_source_stats(store):
    store.upsert_arms([arm("arm-1"), arm("arm-2", source="github")])
    store.upsert_arms([arm("arm-1", query="changed")])  # existing arms are kept as they are
    store.record_arm_run("arm-1", 0)
    store.record_arm_run("arm-1", 2)
    a, b = doc("a", arm_id="arm-1", source="hn"), doc("b", arm_id="arm-1", source="hn")
    dup = doc("dup", text=a.text, duplicate_of=a.id, arm_id="arm-1", source="hn")
    other = doc("o", source="github")
    for d in (a, b, dup, other):
        store.add_document(d)
    store.save_triage(a.id, PAIN, "mock")
    store.save_triage(b.id, NOT_PAIN, "mock")
    store.save_extraction(a.id, [sig(a, 0), sig(a, 1)], 1, [], "mock")

    stats = store.arm_stats()
    assert list(stats) == ["arm-1", "arm-2"]
    assert stats["arm-1"] == {"arm_id": "arm-1", "source": "hn", "query": "invoice re-keying", "runs": 2,
                              "empty_runs": 1, "docs": 2, "verified": 1}
    assert list(store.arm_stats("github")) == ["arm-2"]

    assert store.source_stats() == [
        {"source": "hn", "docs": 3, "duplicates": 1, "triaged": 2, "pain": 1, "signals": 2, "dropped": 1},
        {"source": "github", "docs": 1, "duplicates": 0, "triaged": 0, "pain": 0, "signals": 0, "dropped": 0},
    ]


def test_reset_wipes_rows(store):
    a = doc("a")
    store.add_document(a, b"\x01" * 8)
    store.save_triage(a.id, PAIN, "mock")
    store.save_extraction(a.id, [sig(a)], 0, ["n"], "mock")
    store.replace_clusters([make_cluster("c1", [sig(a).id], 0.4)])
    store.set_label("cluster", "c1", "accept")
    store.add_override("merge", {"signal_ids": []})
    store.log_agent_query("r", "c1", "hn", "q", 1, 1)
    store.upsert_arms([arm("arm-1")])
    store.save_embeddings("m", {"s": [1.0]})
    store.reset()
    assert not store.has_document(a.id) and store.signals_with_docs() == [] and store.triage_results() == {}
    assert store.clusters() == [] and store.snapshot_runs() == [] and store.labels("cluster") == {}
    assert store.overrides() == [] and store.agent_queries() == [] and store.arm_stats() == {}
    assert store.source_stats() == [] and store.embeddings("m") == {} and store.extraction_notes() == []
    assert store.add_override("merge", {}) == 1  # ids restart


# ------------------------------------------------------------------ concurrency


def test_store_is_safe_across_threads(store):
    """FastAPI runs sync endpoints in a threadpool against one shared store."""
    a = doc("a")
    store.add_document(a)
    store.save_extraction(a.id, [sig(a)], 0, [], "mock")

    def work(i: int) -> bool:
        store.set_label("cluster", f"c{i}", "accept", f"note {i}")
        store.add_override("merge", {"i": i})
        store.log_agent_query("threads", None, "hn", f"q{i}", i, 0)
        store.replace_clusters([make_cluster(f"k{i}", [f"s{i}"], 0.1)], snapshot=i % 4 == 0)
        assert len(store.signals_with_docs()) == 1
        store.clusters(), store.overrides(), store.all_labels("cluster"), store.snapshot_runs()
        return True

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert all(pool.map(work, range(64)))
    assert len(store.labels("cluster")) == 64
    assert len(store.overrides()) == 64 and len(store.agent_queries("threads")) == 64
    assert len(store.clusters()) == 1 and len(store.snapshot_runs()) == 16


# ------------------------------------------------------------------ the demo, end to end


def test_demo_pipeline_end_to_end(store, settings):
    p = Pipeline(settings, store=store, log=lambda _: None, sleep=lambda _: None)
    new, _ = p.load_fixture(FIXTURES / "demo_docs.jsonl")
    p.triage()
    p.extract()
    p.jobs()
    p.prices()
    n = p.cluster(threshold=0.75)
    p.synthesize()
    md, html = p.report(demo=True, stem="demo-pain-report")
    assert new > 0 and n > 0 and md.exists() and html.exists()

    for signal, document in store.signals_with_docs():
        assert quote_in_text(signal.problem.quote, normalize(document.reference_text))
    [run] = store.snapshot_runs()
    assert run["n_clusters"] == n
    titles = {r["cluster_id"]: r["title"] for r in store.snapshot(run["run_id"])}
    for c in store.clusters():
        if c.synthesis:
            assert titles[c.id] == c.synthesis.title

    p.cluster(threshold=0.75)  # same evidence again: a second snapshot, every cluster steady
    trends = compute_trends(store, settings.load_yaml("scoring.yaml"))
    assert trends["previous_run_at"] and trends["items"] == []
    assert set(trends["status_by_cluster"].values()) == {"steady"}


def test_replace_clusters_is_atomic(store):
    c1 = make_cluster("c1", ["s1"], 0.5)
    run1 = store.replace_clusters([c1])
    with pytest.raises(Exception):
        store.replace_clusters([make_cluster("c2", ["s2"], 0.4), make_cluster("c2", ["s2"], 0.4)])  # duplicate id
    assert [c.id for c in store.clusters()] == ["c1"]  # rolled back: old clusters and snapshots intact
    assert [r["run_id"] for r in store.snapshot_runs()] == [run1]
    store.set_label("cluster", "c1", "accept")  # the connection is still usable
    assert store.labels("cluster") == {"c1": "accept"}


def test_snapshot_titles_match_card_titles(store):
    """Trend rows link to cards, so a snapshot title must equal the card/API title (report._clean(canonical, 90))."""
    from paindisc.report import _clean
    from paindisc.store import cluster_title
    canonical = ("i run ap for a property  management company\nand we re-key vendor invoices from pdfs into our "
                 "accounting system every day.")
    c = make_cluster("c1", ["s1"], 0.5).model_copy(update={"canonical": canonical})
    run = store.replace_clusters([c])
    title = store.snapshot(run)[0]["title"]
    assert title == cluster_title(c) == _clean(canonical, 90)
    assert len(title) == 90 and title.endswith("…") and "  " not in title and "\n" not in title
    c.synthesis = Synthesis(title="Invoice re-keying", summary="s", hypotheses=[])
    store.save_cluster(c)
    assert store.snapshot(run)[0]["title"] == "Invoice re-keying" == cluster_title(c)
