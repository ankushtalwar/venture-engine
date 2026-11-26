"""Pain-card synthesis and the skeptic pass, both grounded only in verified signals."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from . import mock
from .llm import LLMError
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


def ground_skeptic(cluster: Cluster, review: SkepticReview) -> SkepticReview:
    """Keep only citations to signals in this cluster; an evidence point left with none becomes a hypothesis."""
    valid = set(cluster.signal_ids)
    for point in review.points:
        point.signal_ids = [s for s in point.signal_ids if s in valid]
        if point.kind == "evidence" and not point.signal_ids:
            point.kind = "hypothesis"
    return review


def synthesize_cluster(store, llm, settings, cluster_id: str) -> Cluster:
    """Synthesis + skeptic pass for one cluster, saved with `store.save_cluster`. Works in mock mode.

    Raises KeyError if the cluster doesn't exist, and LLMError if either model call fails (nothing is saved then).
    """
    cluster = store.get_cluster(cluster_id)
    if cluster is None:
        raise KeyError(cluster_id)
    pairs = {s.id: (s, d) for s, d in store.signals_with_docs(list(cluster.signal_ids))}
    members = [pairs[i] for i in cluster.signal_ids if i in pairs]
    if llm.mock:
        cluster.synthesis = mock.synthesize(cluster.canonical, cluster.n_signals, cluster.n_authors,
                                            cluster.source_types)
        cluster.skeptic = mock.skeptic(cluster.n_authors, cluster.source_types, cluster.signal_ids)
        store.save_cluster(cluster)
        return cluster

    pack = evidence_pack(cluster, members)
    with ThreadPoolExecutor(max_workers=2) as pool:  # the two calls are independent
        synth = pool.submit(llm.structured, model=settings.model_synth, system=settings.prompt("synthesize"),
                            user=pack, output_type=Synthesis, max_tokens=1500)
        review = pool.submit(llm.structured, model=settings.model_synth, system=settings.prompt("skeptic"),
                             user=pack, output_type=SkepticReview, max_tokens=1500)
        try:
            synthesis, skeptic = synth.result(), review.result()
        except LLMError:
            raise
        except Exception as exc:  # API errors: one exception type for callers
            raise LLMError(f"synthesis failed for cluster {cluster_id}: {exc}") from exc
    cluster.synthesis = synthesis
    cluster.skeptic = ground_skeptic(cluster, skeptic)
    store.save_cluster(cluster)
    return cluster


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
            c.skeptic = ground_skeptic(c, review)
        store.save_cluster(c)
    return len(packs)
