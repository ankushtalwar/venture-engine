"""Quote verification. Code, not the model, decides what counts as evidence.

Rules (SPEC section 3):
- problem must be observed with a quote found in the document, or the signal is dropped
- any observed field whose quote isn't found becomes unknown
- financial_impact is observed or derived, never inferred; numbers must appear in the quote
- spend evidence needs a found quote; an amount whose numbers aren't in the quote is cleared
- automatability is always inferred
"""

from __future__ import annotations

import re
import unicodedata

from rapidfuzz import fuzz

from .models import Evidence, ExtractedSignal, JobExtraction, PriceExtraction, Status

_PUNCT = str.maketrans({
    "‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-", "…": "...",
    " ": " ",
})
_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")

FUZZY_THRESHOLD = 95.0
MIN_FUZZY_LEN = 20  # shorter quotes must match exactly


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "").translate(_PUNCT).lower()
    return " ".join(text.split())


def quote_in_text(quote: str, normalized_text: str) -> bool:
    q = normalize(quote).strip(" .,;:\"'")
    if len(q) < 3:
        return False
    if q in normalized_text:
        return True
    if len(q) < MIN_FUZZY_LEN:
        return False
    return fuzz.partial_ratio(q, normalized_text) >= FUZZY_THRESHOLD


def numbers(text: str) -> set[str]:
    return {n.replace(",", "").rstrip(".") for n in _NUM.findall(text or "")}


def numbers_supported(value: str, quote: str) -> bool:
    return numbers(value) <= numbers(quote)


TEXT_FIELDS = ("segment", "affected_role", "likely_buyer", "workaround", "occurrence_freq",
               "existing_tool_complaint", "automatability")


def verify_signal(signal: ExtractedSignal, reference_text: str) -> tuple[ExtractedSignal | None, list[str]]:
    text = normalize(reference_text)
    s = signal.model_copy(deep=True)
    notes: list[str] = []

    if s.problem.status != Status.observed or not quote_in_text(s.problem.quote, text):
        return None, ["dropped: problem quote not found in document"]

    for name in TEXT_FIELDS:
        ev: Evidence = getattr(s, name)
        if ev.status == Status.observed and not quote_in_text(ev.quote, text):
            notes.append(f"{name}: quote not found -> unknown")
            setattr(s, name, Evidence.unknown())
        elif ev.status == Status.derived:
            notes.append(f"{name}: derived only allowed for financial_impact -> unknown")
            setattr(s, name, Evidence.unknown())

    if s.automatability.status == Status.observed:
        s.automatability.status = Status.inferred

    fi = s.financial_impact
    if fi.status == Status.inferred:
        notes.append("financial_impact: inferred figures not allowed -> unknown")
        s.financial_impact = Evidence.unknown()
    elif fi.status == Status.observed:
        if not quote_in_text(fi.quote, text):
            notes.append("financial_impact: quote not found -> unknown")
            s.financial_impact = Evidence.unknown()
        elif not numbers(fi.value) or not numbers_supported(fi.value, fi.quote):
            notes.append("financial_impact: figure not in quote -> unknown")
            s.financial_impact = Evidence.unknown()
    elif fi.status == Status.derived:
        if not fi.basis.strip() or not quote_in_text(fi.quote, text):
            notes.append("financial_impact: derived without basis or stated quantity -> unknown")
            s.financial_impact = Evidence.unknown()

    kept = []
    for spend in s.spend_evidence:
        if not quote_in_text(spend.quote, text):
            notes.append(f"spend_evidence ({spend.type}): quote not found -> removed")
            continue
        if spend.amount and not numbers_supported(spend.amount, spend.quote):
            notes.append(f"spend_evidence ({spend.type}): amount not in quote -> cleared")
            spend.amount = ""
        kept.append(spend)
    s.spend_evidence = kept
    return s, notes


def verify_job(job: JobExtraction, reference_text: str) -> tuple[JobExtraction, list[str]]:
    text = normalize(reference_text)
    j = job.model_copy(deep=True)
    notes: list[str] = []
    if j.pay.status != Status.unknown:
        if j.pay.status != Status.observed or not quote_in_text(j.pay.quote, text) \
                or not numbers(j.pay.value) or not numbers_supported(j.pay.value, j.pay.quote):
            notes.append("pay: not verified -> unknown")
            j.pay = Evidence.unknown()
    kept = []
    for task in j.tasks:
        if not quote_in_text(task.quote, text):
            notes.append(f"task dropped: quote not found ({task.task[:40]})")
            continue
        if task.frequency.status == Status.observed and not quote_in_text(task.frequency.quote, text):
            task.frequency = Evidence.unknown()
        elif task.frequency.status not in (Status.observed, Status.unknown):
            task.frequency = Evidence.unknown()
        kept.append(task)
    j.tasks = kept
    return j, notes


def verify_prices(prices: PriceExtraction, reference_text: str) -> tuple[PriceExtraction, list[str]]:
    text = normalize(reference_text)
    p = prices.model_copy(deep=True)
    notes: list[str] = []
    kept = []
    for tier in p.tiers:
        if not quote_in_text(tier.quote, text) or not numbers_supported(tier.price, tier.quote):
            notes.append(f"tier dropped: {tier.name} {tier.price}")
            continue
        kept.append(tier)
    p.tiers = kept
    return p, notes
