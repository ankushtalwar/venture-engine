"""Deterministic stand-ins for the LLM steps (LLM_MOCK=true, or no ANTHROPIC_API_KEY).

They exist so the whole pipeline runs and tests offline. They are crude on purpose: real runs use Claude.
"""

from __future__ import annotations

import re

from .models import (
    Document, Evidence, ExtractedSignal, ExtractionResult, JobExtraction, JobTask, PriceExtraction, PriceTier,
    SkepticPoint, SkepticReview, SpendEvidence, Status, Synthesis, TriageResult,
)

TRIGGERS = re.compile(
    r"(i wish there (was|were)|is there (a|an) (tool|app|software)|does anyone know (a|of) (tool|app|software)"
    r"|still do (this|it) manually|\bmanually\b|by hand|spreadsheets?|\bexcel\b|google sheets"
    r"|spend(ing)? (\w+ )?hours|takes (us )?hours|alternative to|too expensive|we hired|hired (a|an) "
    r"|there has to be a better way|copy(ing)? and past(e|ing)|re-?key|nightmare|costing us|lost \$)",
    re.I,
)
NEGATIVE = re.compile(r"(my homework|looking for a job|i'?m a student|check out our|we just launched|our new app)", re.I)
FREQ = re.compile(
    r"\b(every (day|week|month|quarter|year|monday|tuesday|wednesday|thursday|friday)|daily|weekly|monthly"
    r"|month-end|end of (the )?month|quarterly|annually|each (week|month))\b",
    re.I,
)
MONEY = re.compile(r"\$\s?\d[\d,]*(?:\.\d+)?\s?[kK]?(?:\s?(?:/|per|an?)\s?(?:hr|hour|month|mo|year|yr|week))?")
WORKAROUND = re.compile(
    r"(spreadsheets?|\bexcel\b|google sheets|manually|by hand|copy(?:ing)? and past(?:e|ing)"
    r"|virtual assistant|\bVA\b|freelancers?|\btemp\b)",
    re.I,
)
TOOL_COMPLAINT = re.compile(r"(too expensive|doesn'?t (integrate|support|have)|alternative to|switch(ing)? from"
                            r"|price (increase|hike))", re.I)
LOSS = re.compile(r"(lost|losing|costing|cost us|wasted|penalt|fine[ds]?\b|late fees?|chargebacks?)", re.I)
_SENTENCES = re.compile(r"(?<=[.!?])\s+|\n+")

TOOLS = ["Excel", "Google Sheets", "QuickBooks", "Xero", "NetSuite", "Salesforce", "HubSpot", "SAP", "Sage"]
INPUTS = ["PDF", "email", "portal", "phone", "fax", "paper"]
RULES = re.compile(r"\b(reconcile|enter|update|process|file|verify|schedule|invoice|post|match|track|prepare)\b", re.I)
PAY = re.compile(r"(\$\s?\d[\d,]*(?:\.\d+)?\s?[kK]?\s?(?:-|to|–)\s?\$?\s?\d[\d,]*(?:\.\d+)?\s?[kK]?"
                 r"|\[structured field\] salaryRange:[^\n]+)")
PRICE = re.compile(r"\$\s?\d[\d,]*(?:\.\d+)?(?=\s*(?:/|per)\s*(?:user|seat|month|mo|year|yr))")


def _sentence(pattern: re.Pattern, text: str) -> str | None:
    for sentence in _SENTENCES.split(text):
        if pattern.search(sentence):
            return sentence.strip()[:300]
    return None


def _observed(value: str, quote: str) -> Evidence:
    return Evidence(value=value, status=Status.observed, quote=quote, basis="")


def _inferred(value: str) -> Evidence:
    return Evidence(value=value, status=Status.inferred, quote="", basis="mock heuristic")


def triage(doc: Document) -> TriageResult:
    text = doc.reference_text
    if NEGATIVE.search(text):
        return TriageResult(is_pain=False, kind="other", reason="mock: negative pattern")
    if TRIGGERS.search(text):
        return TriageResult(is_pain=True, kind="business_pain", reason="mock: trigger phrase")
    return TriageResult(is_pain=False, kind="other", reason="mock: no trigger phrase")


def extract(doc: Document) -> ExtractionResult:
    text = doc.text
    problem = _sentence(TRIGGERS, text)
    if not problem:
        return ExtractionResult(signals=[])

    freq_sentence = _sentence(FREQ, text)
    freq_match = FREQ.search(freq_sentence) if freq_sentence else None
    occurrence = _observed(freq_match.group(0).lower(), freq_match.group(0)) if freq_match else Evidence.unknown()

    spend: list[SpendEvidence] = []
    financial = Evidence.unknown()
    money_sentence = _sentence(MONEY, text)
    if money_sentence:
        amount = MONEY.search(money_sentence).group(0).strip()
        lowered = money_sentence.lower()
        if re.search(r"freelanc|upwork|\bva\b|virtual assistant|contractor", lowered):
            kind = "freelancer"
        elif re.search(r"\bhire|hired|salary|full[- ]time|employee", lowered):
            kind = "staff"
        elif LOSS.search(lowered):
            kind = "penalty"
        elif re.search(r"subscription|per user|seat|plan|licen[cs]e", lowered):
            kind = "software"
        else:
            kind = "other"
        spend.append(SpendEvidence(type=kind, amount=amount, quote=money_sentence))
        if LOSS.search(lowered):
            financial = _observed(amount, money_sentence)

    work_sentence = _sentence(WORKAROUND, text)
    workaround = (_observed(WORKAROUND.search(work_sentence).group(0).lower(), work_sentence)
                  if work_sentence else Evidence.unknown())
    tool_sentence = _sentence(TOOL_COMPLAINT, text)
    tool = _observed("complaint about an existing tool", tool_sentence) if tool_sentence else Evidence.unknown()

    signal = ExtractedSignal(
        problem=_observed(problem[:200], problem),
        segment=_inferred(doc.metrics.get("segment_hint", "unspecified business")),
        affected_role=_inferred("author"),
        likely_buyer=_inferred("owner or operations lead"),
        workaround=workaround,
        occurrence_freq=occurrence,
        financial_impact=financial,
        spend_evidence=spend,
        existing_tool_complaint=tool,
        automatability=_inferred("medium"),
        specificity="A" if (freq_match or money_sentence) else "B",
        author_type="unknown",
        intensity=3 if financial.status == Status.observed else 2,
        canonical=" ".join(problem.lower().split())[:200],
    )
    return ExtractionResult(signals=[signal])


def extract_job(doc: Document) -> JobExtraction:
    tasks = []
    for line in doc.text.splitlines():
        line = line.strip()
        if not line.startswith("- "):
            continue
        item = line[2:].strip()
        if len(item) < 15:
            continue
        freq = FREQ.search(item)
        tasks.append(JobTask(
            task=item[:120],
            quote=item,
            frequency=_observed(freq.group(0).lower(), freq.group(0)) if freq else Evidence.unknown(),
            tools=[t for t in TOOLS if t.lower() in item.lower()],
            inputs=[i for i in INPUTS if i.lower() in item.lower()],
            rules_based=bool(RULES.search(item)),
        ))
    pay_match = PAY.search(doc.text)
    pay = _observed(pay_match.group(0).strip(), pay_match.group(0).strip()) if pay_match else Evidence.unknown()
    return JobExtraction(role_title=doc.title, company=str(doc.metrics.get("company", "")), pay=pay, tasks=tasks)


def extract_prices(doc: Document) -> PriceExtraction:
    tiers = []
    for line in doc.text.splitlines():
        match = PRICE.search(line)
        if match:
            name = line.split(match.group(0))[0].strip(" :-|") or "plan"
            tiers.append(PriceTier(name=name[:40], price=match.group(0), unit=line[match.end():].strip()[:40],
                                   quote=line.strip()[:200]))
    contact = bool(re.search(r"contact (sales|us)", doc.text, re.I))
    return PriceExtraction(has_public_pricing=bool(tiers), contact_sales_only=contact and not tiers, tiers=tiers)


def synthesize(canonical: str, n_signals: int, n_authors: int, source_types: list[str]) -> Synthesis:
    title = canonical if len(canonical) <= 70 else canonical[:70].rsplit(" ", 1)[0] + "…"
    return Synthesis(
        title=title[:1].upper() + title[1:],
        summary=(f"{n_signals} verified signals from {n_authors} distinct authors across "
                 f"{', '.join(source_types)}. (Mock summary: run with ANTHROPIC_API_KEY for a real one.)"),
        hypotheses=["Hypothesis: the people describing this are the ones who would pay to fix it."],
    )


def skeptic(n_authors: int, source_types: list[str], signal_ids: list[str]) -> SkepticReview:
    points = []
    if len(source_types) == 1:
        points.append(SkepticPoint(claim=f"All evidence comes from one source type ({source_types[0]}).",
                                   kind="evidence", signal_ids=signal_ids[:3]))
    if n_authors < 3:
        points.append(SkepticPoint(claim="Fewer than three distinct authors: could be one loud voice.",
                                   kind="evidence", signal_ids=signal_ids[:3]))
    points.append(SkepticPoint(claim="Existing tools may already solve this well for most firms.",
                               kind="hypothesis", signal_ids=[]))
    return SkepticReview(points=points)
