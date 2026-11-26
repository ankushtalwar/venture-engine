from conftest import ev

from paindisc.models import ExtractedSignal, SpendEvidence, Status
from paindisc.verify import normalize, quote_in_text, verify_signal

DOC = ("We run a 12-person bookkeeping firm. Every Friday we re-key vendor invoices from PDFs into QuickBooks. "
       "We pay a VA $15/hr to help. Errors cost us a $400 late fee last quarter. It’s a nightmare.")


def make(**overrides) -> ExtractedSignal:
    base = dict(
        problem=ev("re-keying invoices", "observed", "we re-key vendor invoices from PDFs into QuickBooks"),
        segment=ev("bookkeeping firm", "observed", "12-person bookkeeping firm"),
        affected_role=ev(), likely_buyer=ev("owner", "inferred", basis="small firm"), workaround=ev(),
        occurrence_freq=ev("weekly", "observed", "Every Friday"), financial_impact=ev(), spend_evidence=[],
        existing_tool_complaint=ev(), automatability=ev("high", "inferred", basis="structured inputs"),
        specificity="A", author_type="operator", intensity=2, canonical="firms must re-key invoices",
    )
    base.update(overrides)
    return ExtractedSignal(**base)


def test_valid_signal_passes_untouched():
    signal, notes = verify_signal(make(), DOC)
    assert signal is not None and notes == []
    assert signal.segment.status == Status.observed


def test_fabricated_problem_quote_drops_signal():
    signal, notes = verify_signal(make(problem=ev("payroll", "observed", "we spend 40 hours a week on payroll")), DOC)
    assert signal is None and "dropped" in notes[0]


def test_inferred_problem_drops_signal():
    signal, _ = verify_signal(make(problem=ev("payroll pain", "inferred", basis="guess")), DOC)
    assert signal is None


def test_observed_field_with_missing_quote_becomes_unknown():
    signal, notes = verify_signal(make(segment=ev("law firm", "observed", "a 50-person law firm")), DOC)
    assert signal.segment.status == Status.unknown
    assert any(n.startswith("segment") for n in notes)


def test_inferred_financial_figure_is_rejected():
    signal, _ = verify_signal(make(financial_impact=ev("$5,000/month", "inferred", basis="typical firm")), DOC)
    assert signal.financial_impact.status == Status.unknown


def test_financial_figure_must_appear_in_its_quote():
    bad = ev("$4,000", "observed", "Errors cost us a $400 late fee last quarter")
    good = ev("$400", "observed", "Errors cost us a $400 late fee last quarter")
    assert verify_signal(make(financial_impact=bad), DOC)[0].financial_impact.status == Status.unknown
    assert verify_signal(make(financial_impact=good), DOC)[0].financial_impact.status == Status.observed


def test_derived_financial_needs_basis():
    no_basis = ev("$3,120/year", "derived", "We pay a VA $15/hr to help")
    with_basis = ev("$3,120/year", "derived", "We pay a VA $15/hr to help", "4 h/week x $15/hr x 52 weeks")
    assert verify_signal(make(financial_impact=no_basis), DOC)[0].financial_impact.status == Status.unknown
    assert verify_signal(make(financial_impact=with_basis), DOC)[0].financial_impact.status == Status.derived


def test_spend_evidence_quote_and_amount_checked():
    spends = [
        SpendEvidence(type="freelancer", amount="$25/hr", quote="We pay a VA $15/hr to help"),  # wrong amount
        SpendEvidence(type="software", amount="$99", quote="we pay $99 a month for software"),  # not in doc
    ]
    signal, notes = verify_signal(make(spend_evidence=spends), DOC)
    assert len(signal.spend_evidence) == 1
    assert signal.spend_evidence[0].amount == ""
    assert len(notes) == 2


def test_automatability_is_never_observed():
    signal, _ = verify_signal(make(automatability=ev("high", "observed", "Every Friday")), DOC)
    assert signal.automatability.status == Status.inferred


def test_quote_matching_tolerates_typography_but_not_short_paraphrase():
    text = normalize(DOC)
    assert quote_in_text("It's a nightmare", text)  # straight vs curly apostrophe
    assert quote_in_text("every friday  we re-key vendor invoices", text)  # case and spacing
    assert not quote_in_text("a nightmares", text)  # short quotes must match exactly
    assert quote_in_text("we re-key vendor invoice from PDFs into QuickBooks", text)  # small typo in a long quote
