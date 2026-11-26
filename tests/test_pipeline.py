from conftest import FIXTURES, ev, make_pipeline

from paindisc import mock
from paindisc.evaluate import evaluate
from paindisc.models import Document, ExtractedSignal, ExtractionResult, Status
from paindisc.verify import normalize, numbers_supported, quote_in_text


def run_demo(settings):
    p = make_pipeline(settings)
    new, dupes = p.load_fixture(FIXTURES / "demo_docs.jsonl")
    p.triage()
    p.extract()
    p.jobs()
    p.prices()
    p.cluster(threshold=0.75)
    p.synthesize()
    return p, new, dupes


def test_demo_pipeline_end_to_end(settings):
    p, new, dupes = run_demo(settings)
    assert dupes == 1  # the cross-post is caught by near-duplicate detection

    md, html = p.report(demo=True, stem="t")
    text = md.read_text()
    assert "DEMO" in text and "Pricing history" in text and "Repetitive tasks in job posts" in text
    assert html.read_text().startswith("<!DOCTYPE html>")

    triage = p.store.triage_results()
    assert triage[Document.make_id("demo-forum", "n1")] is False  # vendor promo
    assert triage[Document.make_id("demo-forum", "n2")] is False  # job seeker

    top = p.store.clusters()[0]
    assert top.n_signals >= 2 and top.n_authors >= 2


def test_every_stored_quote_is_in_its_document(settings):
    p, _, _ = run_demo(settings)
    pairs = p.store.signals_with_docs()
    assert pairs
    for signal, doc in pairs:
        ref = normalize(doc.reference_text)
        assert quote_in_text(signal.problem.quote, ref)
        for spend in signal.spend_evidence:
            assert quote_in_text(spend.quote, ref)
        if signal.financial_impact.status == Status.observed:
            assert numbers_supported(signal.financial_impact.value, signal.financial_impact.quote)


def test_gold_set_evaluation(settings):
    p, _, _ = run_demo(settings)
    result = evaluate(p.store, FIXTURES / "gold.jsonl")
    assert result["financial_fabrications"] == []
    assert result["triage"]["precision"] == 1.0


def test_job_tasks_aggregate_across_companies(settings):
    p, _, _ = run_demo(settings)
    tasks = p.job_tasks()
    assert any(t["companies"] == 2 for t in tasks)  # statement reconciliation appears at both companies
    assert any(t["pay_quotes"] for t in tasks)


def test_fabricated_extraction_never_reaches_the_store(settings, monkeypatch):
    fabricated = ExtractedSignal(
        problem=ev("payroll", "observed", "we spend 40 hours a week fixing payroll errors"),
        segment=ev(), affected_role=ev(), likely_buyer=ev(), workaround=ev(), occurrence_freq=ev(),
        financial_impact=ev("$10,000 a month", "inferred", basis="sounds expensive"), spend_evidence=[],
        existing_tool_complaint=ev(), automatability=ev(), specificity="C", author_type="unknown", intensity=3,
        canonical="made up",
    )
    monkeypatch.setattr(mock, "extract", lambda doc: ExtractionResult(signals=[fabricated]))
    p = make_pipeline(settings)
    p.load_fixture(FIXTURES / "demo_docs.jsonl")
    p.triage()
    stats = p.extract()
    assert stats["signals"] == 0 and stats["dropped"] > 0
    assert p.store.signals_with_docs() == []


def test_near_dup_index_reloads_stored_minhashes(settings):
    """A non-empty store must reload into a fresh index (datasketch >= 2.0 needs the scheme) and catch near-dupes."""
    from datetime import datetime, timezone

    from paindisc.dedupe import NearDupIndex, ingest

    p, new, _ = run_demo(settings)
    assert new > 0 and p.store.minhashes()
    index = NearDupIndex()
    index.load(p.store)  # raised ValueError under datasketch 2.0 before the scheme was pinned
    original = next(d for d in (p.store.get_document(i) for i, _ in p.store.minhashes()) if len(d.text.split()) > 30)
    now = datetime.now(timezone.utc)
    near = Document(id="near-dup", source="test", source_type="forum", route="fixture", external_id="near-dup",
                    url="https://example.com/near", text=original.text + " Thanks.", published_at=now,
                    captured_at=now)
    added, dupes = ingest(p.store, index, [near])
    assert (added, dupes) == (0, 1)
    assert p.store.get_document("near-dup").duplicate_of == original.id


def test_cluster_labels_follow_reformed_clusters(settings):
    """A reviewer's accept survives a re-cluster that changes the cluster id (one signal removed)."""
    p, _, _ = run_demo(settings)
    target = next(c for c in p.store.clusters() if c.n_signals >= 3)
    p.store.set_label("cluster", target.id, "accept", "keep")
    p.store.add_override("split", {"signal_ids": [target.signal_ids[0]]})  # membership changes -> new id
    p.cluster(threshold=0.75)
    successor = [c for c in p.store.clusters() if set(c.signal_ids) == set(target.signal_ids[1:])]
    assert successor and successor[0].id != target.id
    assert p.store.labels("cluster") == {successor[0].id: "accept"}


def test_labels_go_to_the_true_successor_not_the_nearest_labelled_one(settings):
    """X continues unlabelled B (Jaccard .625); it only grazes labelled A (.3). A's label must not jump to X."""
    from paindisc.models import Cluster

    def c(cid, ids):
        return Cluster(id=cid, canonical=cid, signal_ids=[f"s{i}" for i in ids], n_signals=len(ids), n_authors=1,
                       source_types=["forum"])

    p = make_pipeline(settings)
    p.store.set_label("cluster", "A", "accept")
    previous = {"A": c("A", range(1, 6)), "B": c("B", range(6, 11))}
    new = [c("X", [1, 2, 3, 6, 7, 8, 9, 10]), c("Z", [4, 5, 11, 12, 13, 14])]
    assert p._carry_labels(previous, new, threshold=0.3) == 0
    assert p.store.labels("cluster") == {"A": "accept"}  # kept as history, not misattributed


def test_report_links_are_http_only_and_source_text_is_inert():
    """Report files are opened outside the app (disk, Slack digest): no javascript:/data: links, no Markdown links
    or HTML smuggled in through quotes."""
    from paindisc.report import _href, _md, _md_ref, safe_url

    for bad in ("javascript:window.x=1", " JavaScript:alert(1)", "data:text/html,<b>x</b>", "/relative", "", None):
        assert safe_url(bad) is None
        assert "href" not in _href({"url": bad, "label": "src · 2026-10-05"})
        assert "](" not in _md_ref({"url": bad, "label": "src · 2026-10-05"})
    ok = "https://news.ycombinator.com/item?id=1"
    assert _href({"url": ok, "label": "hn"}) == f'<a href="{ok.replace("&", "&amp;")}" rel="noopener noreferrer">hn</a>'
    assert _md_ref({"url": "https://ex.com/a b)(<x>|", "label": "ex"}) == "[ex](https://ex.com/a%20b%29%28%3Cx%3E%7C)"
    quote = "[click me](javascript:alert(1)) <img src=x onerror=alert(2)> | col"
    escaped = _md(quote)
    import re
    assert re.search(r"(?<!\\)[\[\]<>|]", escaped) is None  # every Markdown/HTML special is backslash-escaped
    assert escaped.replace("\\", "") == quote  # renders back to the verbatim quote
