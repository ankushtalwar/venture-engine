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
