"""Job-description engine: aggregate verified tasks across postings, optionally mapped to O*NET tasks.

O*NET (CC BY 4.0): download the database text files and put them in data/onet/
("Task Statements.txt" and, optionally, "Occupation Data.txt").
"""

from __future__ import annotations

import csv
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from sklearn.feature_extraction.text import TfidfVectorizer

from .cluster import cluster_labels
from .models import Document, JobExtraction, JobTask, Status


@dataclass
class OnetTask:
    soc: str
    title: str
    task_id: str
    task: str


def load_onet(directory: Path) -> list[OnetTask] | None:
    tasks_file = directory / "Task Statements.txt"
    if not tasks_file.exists():
        return None
    titles: dict[str, str] = {}
    occupations = directory / "Occupation Data.txt"
    if occupations.exists():
        with occupations.open(encoding="utf-8", errors="replace") as fh:
            for row in csv.DictReader(fh, delimiter="\t"):
                titles[row["O*NET-SOC Code"]] = row.get("Title", "")
    tasks = []
    with tasks_file.open(encoding="utf-8", errors="replace") as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            soc = row["O*NET-SOC Code"]
            tasks.append(OnetTask(soc=soc, title=titles.get(soc, ""), task_id=row["Task ID"], task=row["Task"]))
    return tasks


class OnetMatcher:
    def __init__(self, tasks: list[OnetTask]):
        self.tasks = tasks
        self.vectorizer = TfidfVectorizer(ngram_range=(1, 2), stop_words="english", sublinear_tf=True)
        self.matrix = self.vectorizer.fit_transform([t.task for t in tasks])

    def match(self, texts: list[str], chunk: int = 500) -> list[tuple[OnetTask, float]]:
        out: list[tuple[OnetTask, float]] = []
        for start in range(0, len(texts), chunk):
            sims = (self.vectorizer.transform(texts[start:start + chunk]) @ self.matrix.T).toarray()
            for row in sims:
                best = int(row.argmax())
                out.append((self.tasks[best], float(row[best])))
        return out


def aggregate_tasks(pairs: list[tuple[JobExtraction, Document]], matcher: OnetMatcher | None = None,
                    min_similarity: float = 0.25, distance_threshold: float = 0.5) -> list[dict]:
    rows: list[tuple[JobTask, JobExtraction, Document]] = [(t, job, doc) for job, doc in pairs for t in job.tasks]
    if not rows:
        return []

    keys: list[str | None] = [None] * len(rows)
    onet_for_key: dict[str, OnetTask] = {}
    if matcher:
        for i, (task, sim) in enumerate(matcher.match([r[0].task for r in rows])):
            if sim >= min_similarity:
                keys[i] = f"onet:{task.task_id}"
                onet_for_key[keys[i]] = task
    unmatched = [i for i, k in enumerate(keys) if k is None]
    if unmatched:
        labels = cluster_labels([rows[i][0].task for i in unmatched], distance_threshold)
        for i, label in zip(unmatched, labels):
            keys[i] = f"text:{label}"

    groups: dict[str, list[int]] = {}
    for i, key in enumerate(keys):
        groups.setdefault(key, []).append(i)

    results = []
    for key, idx in groups.items():
        members = [rows[i] for i in idx]
        onet = onet_for_key.get(key)
        tasks_text = Counter(m[0].task for m in members)
        pay_quotes = sorted({m[1].pay.quote for m in members if m[1].pay.status == Status.observed})
        results.append({
            "task": onet.task if onet else tasks_text.most_common(1)[0][0],
            "onet": f"{onet.soc} {onet.title}".strip() if onet else "",
            "postings": len({m[2].id for m in members}),
            "companies": len({m[2].metrics.get("company") or m[2].id for m in members}),
            "tools": [t for t, _ in Counter(t for m in members for t in m[0].tools).most_common(5)],
            "inputs": [t for t, _ in Counter(t for m in members for t in m[0].inputs).most_common(5)],
            "frequency": [f for f, _ in Counter(m[0].frequency.value for m in members
                                                if m[0].frequency.status == Status.observed).most_common(3)],
            "rules_based_share": round(sum(m[0].rules_based for m in members) / len(members), 2),
            "pay_quotes": pay_quotes[:5],
            "examples": [{"quote": m[0].quote, "url": m[2].url, "title": m[2].title} for m in members[:3]],
        })
    results.sort(key=lambda r: (r["companies"], r["postings"]), reverse=True)
    return results
