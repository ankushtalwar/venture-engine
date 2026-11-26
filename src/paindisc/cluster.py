"""Group verified signals into problems: cluster on the job to be done, not the vendor."""

from __future__ import annotations

import hashlib
import os
import re

import numpy as np
from scipy.sparse import hstack
from sklearn.cluster import AgglomerativeClustering
from sklearn.decomposition import TruncatedSVD
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.preprocessing import normalize

from .models import Cluster, Document, MergePlan, PainSignal
from .score import score_members


EXACT_LIMIT = 4000  # up to this many texts, use exact pairwise cosine on the sparse TF-IDF matrix


def tfidf(texts: list[str]):
    """Word (1-2 gram) + character (3-5 gram) TF-IDF, rows L2-normalised.

    Swap for a sentence-embedding model once volumes grow; the interface is just texts -> vectors.
    """
    matrices = []
    try:
        words = TfidfVectorizer(ngram_range=(1, 2), stop_words="english", sublinear_tf=True)
        matrices.append(words.fit_transform(texts))
    except ValueError:  # empty vocabulary (very short texts)
        pass
    chars = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), sublinear_tf=True)
    matrices.append(chars.fit_transform(texts))
    return normalize(hstack(matrices).tocsr())


def sbert(texts: list[str]) -> np.ndarray:
    """Optional semantic embeddings (`uv sync --extra embeddings`, then embedding: sbert in scoring.yaml)."""
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer(os.environ.get("PAINDISC_SBERT_MODEL", "all-MiniLM-L6-v2"))
    return np.asarray(model.encode(texts, normalize_embeddings=True))


def cluster_labels(texts: list[str], distance_threshold: float = 0.7, method: str = "tfidf") -> np.ndarray:
    if len(texts) == 1:
        return np.zeros(1, dtype=int)
    if method == "sbert":
        E = sbert(texts)
        distances = np.clip(1.0 - E @ E.T, 0.0, 2.0)
        np.fill_diagonal(distances, 0.0)
        return AgglomerativeClustering(n_clusters=None, metric="precomputed", linkage="average",
                                       distance_threshold=distance_threshold).fit_predict(distances)
    X = tfidf(texts)
    if X.shape[0] <= EXACT_LIMIT:
        distances = np.clip(1.0 - (X @ X.T).toarray(), 0.0, 2.0)
        np.fill_diagonal(distances, 0.0)
        model = AgglomerativeClustering(n_clusters=None, metric="precomputed", linkage="average",
                                        distance_threshold=distance_threshold)
        return model.fit_predict(distances)
    reduced = normalize(TruncatedSVD(n_components=256, random_state=0).fit_transform(X))
    model = AgglomerativeClustering(n_clusters=None, metric="cosine", linkage="average",
                                    distance_threshold=distance_threshold)
    return model.fit_predict(reduced)


# Words the canonical template puts in every statement; they'd make all statements look alike.
_TEMPLATE_WORDS = re.compile(r"\b(must|because|today|they|their|we|our)\b", re.I)


def signal_text(signal: PainSignal) -> str:
    return signal.canonical or signal.problem.value or signal.problem.quote


def _cluster_text(signal: PainSignal) -> str:
    return " ".join(_TEMPLATE_WORDS.sub(" ", signal_text(signal)).split())


Pair = tuple[PainSignal, Document]


def make_cluster(members: list[Pair], weights: dict | None = None, thresholds: dict | None = None) -> Cluster:
    stats = score_members(members, weights, thresholds)
    # representative statement: the most specific signal (A before B before C), then the most intense
    rep = sorted(members, key=lambda m: (m[0].specificity, -m[0].intensity))[0][0]
    ids = sorted(s.id for s, _ in members)
    return Cluster(
        id=hashlib.sha1("|".join(ids).encode()).hexdigest()[:12],
        canonical=signal_text(rep),
        signal_ids=ids,
        n_signals=len(members),
        n_authors=stats["n_authors"],
        source_types=stats["source_types"],
        first_seen=stats["first_seen"],
        last_seen=stats["last_seen"],
        components=stats["components"],
        score=stats["score"],
        confidence=stats["confidence"],
    )


def build_clusters(pairs: list[Pair], distance_threshold: float = 0.7, weights: dict | None = None,
                   thresholds: dict | None = None, method: str = "tfidf") -> list[Cluster]:
    if not pairs:
        return []
    labels = cluster_labels([_cluster_text(s) for s, _ in pairs], distance_threshold, method)
    groups: dict[int, list[Pair]] = {}
    for label, pair in zip(labels, pairs):
        groups.setdefault(int(label), []).append(pair)
    clusters = [make_cluster(members, weights, thresholds) for members in groups.values()]
    clusters.sort(key=lambda c: c.score, reverse=True)
    return clusters


def apply_merges(clusters: list[Cluster], plan: MergePlan, pairs: list[Pair], weights: dict | None = None,
                 thresholds: dict | None = None) -> list[Cluster]:
    """Merge clusters an LLM judged to be the same job to be done (statements only; no evidence is created)."""
    by_cluster = {c.id: c for c in clusters}
    by_signal = {s.id: (s, d) for s, d in pairs}
    used: set[str] = set()
    merged = []
    for group in plan.groups:
        ids = [i for i in dict.fromkeys(group.ids) if i in by_cluster and i not in used]
        if len(ids) < 2:
            continue
        used.update(ids)
        members = [by_signal[sid] for cid in ids for sid in by_cluster[cid].signal_ids if sid in by_signal]
        merged.append(make_cluster(members, weights, thresholds))
    out = [c for c in clusters if c.id not in used] + merged
    out.sort(key=lambda c: c.score, reverse=True)
    return out


# ------------------------------------------------------------------ human overrides (review UI)
#
# Cluster ids are hashes of their signal ids and every `paindisc cluster` run rebuilds clusters from scratch, so a
# reviewer's merges and splits are stored as overrides (store.add_override) and re-applied, oldest first, after
# automatic clustering and the LLM merge judge. Payloads:
#   merge: {"signal_ids": [...]}  these signals belong together: the clusters holding them become one cluster
#   split: {"signal_ids": [...]}  these signals form their own cluster, apart from the rest
# Signal ids that no longer exist are ignored.


def merge_clusters(clusters: list[Cluster], pairs: list[Pair], weights: dict | None = None,
                   thresholds: dict | None = None) -> Cluster:
    """One rescored cluster holding every (still existing) signal of `clusters`. No synthesis."""
    by_signal = {s.id: (s, d) for s, d in pairs}
    ids = list(dict.fromkeys(sid for c in clusters for sid in c.signal_ids if sid in by_signal))
    if not ids:
        raise ValueError("none of these clusters' signals exist any more")
    return make_cluster([by_signal[sid] for sid in ids], weights, thresholds)


def split_cluster(cluster: Cluster, signal_ids: list[str], pairs: list[Pair], weights: dict | None = None,
                  thresholds: dict | None = None) -> tuple[Cluster, Cluster]:
    """Split `signal_ids` off `cluster`. Returns (split-off cluster, remainder), both rescored, no synthesis."""
    by_signal = {s.id: (s, d) for s, d in pairs}
    members = [sid for sid in cluster.signal_ids if sid in by_signal]
    moved = set(signal_ids)
    unknown = moved - set(cluster.signal_ids)
    if unknown:
        raise ValueError(f"signals not in cluster {cluster.id}: {', '.join(sorted(unknown))}")
    out = [sid for sid in members if sid in moved]
    rest = [sid for sid in members if sid not in moved]
    if not out:
        raise ValueError("none of the signals to split off exist any more")
    if not rest:
        raise ValueError("a split must leave at least one signal in the original cluster")
    return (make_cluster([by_signal[sid] for sid in out], weights, thresholds),
            make_cluster([by_signal[sid] for sid in rest], weights, thresholds))


def apply_overrides(clusters: list[Cluster], overrides: list[dict], pairs: list[Pair], weights: dict | None = None,
                    thresholds: dict | None = None) -> list[Cluster]:
    """Re-apply stored merge/split overrides, in order, to freshly built clusters.

    Clusters whose membership ends up unchanged are returned as they came in; changed ones are rebuilt with
    `make_cluster` (new id, rescored, no synthesis). The result is sorted by score, highest first.
    """
    by_signal = {s.id: (s, d) for s, d in pairs}
    original = {frozenset(c.signal_ids): c for c in clusters}
    groups: list[set[str]] = [set(c.signal_ids) for c in clusters]

    for override in overrides:
        payload = override.get("payload") or {}
        wanted = {sid for sid in payload.get("signal_ids") or [] if sid in by_signal}
        if not wanted:
            continue
        touched = [g for g in groups if g & wanted]
        if override.get("kind") == "merge":
            if len(touched) < 2:
                continue  # already together (or no longer clustered)
            groups = [g for g in groups if not g & wanted] + [set().union(*touched)]
        elif override.get("kind") == "split":
            moved = wanted & set().union(*touched) if touched else set()
            if not moved:
                continue
            kept = [g - moved for g in touched]
            groups = [g for g in groups if not g & wanted] + [g for g in kept if g] + [moved]

    out: list[Cluster] = []
    for group in groups:
        same = original.get(frozenset(group))
        if same is not None:
            out.append(same)
            continue
        members = [by_signal[sid] for sid in sorted(group) if sid in by_signal]
        if members:
            out.append(make_cluster(members, weights, thresholds))
    out.sort(key=lambda c: c.score, reverse=True)
    return out
