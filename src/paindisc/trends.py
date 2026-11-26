"""Trend alerts (SPEC backlog #8): how cluster scores moved between clustering runs.

Every `Pipeline.cluster()` run writes a snapshot (store.replace_clusters). Cluster ids are hashes of their
signal sets, so they change whenever membership changes; clusters are therefore followed across runs by
signal-set overlap (Jaccard >= trends.match_jaccard), best match first, one-to-one. The same matching
carries review labels forward to the new cluster ids.

Statuses: new (no earlier cluster matches), rising / falling (score moved by at least rising_delta /
falling_delta), gone (an earlier cluster nothing matches), steady (the rest; not listed as an alert).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from typing import Iterable, Mapping

DEFAULTS = {"match_jaccard": 0.5, "rising_delta": 0.05, "falling_delta": 0.05, "compare_days": 7}
STATUS_ORDER = ("new", "rising", "falling", "gone")
_EPS = 1e-9  # so a delta of exactly rising_delta counts despite float error (0.55 - 0.5 = 0.04999...)


def _signal_sets(items) -> dict[str, frozenset[str]]:
    """Accepts Cluster objects, snapshot rows ({cluster_id | id, signal_ids}) or a {id: signal_ids} mapping."""
    if isinstance(items, Mapping):
        return {str(k): frozenset(v) for k, v in items.items()}
    out: dict[str, frozenset[str]] = {}
    for item in items:
        if isinstance(item, Mapping):
            out[item.get("cluster_id") or item["id"]] = frozenset(item.get("signal_ids") or ())
        else:
            out[item.id] = frozenset(item.signal_ids)
    return out


def jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    a, b = set(a), set(b)
    return len(a & b) / len(a | b) if a or b else 0.0


def match_clusters(old, new, threshold: float = DEFAULTS["match_jaccard"]) -> dict[str, str]:
    """Map new cluster ids to the old cluster each one continues: {new_id: old_id}.

    Pairs need Jaccard(signal sets) >= threshold and at least one shared signal. Pairs are taken best first
    (Jaccard, then shared-signal count, then ids for determinism) and each id is used at most once, so an
    unchanged cluster always maps to itself.
    """
    old_sets, new_sets = _signal_sets(old), _signal_sets(new)
    owners: dict[str, set[str]] = defaultdict(set)
    for old_id, signals in old_sets.items():
        for sid in signals:
            owners[sid].add(old_id)
    candidates = []
    for new_id, signals in new_sets.items():
        shared = Counter(old_id for sid in signals for old_id in owners.get(sid, ()))
        for old_id, inter in shared.items():
            score = inter / (len(signals) + len(old_sets[old_id]) - inter)
            if score + _EPS >= threshold:
                candidates.append((-score, -inter, new_id, old_id))
    candidates.sort()
    matched: dict[str, str] = {}
    used: set[str] = set()
    for _, _, new_id, old_id in candidates:
        if new_id not in matched and old_id not in used:
            matched[new_id] = old_id
            used.add(old_id)
    return matched


def trend_config(cfg: Mapping | None) -> dict:
    """Settings from scoring.yaml: pass the whole file or just its `trends:` block."""
    block = (cfg or {}).get("trends", cfg) if isinstance(cfg, Mapping) else None
    block = block if isinstance(block, Mapping) else {}
    return {**DEFAULTS, **{k: block[k] for k in DEFAULTS if block.get(k) is not None}}


def _day(iso: str):
    return datetime.fromisoformat(iso).date()


def pick_baseline(runs: list[dict], compare_days: int) -> dict | None:
    """The newest run at least `compare_days` calendar days (UTC) before the latest; else the previous run.

    Calendar days, not hours, so a weekly job that drifts by a few minutes still compares week to week.
    `runs` is newest first (store.snapshot_runs()).
    """
    if len(runs) < 2:
        return None
    latest = _day(runs[0]["run_at"])
    for run in runs[1:]:
        if (latest - _day(run["run_at"])).days >= compare_days:
            return run
    return runs[1]


def diff_snapshots(previous: list[dict], current: list[dict], cfg: Mapping | None = None) -> dict:
    """Compare two snapshots (store.snapshot rows). Returns {items, status_by_cluster}."""
    tc = trend_config(cfg)
    prev_by_id = {r["cluster_id"]: r for r in previous}
    matches = match_clusters(previous, current, float(tc["match_jaccard"]))
    rising, falling = float(tc["rising_delta"]), float(tc["falling_delta"])
    items: list[dict] = []
    status_by_cluster: dict[str, str] = {}
    for row in current:
        score = float(row["score"] or 0.0)
        old_id = matches.get(row["cluster_id"])
        if old_id is None:
            status, previous_score, delta = "new", None, None
        else:
            previous_score = float(prev_by_id[old_id]["score"] or 0.0)
            delta = score - previous_score
            status = ("rising" if delta + _EPS >= rising else
                      "falling" if -delta + _EPS >= falling else "steady")
            delta = round(delta, 4)
        status_by_cluster[row["cluster_id"]] = status
        if status != "steady":
            items.append({"cluster_id": row["cluster_id"], "title": row["title"], "status": status, "score": score,
                          "previous_score": previous_score, "delta": delta, "previous_cluster_id": old_id})
    continued = set(matches.values())
    for row in previous:
        if row["cluster_id"] not in continued:
            items.append({"cluster_id": row["cluster_id"], "title": row["title"], "status": "gone", "score": None,
                          "previous_score": float(row["score"] or 0.0), "delta": None,
                          "previous_cluster_id": row["cluster_id"]})

    def order(item: dict):
        rank = STATUS_ORDER.index(item["status"])
        key = {"new": -(item["score"] or 0), "rising": -(item["delta"] or 0), "falling": item["delta"] or 0,
               "gone": -(item["previous_score"] or 0)}[item["status"]]
        return rank, key, item["cluster_id"]

    items.sort(key=order)
    return {"items": items, "status_by_cluster": status_by_cluster}


def compute_trends(store, cfg: Mapping | None = None) -> dict:
    """Latest snapshot run vs the newest run at least `compare_days` older (else the previous run).

    Returns {run_id, run_at, previous_run_id, previous_run_at,
             items: [{cluster_id, title, status, score, previous_score, delta, previous_cluster_id}],
             status_by_cluster: {cluster_id: new|rising|falling|steady}}
    `items` lists only new / rising / falling / gone. With fewer than two runs there is nothing to compare:
    items and status_by_cluster are empty.
    """
    tc = trend_config(cfg)
    result = {"run_id": None, "run_at": None, "previous_run_id": None, "previous_run_at": None,
              "items": [], "status_by_cluster": {}}
    runs = store.snapshot_runs()
    if not runs:
        return result
    result.update(run_id=runs[0]["run_id"], run_at=runs[0]["run_at"])
    baseline = pick_baseline(runs, int(tc["compare_days"]))
    if baseline is None:
        return result
    result.update(previous_run_id=baseline["run_id"], previous_run_at=baseline["run_at"])
    result.update(diff_snapshots(store.snapshot(baseline["run_id"]), store.snapshot(runs[0]["run_id"]), tc))
    return result


def format_trends(result: dict) -> str:
    """Plain-text rendering for `paindisc trends`."""
    if not result.get("run_at"):
        return "No clustering runs recorded yet. Run `paindisc cluster` (or `paindisc run`) first."
    if not result.get("previous_run_at"):
        return f"Only one clustering run so far ({result['run_at'][:10]}); trends need two."
    lines = [f"Trends: {result['run_at'][:16].replace('T', ' ')} UTC vs "
             f"{result['previous_run_at'][:16].replace('T', ' ')} UTC"]
    for item in result["items"]:
        if item["status"] == "gone":
            figure = f"(was {item['previous_score']:.2f})"
        elif item["status"] == "new":
            figure = f"{item['score']:.2f}"
        else:
            figure = f"{item['score']:.2f} ({item['delta']:+.2f})"
        lines.append(f"  {item['status'].upper():<8} {figure:<14} {item['title']}")
    steady = sum(1 for s in result["status_by_cluster"].values() if s == "steady")
    if not result["items"]:
        lines.append("  no new, rising, falling or gone clusters")
    lines.append(f"  {steady} steady cluster(s)")
    return "\n".join(lines)
