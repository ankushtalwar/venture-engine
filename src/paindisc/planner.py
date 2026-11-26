"""Query planner: (phrase x segment term x source) arms, chosen by Thompson sampling.

Each collected document is a Bernoulli trial: did it yield at least one verified signal?
Runs that return nothing count as failures, so dead queries fade out.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Arm:
    arm_id: str
    source: str
    family: str
    phrase: str
    segment: str
    term: str
    query: str


def build_arms(phrases_cfg: dict, segments_cfg: dict) -> dict[str, list[Arm]]:
    families: dict[str, list[str]] = phrases_cfg.get("families") or {}
    segments: dict[str, dict] = segments_cfg.get("segments") or {}
    arms: dict[str, dict[str, Arm]] = defaultdict(dict)
    for source, spec in (phrases_cfg.get("sources") or {}).items():
        spec = spec or {}
        templates = spec.get("templates") or ["{phrase} {term}"]
        if spec.get("use_phrases", True):
            chosen = spec.get("families") or list(families)
            phrase_list = [(fam, p) for fam in chosen for p in families.get(fam, [])]
        else:
            phrase_list = [("none", "")]
        for seg_id, seg in segments.items():
            scope = str((seg or {}).get(f"{source}_scope") or "")  # e.g. github_scope: "repo:org/name"
            for term in (seg or {}).get("vocab") or [""]:
                for fam, phrase in phrase_list:
                    for template in templates:
                        filled = template.format(phrase=phrase, term=term, scope=scope)
                        query = " ".join(filled.replace('""', "").split())
                        if not query:
                            continue
                        arm_id = hashlib.sha1(f"{source}|{query}".encode()).hexdigest()[:12]
                        arms[source].setdefault(arm_id, Arm(arm_id, source, fam, phrase, seg_id, term, query))
    return {source: list(by_id.values()) for source, by_id in arms.items()}


def choose(arms: list[Arm], stats: dict[str, dict], budget: int, explore: float = 0.2,
           seed: int | None = None) -> list[Arm]:
    if budget >= len(arms):
        return list(arms)
    rng = np.random.default_rng(seed)

    def sample(arm: Arm) -> float:
        s = stats.get(arm.arm_id) or {}
        verified, docs, empty = s.get("verified", 0), s.get("docs", 0), s.get("empty_runs", 0)
        return rng.beta(1 + verified, 1 + max(0, docs - verified) + empty)

    tried = [a for a in arms if (stats.get(a.arm_id) or {}).get("runs", 0) > 0]
    untried = [a for a in arms if (stats.get(a.arm_id) or {}).get("runs", 0) == 0]
    n_exploit = min(len(tried), budget - int(round(budget * explore)))
    exploit = sorted(tried, key=sample, reverse=True)[:n_exploit]
    chosen_ids = {a.arm_id for a in exploit}
    pool = untried or [a for a in arms if a.arm_id not in chosen_ids]
    k = min(budget - len(exploit), len(pool))
    explore_picks = [pool[i] for i in rng.choice(len(pool), size=k, replace=False)] if k > 0 else []
    return exploit + explore_picks
