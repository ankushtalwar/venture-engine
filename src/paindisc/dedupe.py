"""Document-level de-duplication: exact (content hash) and near-duplicate (MinHash LSH)."""

from __future__ import annotations

import re

import numpy as np
from datasketch import MinHash, MinHashLSH

from .models import Document

NUM_PERM = 64


def _shingles(text: str, k: int = 5) -> set[str]:
    words = re.findall(r"\w+", text.lower())
    if len(words) <= k:
        return {" ".join(words)}
    return {" ".join(words[i:i + k]) for i in range(len(words) - k + 1)}


def minhash(text: str) -> MinHash:
    m = MinHash(num_perm=NUM_PERM)
    for shingle in _shingles(text):
        m.update(shingle.encode())
    return m


class NearDupIndex:
    def __init__(self, threshold: float = 0.8):
        self.lsh = MinHashLSH(threshold=threshold, num_perm=NUM_PERM)
        self._keys: set[str] = set()

    def load(self, store) -> None:
        for doc_id, blob in store.minhashes():
            if doc_id in self._keys:
                continue
            m = MinHash(num_perm=NUM_PERM, hashvalues=np.frombuffer(blob, dtype=np.uint64))
            self.lsh.insert(doc_id, m)
            self._keys.add(doc_id)

    def check_and_add(self, doc_id: str, text: str) -> tuple[str | None, bytes]:
        m = minhash(text)
        blob = m.hashvalues.astype(np.uint64).tobytes()
        hits = [h for h in self.lsh.query(m) if h != doc_id]
        if hits:
            return sorted(hits)[0], blob
        if doc_id not in self._keys:
            self.lsh.insert(doc_id, m)
            self._keys.add(doc_id)
        return None, blob


def ingest(store, index: NearDupIndex, docs: list[Document]) -> tuple[int, int]:
    """Add documents, marking exact and near duplicates. Returns (new, duplicates)."""
    new = dupes = 0
    for doc in docs:
        if store.has_document(doc.id):
            continue
        blob = None
        existing = store.find_by_content_hash(doc.content_hash)
        if existing:
            doc.duplicate_of = existing
        else:
            doc.duplicate_of, blob = index.check_and_add(doc.id, doc.text)
        if store.add_document(doc, blob):
            if doc.duplicate_of:
                dupes += 1
            else:
                new += 1
    return new, dupes
