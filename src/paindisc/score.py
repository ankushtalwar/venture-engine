"""Cluster scoring: visible components, computed confidence."""

from __future__ import annotations

import math
import re
from datetime import datetime, timedelta, timezone

from .models import Document, PainSignal, Status

DEFAULT_WEIGHTS = {
    "breadth": 0.30,
    "spend": 0.20,
    "frequency": 0.15,
    "underserved": 0.15,
    "intensity": 0.10,
    "recency": 0.10,
    "automatability": 0.0,  # always inferred: shown, not scored, unless you change the weight
}

FREQ_WEIGHTS = {"daily": 1.0, "weekly": 0.8, "monthly": 0.6, "quarterly": 0.4, "per-event": 0.4, "annual": 0.2}
_FREQ_PATTERNS = [
    ("daily", r"daily|every day|each day|every morning|per day"),
    ("weekly", r"weekly|every week|each week|every (mon|tues|wednes|thurs|fri|satur|sun)day|per week"),
    ("monthly", r"monthly|every month|each month|month[- ]end|end of (the )?month|per month"),
    ("quarterly", r"quarterly|every quarter|each quarter"),
    ("annual", r"annual|yearly|every year|tax season|per year"),
]


def freq_bucket(value: str) -> str | None:
    text = (value or "").lower()
    if not text:
        return None
    for bucket, pattern in _FREQ_PATTERNS:
        if re.search(pattern, text):
            return bucket
    return "per-event"


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def score_members(members: list[tuple[PainSignal, Document]], weights: dict | None = None,
                  thresholds: dict | None = None, now: datetime | None = None) -> dict:
    weights = {**DEFAULT_WEIGHTS, **(weights or {})}
    th = {"high_authors": 5, "high_source_types": 2, "high_max_age_days": 365, "medium_authors": 3,
          **(thresholds or {})}
    now = now or datetime.now(timezone.utc)

    authors = {doc.author_key for _, doc in members}
    source_types = sorted({doc.source_type for _, doc in members})
    dates = [d for d in (_aware(doc.published_at) for _, doc in members) if d]
    spend_count = sum(1 for s, _ in members if s.spend_evidence or s.financial_impact.status in
                      (Status.observed, Status.derived))
    freq_scores = [FREQ_WEIGHTS.get(freq_bucket(s.occurrence_freq.value) or "", 0.0)
                   for s, _ in members if s.occurrence_freq.status == Status.observed]
    complaints = sum(1 for s, _ in members if s.existing_tool_complaint.status == Status.observed)
    recent = [d for d in dates if now - d <= timedelta(days=365)]
    last90 = sum(1 for d in dates if now - d <= timedelta(days=90))
    prev90 = sum(1 for d in dates if timedelta(days=90) < now - d <= timedelta(days=180))
    auto_high = sum(1 for s, _ in members if s.automatability.value.lower() == "high")

    components = {
        "breadth": min(1.0, math.log1p(len(authors)) * (1 + 0.5 * (len(source_types) - 1)) / (math.log1p(10) * 1.5)),
        "spend": min(spend_count, 5) / 5,
        "frequency": max(freq_scores, default=0.0),
        "underserved": min(complaints, 5) / 5,
        "intensity": sum(s.intensity for s, _ in members) / (3 * len(members)),
        "recency": len(recent) / len(dates) if dates else 0.0,
        "automatability": auto_high / len(members),
        "trend": max(-1.0, min(3.0, (last90 - prev90) / max(1, prev90))),  # informational, not scored
    }
    score = sum(weights.get(k, 0.0) * v for k, v in components.items() if k in DEFAULT_WEIGHTS)

    newest_age = (now - max(dates)).days if dates else 10_000
    if (len(authors) >= th["high_authors"] and len(source_types) >= th["high_source_types"]
            and spend_count >= 1 and newest_age <= th["high_max_age_days"]):
        confidence = "High"
    elif len(authors) >= th["medium_authors"] or (len(authors) >= 2 and spend_count >= 1):
        confidence = "Medium"
    else:
        confidence = "Low"

    return {
        "components": {k: round(v, 3) for k, v in components.items()},
        "score": round(score, 4),
        "confidence": confidence,
        "n_authors": len(authors),
        "source_types": source_types,
        "first_seen": min(dates) if dates else None,
        "last_seen": max(dates) if dates else None,
        "spend_count": spend_count,
    }
