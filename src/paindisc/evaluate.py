"""Evaluation against a hand-labelled gold set (JSONL).

Each line: {"id": "<document id>" or "url": "<document url>", "is_pain": true|false, "no_figures": true|false}
`no_figures: true` marks documents that contain no money figures: no signal from them may carry a financial impact.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from .models import Status


def evaluate(store, gold_path: Path) -> dict:
    gold = [json.loads(line) for line in gold_path.read_text().splitlines() if line.strip()]
    by_url = {r["url"]: r["id"] for r in store.db.execute(
        "SELECT id, json_extract(data, '$.url') AS url FROM documents")}
    predictions = store.triage_results()

    tp = fp = fn = tn = missing = 0
    no_figures: set[str] = set()
    for item in gold:
        doc_id = item.get("id") or by_url.get(item.get("url", ""))
        if item.get("no_figures") and doc_id:
            no_figures.add(doc_id)
        if doc_id not in predictions:
            missing += 1
            continue
        predicted, truth = predictions[doc_id], bool(item["is_pain"])
        tp += predicted and truth
        fp += predicted and not truth
        fn += (not predicted) and truth
        tn += (not predicted) and (not truth)

    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    notes = store.extraction_notes()
    violations = [s.id for s, d in store.signals_with_docs()
                  if d.id in no_figures and s.financial_impact.status in (Status.observed, Status.derived)]
    return {
        "gold_items": len(gold),
        "not_triaged_yet": missing,
        "triage": {"tp": tp, "fp": fp, "fn": fn, "tn": tn, "precision": precision, "recall": recall},
        "signals_dropped_by_verification": sum(1 for n in notes if n.startswith("dropped")),
        "field_downgrades": dict(Counter(n.split(":")[0] for n in notes if not n.startswith("dropped"))),
        "no_figures_docs": len(no_figures),
        "financial_fabrications": violations,
    }
