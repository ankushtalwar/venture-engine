"""Trend alerts: cluster matching across runs and the snapshot diff (SPEC backlog #8)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from test_store_support import make_cluster

from paindisc.store import Store
from paindisc.trends import (compute_trends, diff_snapshots, format_trends, jaccard, match_clusters, pick_baseline,
                             trend_config)

NOW = datetime(2026, 11, 23, 6, 0, 5, tzinfo=timezone.utc)  # a Monday


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "trends.sqlite")
    yield s
    s.close()


# ------------------------------------------------------------------ match_clusters


def test_unchanged_clusters_match_themselves():
    clusters = [make_cluster("a", ["1", "2"], 0.5), make_cluster("b", ["3"], 0.4)]
    assert match_clusters(clusters, clusters) == {"a": "a", "b": "b"}


def test_changed_membership_matches_by_jaccard_threshold():
    old = {"A": ["1", "2", "3", "4"], "Z": ["7", "8"]}
    new = {"A2": ["1", "2", "3", "5"], "Q": ["1", "9", "10", "11"]}
    assert match_clusters(old, new, 0.5) == {"A2": "A"}  # 3/5 = 0.6; Q shares one signal of seven
    assert match_clusters(old, new, 0.7) == {}
    assert match_clusters({"A": ["1", "2"]}, {"B": ["1", "2", "3", "4"]}, 0.5) == {"B": "A"}  # exactly 0.5 counts


def test_matching_is_one_to_one_best_first():
    old = {"A": ["1", "2", "3", "4"]}
    new = {"X": ["1", "2", "3"], "Y": ["1", "2", "3", "4", "5"]}  # Y: 0.8, X: 0.75
    assert match_clusters(old, new, 0.5) == {"Y": "A"}

    split = match_clusters({"A": ["1", "2", "3", "4", "5", "6"]}, {"C": ["4", "5", "6"], "B": ["1", "2", "3"]}, 0.5)
    assert split == {"B": "A"}  # equal overlap: deterministic by id; C counts as new

    merged = match_clusters({"B": ["3", "4"], "A": ["1", "2"]}, {"M": ["1", "2", "3", "4"]}, 0.5)
    assert merged == {"M": "A"}  # B is gone


def test_match_accepts_clusters_snapshot_rows_and_mappings():
    old_rows = [{"cluster_id": "a", "signal_ids": ["1", "2"]}]
    new_clusters = [make_cluster("b", ["1", "2", "3"], 0.5)]
    assert match_clusters(old_rows, new_clusters, 0.5) == {"b": "a"}
    assert match_clusters({"a": ["1"]}, [{"id": "c", "signal_ids": ["1"]}]) == {"c": "a"}
    assert match_clusters([], new_clusters) == {} and match_clusters(old_rows, []) == {}
    assert match_clusters({"a": []}, {"b": []}, 0.0) == {}  # nothing shared, nothing matched
    assert jaccard(["1", "2"], ["2", "3"]) == pytest.approx(1 / 3) and jaccard([], []) == 0.0


# ------------------------------------------------------------------ configuration and baseline choice


def test_trend_config_reads_whole_file_or_block():
    assert trend_config(None) == {"match_jaccard": 0.5, "rising_delta": 0.05, "falling_delta": 0.05,
                                  "compare_days": 7}
    assert trend_config({"weights": {}, "trends": {"rising_delta": 0.1}})["rising_delta"] == 0.1
    assert trend_config({"compare_days": 14})["compare_days"] == 14
    assert trend_config({"trends": None})["match_jaccard"] == 0.5


def _run(run_id: str, at: datetime) -> dict:
    return {"run_id": run_id, "run_at": at.isoformat(), "n_clusters": 1}


def test_baseline_is_newest_run_at_least_compare_days_older():
    runs = [_run("now", NOW), _run("3d", NOW - timedelta(days=3)),
            _run("week", NOW - timedelta(days=7) + timedelta(seconds=5)),  # 6d 23:59:55 earlier, same weekday
            _run("2w", NOW - timedelta(days=14))]
    assert pick_baseline(runs, 7)["run_id"] == "week"  # calendar days, so a drifting weekly job still pairs up
    assert pick_baseline(runs, 10)["run_id"] == "2w"
    assert pick_baseline(runs, 30)["run_id"] == "3d"  # nothing that old: the previous run
    assert pick_baseline(runs[:1], 7) is None and pick_baseline([], 7) is None


# ------------------------------------------------------------------ compute_trends over stored snapshots


def test_compute_trends_flags_new_rising_falling_gone(store):
    old = [make_cluster("A", ["a1", "a2", "a3", "a4"], 0.50, title="Invoice re-keying"),
           make_cluster("B", ["b1", "b2"], 0.40), make_cluster("C", ["c1"], 0.30, title="Gone problem"),
           make_cluster("D", ["d1", "d2"], 0.60)]
    store.replace_clusters(old, run_at=NOW - timedelta(days=10))
    store.replace_clusters([make_cluster("X", ["a1"], 0.9)], run_at=NOW - timedelta(days=3))  # too recent
    new = [make_cluster("A2", ["a1", "a2", "a3", "a5"], 0.58, title="Invoice re-keying v2"),
           make_cluster("B", ["b1", "b2"], 0.35), make_cluster("D", ["d1", "d2"], 0.62),
           make_cluster("E", ["e1"], 0.45, title="Brand new")]
    latest = store.replace_clusters(new, run_at=NOW)

    result = compute_trends(store, {"trends": {"compare_days": 7}})
    assert result["run_id"] == latest
    assert datetime.fromisoformat(result["run_at"]) == NOW
    assert datetime.fromisoformat(result["previous_run_at"]) == NOW - timedelta(days=10)
    assert [(i["status"], i["cluster_id"]) for i in result["items"]] == [
        ("new", "E"), ("rising", "A2"), ("falling", "B"), ("gone", "C")]
    by_id = {i["cluster_id"]: i for i in result["items"]}
    assert by_id["A2"] == {"cluster_id": "A2", "title": "Invoice re-keying v2", "status": "rising", "score": 0.58,
                           "previous_score": 0.5, "delta": 0.08, "previous_cluster_id": "A"}
    assert by_id["B"]["delta"] == -0.05  # exactly the threshold counts despite float error
    assert by_id["E"]["previous_score"] is None and by_id["E"]["delta"] is None
    assert by_id["C"] == {"cluster_id": "C", "title": "Gone problem", "status": "gone", "score": None,
                          "previous_score": 0.3, "delta": None, "previous_cluster_id": "C"}
    assert result["status_by_cluster"] == {"D": "steady", "A2": "rising", "E": "new", "B": "falling"}

    text = format_trends(result)
    assert "RISING" in text and "Invoice re-keying v2" in text and "(was 0.30)" in text and "1 steady" in text


def test_compute_trends_falls_back_to_previous_run(store):
    store.replace_clusters([make_cluster("A", ["1"], 0.5)], run_at=NOW - timedelta(days=2))
    store.replace_clusters([make_cluster("A", ["1"], 0.52)], run_at=NOW)
    result = compute_trends(store, {"compare_days": 7})
    assert datetime.fromisoformat(result["previous_run_at"]) == NOW - timedelta(days=2)
    assert result["items"] == [] and result["status_by_cluster"] == {"A": "steady"}
    assert "no new, rising, falling or gone clusters" in format_trends(result)


def test_compute_trends_needs_two_runs(store):
    empty = compute_trends(store, None)
    assert empty == {"run_id": None, "run_at": None, "previous_run_id": None, "previous_run_at": None,
                     "items": [], "status_by_cluster": {}}
    assert "No clustering runs" in format_trends(empty)
    store.replace_clusters([make_cluster("A", ["1"], 0.5)], run_at=NOW)
    single = compute_trends(store, None)
    assert single["run_at"] and single["previous_run_at"] is None and single["items"] == []
    assert "Only one clustering run" in format_trends(single)


def test_diff_uses_custom_thresholds():
    previous = [{"cluster_id": "A", "title": "a", "score": 0.5, "signal_ids": ["1", "2"]}]
    current = [{"cluster_id": "A", "title": "a", "score": 0.53, "signal_ids": ["1", "2"]}]
    assert diff_snapshots(previous, current, {"rising_delta": 0.02})["status_by_cluster"] == {"A": "rising"}
    assert diff_snapshots(previous, current)["status_by_cluster"] == {"A": "steady"}


# ------------------------------------------------------------------ label carry-forward recipe (pipeline.cluster)


def test_labels_follow_matched_clusters(store):
    """The recipe Pipeline.cluster uses: labels move from an old cluster id to the id that continues it."""
    old = [make_cluster("A", ["1", "2", "3"], 0.5), make_cluster("B", ["7", "8"], 0.4)]
    store.replace_clusters(old)
    store.set_label("cluster", "A", "accept", "real pain")
    store.set_label("cluster", "B", "reject")
    new = [make_cluster("A2", ["1", "2", "3", "4"], 0.55), make_cluster("Z", ["9"], 0.2)]
    store.replace_clusters(new)
    for new_id, old_id in match_clusters(old, new, 0.5).items():
        store.move_label("cluster", old_id, new_id)
    assert store.get_label("cluster", "A2")["note"] == "real pain"
    assert store.labels("cluster") == {"A2": "accept", "B": "reject"}  # B is gone; its label stays as history
