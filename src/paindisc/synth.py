"""Pain-card synthesis and the skeptic pass, both grounded only in verified signals."""

from __future__ import annotations

from . import mock
from .models import Cluster, Document, PainSignal, SkepticReview, Status, Synthesis


def evidence_pack(cluster: Cluster, members: list[tuple[PainSignal, Document]], max_signals: int = 25) -> str:
    lines = [
        f"CLUSTER: {cluster.canonical}",
        f"STATS: {cluster.n_signals} verified signals, {cluster.n_authors} distinct authors, "
        f"source types: {', '.join(cluster.source_types)}",
        "SIGNALS:",
    ]
    for signal, doc in members[:max_signals]:
        date = doc.published_at.date().isoformat() if doc.published_at else "unknown date"
        lines.append(f"[{signal.id}] {doc.source}/{doc.source_type} {date} {doc.url}")
        lines.append(f'  problem: "{signal.problem.quote}"')
        for name in ("workaround", "occurrence_freq", "financial_impact", "existing_tool_complaint"):
            ev = getattr(signal, name)
            if ev.status == Status.observed:
                lines.append(f'  {name}: "{ev.quote}"')
            elif ev.status == Status.derived:
                lines.append(f"  {name} (derived): {ev.value} - {ev.basis}")
        for spend in signal.spend_evidence:
            amount = f": {spend.amount}" if spend.amount else ""
            lines.append(f'  spend ({spend.type}{amount}): "{spend.quote}"')
        for name in ("segment", "affected_role", "likely_buyer"):
            ev = getattr(signal, name)
            if ev.known:
                lines.append(f"  {name} ({ev.status.value}): {ev.value}")
    return "\n".join(lines)


def run_synthesis(store, llm, settings, top: int = 20, batch: bool = False) -> int:
    clusters = store.clusters()[:top]
    if not clusters:
        return 0
    by_id = {s.id: (s, d) for s, d in store.signals_with_docs()}
    packs = {}
    for c in clusters:
        members = [by_id[i] for i in c.signal_ids if i in by_id]
        packs[c.id] = (c, members, evidence_pack(c, members))

    if llm.mock:
        for c, members, _ in packs.values():
            c.synthesis = mock.synthesize(c.canonical, c.n_signals, c.n_authors, c.source_types)
            c.skeptic = mock.skeptic(c.n_authors, c.source_types, c.signal_ids)
            store.save_cluster(c)
        return len(packs)

    items = [(cid, pack) for cid, (_, _, pack) in packs.items()]
    synth = llm.run(model=settings.model_synth, system=settings.prompt("synthesize"), items=items,
                    output_type=Synthesis, max_tokens=1500, batch=batch)
    skeptic = llm.run(model=settings.model_synth, system=settings.prompt("skeptic"), items=items,
                      output_type=SkepticReview, max_tokens=1500, batch=batch)
    for cid, (c, _, _) in packs.items():
        if isinstance(synth.get(cid), Synthesis):
            c.synthesis = synth[cid]
        review = skeptic.get(cid)
        if isinstance(review, SkepticReview):
            valid = set(c.signal_ids)
            for point in review.points:  # keep only citations to signals in this cluster
                point.signal_ids = [s for s in point.signal_ids if s in valid]
                if point.kind == "evidence" and not point.signal_ids:
                    point.kind = "hypothesis"
            c.skeptic = review
        store.save_cluster(c)
    return len(packs)
