"""Orchestration: collect -> triage -> extract (+verify) -> jobs -> prices -> cluster -> synthesize -> report."""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections import Counter
from pathlib import Path
from typing import Callable

import httpx
from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS

from . import mock
from .cluster import apply_merges, apply_overrides, build_clusters
from .collectors import (BOARD_COLLECTORS, QUERY_COLLECTORS, CollectorUnavailable, PricingCollector, Query,
                         WaybackCollector)
from .config import Settings
from .dedupe import NearDupIndex, ingest
from .http import PoliteClient
from .jobtasks import OnetMatcher, aggregate_tasks, load_onet
from .llm import LLM
from .models import (Document, ExtractionResult, JobExtraction, MergePlan, PainSignal, PriceExtraction,
                     TriageResult)
from .planner import build_arms, choose
from .registry import Registry, RegistryError
from .report import write_report
from .store import Store
from .synth import run_synthesis
from .verify import verify_job, verify_prices, verify_signal


def render_doc(doc: Document, max_chars: int) -> str:
    date = doc.published_at.date().isoformat() if doc.published_at else "unknown"
    return (f'<document source="{doc.source}" type="{doc.source_type}" url="{doc.url}" published="{date}">\n'
            f"TITLE: {doc.title}\n\n{doc.text[:max_chars]}\n</document>")


def _open_store(settings: Settings):
    """Postgres when DATABASE_URL is set, else the SQLite file at db_path."""
    from .storage import open_store
    return open_store(settings)


class Pipeline:
    def __init__(self, settings: Settings, store: Store | None = None, transport: httpx.BaseTransport | None = None,
                 log: Callable[[str], None] = print, sleep: Callable[[float], None] = time.sleep):
        self.settings = settings
        self.store = store or _open_store(settings)
        self.registry = Registry.load(settings.config_dir)
        self.client = PoliteClient(settings, self.registry, transport=transport, sleep=sleep)
        self.llm = LLM(settings, log=log)
        self.log = log

    @property
    def model_label(self) -> str:
        return "mock" if self.llm.mock else "claude"

    # ------------------------------------------------------------ collect

    def collect(self, sources: list[str], budget: int = 20, limit: int = 50, seed: int | None = None) -> dict:
        index = NearDupIndex()
        index.load(self.store)
        arms_by_source = build_arms(self.settings.load_yaml("phrases.yaml"), self.settings.load_yaml("segments.yaml"))
        boards = self.settings.load_yaml("job_boards.yaml")
        targets = [t for t in self.settings.load_yaml("pricing_targets.yaml").get("targets") or []
                   if t.get("terms_checked")]
        summary: dict[str, dict] = {}

        for source in sources:
            new = dupes = runs = 0
            try:
                if source in QUERY_COLLECTORS:
                    collector = QUERY_COLLECTORS[source](self.client, self.settings)
                    arms = arms_by_source.get(source, [])
                    self.store.upsert_arms(arms)
                    for arm in choose(arms, self.store.arm_stats(source), budget, seed=seed):
                        docs = self._safe(lambda: collector.collect(Query(arm.query, arm_id=arm.arm_id), limit=limit),
                                          f"{source} '{arm.query}'")
                        if docs is None:
                            continue
                        n, d = ingest(self.store, index, docs)
                        self.store.record_arm_run(arm.arm_id, n)
                        new, dupes, runs = new + n, dupes + d, runs + 1
                elif source in BOARD_COLLECTORS:
                    collector = BOARD_COLLECTORS[source](self.client, self.settings)
                    for token in boards.get(source) or []:
                        query = Query(str(token), params={"title_filter": boards.get("title_filter")})
                        docs = self._safe(lambda: collector.collect(query), f"{source} '{token}'")
                        if docs is None:
                            continue
                        n, d = ingest(self.store, index, docs)
                        new, dupes, runs = new + n, dupes + d, runs + 1
                elif source in ("pricing", "wayback"):
                    collector = (PricingCollector if source == "pricing" else WaybackCollector)(self.client, self.settings)
                    for target in targets:
                        if source == "pricing":
                            docs = self._safe(lambda: [collector.collect_target(target)], f"pricing {target['url']}")
                        else:
                            docs = self._safe(lambda: collector.collect_target(target), f"wayback {target['url']}")
                        if docs is None:
                            continue
                        n, d = ingest(self.store, index, docs)
                        new, dupes, runs = new + n, dupes + d, runs + 1
                else:
                    self.log(f"{source}: unknown or not-yet-approved source (see config/registry.yaml)")
                    continue
            except CollectorUnavailable as exc:
                self.log(f"{source}: skipped ({exc})")
                continue
            summary[source] = {"queries": runs, "new": new, "duplicates": dupes}
            self.log(f"{source}: {runs} queries, {new} new documents, {dupes} duplicates")
        return summary

    def _safe(self, fn, label: str):
        try:
            return fn()
        except CollectorUnavailable:
            raise
        except RegistryError as exc:
            self.log(f"  blocked by registry: {label}: {exc}")
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            self.log(f"  failed: {label}: {exc}")
        return None

    def load_fixture(self, path: Path) -> tuple[int, int]:
        """Load synthetic documents (JSONL) for demos and tests."""
        index = NearDupIndex()
        index.load(self.store)
        docs = []
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            raw = json.loads(line)
            raw.setdefault("route", "fixture")
            raw.setdefault("captured_at", raw.get("published_at"))
            raw["id"] = Document.make_id(raw["source"], raw["external_id"])
            docs.append(Document.model_validate(raw))
        return ingest(self.store, index, docs)

    # ------------------------------------------------------------ LLM steps

    def triage(self, batch: bool = False, limit: int | None = None) -> dict:
        docs = self.store.docs_for_triage(limit)
        if self.llm.mock:
            results = {d.id: mock.triage(d) for d in docs}
        else:
            results = self.llm.run(model=self.settings.model_triage, system=self.settings.prompt("triage"),
                                   items=[(d.id, render_doc(d, 6000)) for d in docs], output_type=TriageResult,
                                   max_tokens=300, batch=batch)
        model = "mock" if self.llm.mock else self.settings.model_triage
        pain = errors = 0
        for doc in docs:
            result = results.get(doc.id)
            if not isinstance(result, TriageResult):
                errors += 1
                continue
            self.store.save_triage(doc.id, result, model)
            pain += int(result.is_pain)
        self.log(f"triage: {len(docs) - errors} documents, {pain} business pain, {errors} errors")
        return {"triaged": len(docs) - errors, "pain": pain, "errors": errors}

    def extract(self, batch: bool = False, limit: int | None = None) -> dict:
        docs = self.store.docs_for_extraction(limit)
        if self.llm.mock:
            results = {d.id: mock.extract(d) for d in docs}
        else:
            results = self.llm.run(model=self.settings.model_extract, system=self.settings.prompt("extract"),
                                   items=[(d.id, render_doc(d, 20000)) for d in docs], output_type=ExtractionResult,
                                   max_tokens=6000, batch=batch)
        model = "mock" if self.llm.mock else self.settings.model_extract
        kept_total = dropped_total = errors = 0
        for doc in docs:
            result = results.get(doc.id)
            if not isinstance(result, ExtractionResult):
                errors += 1
                continue
            kept, notes, dropped = [], [], 0
            for raw in result.signals:
                verified, signal_notes = verify_signal(raw, doc.reference_text)
                notes.extend(signal_notes)
                if verified is None:
                    dropped += 1
                    continue
                sid = hashlib.sha1(f"{doc.id}:{verified.problem.quote}".encode()).hexdigest()[:12]
                kept.append(PainSignal(**verified.model_dump(), id=sid, document_id=doc.id, notes=signal_notes))
            self.store.save_extraction(doc.id, kept, dropped, notes, model)
            kept_total, dropped_total = kept_total + len(kept), dropped_total + dropped
        self.log(f"extract: {kept_total} verified signals from {len(docs) - errors} documents, "
                 f"{dropped_total} dropped by verification, {errors} errors")
        return {"signals": kept_total, "dropped": dropped_total, "errors": errors}

    def jobs(self, batch: bool = False, limit: int | None = None) -> dict:
        docs = self.store.docs_for_jobs(limit)
        if self.llm.mock:
            results = {d.id: mock.extract_job(d) for d in docs}
        else:
            results = self.llm.run(model=self.settings.model_extract, system=self.settings.prompt("jobs"),
                                   items=[(d.id, render_doc(d, 15000)) for d in docs], output_type=JobExtraction,
                                   max_tokens=4000, batch=batch)
        model = "mock" if self.llm.mock else self.settings.model_extract
        tasks = 0
        for doc in docs:
            result = results.get(doc.id)
            if isinstance(result, JobExtraction):
                job, notes = verify_job(result, doc.reference_text)
                self.store.save_job(doc.id, job, notes, model)
                tasks += len(job.tasks)
        self.log(f"jobs: {len(docs)} postings, {tasks} verified tasks")
        return {"postings": len(docs), "tasks": tasks}

    def prices(self, batch: bool = False) -> dict:
        docs = self.store.docs_for_prices()
        if self.llm.mock:
            results = {d.id: mock.extract_prices(d) for d in docs}
        else:
            results = self.llm.run(model=self.settings.model_triage, system=self.settings.prompt("prices"),
                                   items=[(d.id, render_doc(d, 15000)) for d in docs], output_type=PriceExtraction,
                                   max_tokens=2000, batch=batch)
        model = "mock" if self.llm.mock else self.settings.model_triage
        for doc in docs:
            result = results.get(doc.id)
            if isinstance(result, PriceExtraction):
                prices, notes = verify_prices(result, doc.reference_text)
                self.store.save_prices(doc.id, prices, notes, model)
        self.log(f"prices: {len(docs)} pricing snapshots processed")
        return {"snapshots": len(docs)}

    # ------------------------------------------------------------ analysis

    def cluster(self, threshold: float | None = None) -> int:
        cfg = self.settings.load_yaml("scoring.yaml")
        previous = {c.id: c for c in self.store.clusters()}
        pairs = self.store.signals_with_docs()
        weights, thresholds = cfg.get("weights"), cfg.get("confidence")
        clusters = build_clusters(pairs, threshold or cfg.get("distance_threshold", 0.7), weights, thresholds,
                                  cfg.get("embedding", "tfidf"))
        if cfg.get("llm_merge", True) and not self.llm.mock and len(clusters) > 1:
            candidates = clusters[: int(cfg.get("llm_merge_top", 300))]
            listing = "\n".join(f"{c.id}: {c.canonical}" for c in candidates)
            try:
                plan = self.llm.structured(model=self.settings.model_extract, system=self.settings.prompt("merge"),
                                           user=listing, output_type=MergePlan, max_tokens=4000)
                before = len(clusters)
                clusters = apply_merges(clusters, plan, pairs, weights, thresholds)
                self.log(f"cluster: merge judge combined {before - len(clusters)} clusters")
            except Exception as exc:  # merging is an improvement, not a requirement
                self.log(f"cluster: merge judge skipped ({exc})")
        overrides = self.store.overrides()  # reviewer merges/splits from the UI, oldest first
        if overrides:
            before = len(clusters)
            clusters = apply_overrides(clusters, overrides, pairs, weights, thresholds)
            self.log(f"cluster: re-applied {len(overrides)} manual overrides ({before} -> {len(clusters)} clusters)")
        for c in clusters:  # keep write-ups for clusters whose membership didn't change
            if c.id in previous:
                c.synthesis, c.skeptic = previous[c.id].synthesis, previous[c.id].skeptic
        self._carry_labels(previous, clusters, cfg.get("trends", {}).get("match_jaccard", 0.5))
        self.store.replace_clusters(clusters)
        self.log(f"cluster: {len(clusters)} problems from {sum(c.n_signals for c in clusters)} signals")
        return len(clusters)

    def _carry_labels(self, previous: dict, clusters: list, threshold: float) -> int:
        """Review labels follow a cluster whose id changed because its membership changed (signal-set overlap)."""
        labelled = self.store.labels("cluster")
        if not any(cid in labelled for cid in previous):
            return 0
        from .trends import match_clusters
        # match every old cluster (not only labelled ones) so each label goes to its cluster's true successor
        matches = match_clusters(previous.values(), clusters, threshold)
        moved = sum(self.store.move_label("cluster", old_id, new_id)
                    for new_id, old_id in matches.items() if new_id != old_id and old_id in labelled)
        if moved:
            self.log(f"cluster: carried {moved} review labels to re-formed clusters")
        return moved

    def synthesize(self, top: int = 20, batch: bool = False) -> int:
        n = run_synthesis(self.store, self.llm, self.settings, top=top, batch=batch)
        self.log(f"synthesize: {n} pain cards written up")
        return n

    def job_tasks(self) -> list[dict]:
        onet = load_onet(self.settings.home / "data" / "onet")
        matcher = OnetMatcher(onet) if onet else None
        return aggregate_tasks(self.store.jobs_with_docs(), matcher)

    def report(self, top: int = 20, demo: bool = False, stem: str | None = None) -> tuple[Path, Path]:
        md, html = write_report(self.store, self.settings.report_dir, top=top, job_tasks=self.job_tasks(),
                                demo=demo, stem=stem)
        self.log(f"report: {md}\n        {html}")
        return md, html

    def run(self, sources: list[str], budget: int = 20, limit: int = 50, top: int = 20, batch: bool = False,
            seed: int | None = None) -> tuple[Path, Path]:
        self.collect(sources, budget=budget, limit=limit, seed=seed)
        self.triage(batch=batch)
        self.extract(batch=batch)
        self.jobs(batch=batch)
        self.prices(batch=batch)
        self.cluster()
        self.synthesize(top=top, batch=batch)
        return self.report(top=top)

    # ------------------------------------------------------------ deep dive

    def deepdive_agentic(self, top: int = 10, sources: tuple[str, ...] = ("hn", "github"),
                         max_queries: int = 4, max_total: int = 30) -> dict:
        """Tool-using Claude loop over registered collectors (mock planner offline), then re-analyse."""
        from .agent.deepdive import run as run_agent, summarize
        stats = run_agent(self, top=top, sources=sources, max_queries=max_queries, max_total=max_total)
        self.log(f"deepdive (agentic): {summarize(stats)}")
        for c in stats.get("per_cluster", []):
            if c.get("notes"):
                self.log(f"  [{c['cluster_id']}] {c['notes']}")
        if stats.get("new_documents"):  # nothing new: skip a paid re-analysis
            self.triage()
            self.extract()
            self.cluster()
            self.synthesize(top=top)
        return stats

    def deepdive(self, top: int = 10, sources: tuple[str, ...] = ("hn", "github"), limit: int = 30) -> None:
        """Search for corroborating (and contradicting) evidence for the top clusters, then re-run analysis."""
        by_id = {s.id: s for s, _ in self.store.signals_with_docs()}
        index = NearDupIndex()
        index.load(self.store)
        for c in self.store.clusters()[:top]:
            text = " ".join(by_id[i].canonical for i in c.signal_ids if i in by_id).lower()
            words = [w for w in re.findall(r"[a-z][a-z\-]{2,}", text)
                     if w not in ENGLISH_STOP_WORDS and w not in {"today", "must", "because", "they"}]
            keywords = " ".join(w for w, _ in Counter(words).most_common(3))
            if not keywords:
                continue
            for source in sources:
                if source not in QUERY_COLLECTORS:
                    continue
                collector = QUERY_COLLECTORS[source](self.client, self.settings)
                try:
                    docs = self._safe(lambda: collector.collect(Query(keywords, arm_id=f"deepdive:{c.id}"),
                                                                limit=limit), f"deepdive {source} '{keywords}'")
                except CollectorUnavailable as exc:
                    self.log(f"{source}: skipped ({exc})")
                    continue
                if docs:
                    n, _ = ingest(self.store, index, docs)
                    self.log(f"deepdive [{c.id}] {source} '{keywords}': {n} new documents")
        self.triage()
        self.extract()
        self.cluster()
        self.synthesize(top=top)
