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
