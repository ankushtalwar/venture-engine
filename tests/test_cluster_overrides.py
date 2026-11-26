"""Human merges and splits (review UI) survive automatic re-clustering via cluster.apply_overrides."""

import pytest
from conftest import make_pair

from paindisc.cluster import apply_overrides, make_cluster, merge_clusters, split_cluster
from paindisc.models import Synthesis


def pairs_for(*authors, spend=()):
    return {a: make_pair(a, spend=a in spend) for a in authors}


def cluster_of(pairs, *authors):
    return make_cluster([pairs[a] for a in authors])


def sid(pairs, author):
    return pairs[author][0].id


def memberships(clusters):
    return sorted(sorted(c.signal_ids) for c in clusters)


def ids(pairs, *authors):
    return sorted(sid(pairs, a) for a in authors)


def test_merge_clusters_rescored_without_synthesis():
    p = pairs_for("a1", "a2", "b1", spend=("b1",))
    a, b = cluster_of(p, "a1", "a2"), cluster_of(p, "b1")
    a.synthesis = Synthesis(title="t", summary="s", hypotheses=[])
    merged = merge_clusters([a, b], list(p.values()))
    assert merged.signal_ids == ids(p, "a1", "a2", "b1")
    assert merged.n_signals == 3 and merged.n_authors == 3
    assert merged.synthesis is None and merged.skeptic is None
    assert merged.id not in (a.id, b.id)
    assert merged.components["spend"] > 0  # rescored from the members


def test_merge_clusters_ignores_missing_signals():
    p = pairs_for("a1", "b1")
    a, b = cluster_of(p, "a1"), cluster_of(p, "b1")
    merged = merge_clusters([a, b], [p["a1"]])
    assert merged.signal_ids == ids(p, "a1")
    with pytest.raises(ValueError):
        merge_clusters([a, b], [])


def test_split_cluster():
    p = pairs_for("a1", "a2", "a3")
    c = cluster_of(p, "a1", "a2", "a3")
    moved, rest = split_cluster(c, [sid(p, "a3")], list(p.values()))
    assert moved.signal_ids == ids(p, "a3")
    assert rest.signal_ids == ids(p, "a1", "a2")
    assert moved.synthesis is None and rest.synthesis is None
    with pytest.raises(ValueError, match="not in cluster"):
        split_cluster(c, ["nope"], list(p.values()))
    with pytest.raises(ValueError, match="at least one"):
        split_cluster(c, ids(p, "a1", "a2", "a3"), list(p.values()))


def test_no_overrides_keeps_clusters():
    p = pairs_for("a1", "a2", "b1")
    clusters = [cluster_of(p, "a1", "a2"), cluster_of(p, "b1")]
    out = apply_overrides(clusters, [], list(p.values()))
    assert {id(c) for c in out} == {id(c) for c in clusters}
    assert [c.score for c in out] == sorted((c.score for c in out), reverse=True)


def test_merge_override_reapplied_after_reclustering():
    p = pairs_for("a1", "a2", "b1", "c1", "n1")
    # earlier run: {a1,a2} and {b1}; the reviewer merged them
    override = {"kind": "merge", "payload": {"signal_ids": ids(p, "a1", "a2", "b1")}}
    # next run: a new signal n1 joined a1/a2; b1 is on its own again
    fresh = [cluster_of(p, "a1", "a2", "n1"), cluster_of(p, "b1"), cluster_of(p, "c1")]
    untouched = fresh[2]
    out = apply_overrides(fresh, [override], list(p.values()))
    assert memberships(out) == sorted([ids(p, "a1", "a2", "b1", "n1"), ids(p, "c1")])
    assert any(c is untouched for c in out)  # clusters the override didn't touch come back as they were
    merged = next(c for c in out if c.n_signals == 4)
    assert merged.synthesis is None


def test_split_override_reapplied_after_reclustering():
    p = pairs_for("a1", "a2", "a3", "n1")
    override = {"kind": "split", "payload": {"signal_ids": ids(p, "a3")}}
    fresh = [cluster_of(p, "a1", "a2", "a3", "n1")]
    out = apply_overrides(fresh, [override], list(p.values()))
    assert memberships(out) == sorted([ids(p, "a1", "a2", "n1"), ids(p, "a3")])


def test_split_override_pulls_signals_out_of_several_clusters():
    p = pairs_for("a1", "a2", "b1", "b2")
    override = {"kind": "split", "payload": {"signal_ids": ids(p, "a2", "b2")}}
    fresh = [cluster_of(p, "a1", "a2"), cluster_of(p, "b1", "b2")]
    out = apply_overrides(fresh, [override], list(p.values()))
    assert memberships(out) == sorted([ids(p, "a1"), ids(p, "b1"), ids(p, "a2", "b2")])


def test_overrides_apply_in_order():
    p = pairs_for("a1", "a2", "b1")
    merge = {"kind": "merge", "payload": {"signal_ids": ids(p, "a1", "a2", "b1")}}
    split = {"kind": "split", "payload": {"signal_ids": ids(p, "a2")}}
    fresh = [cluster_of(p, "a1", "a2"), cluster_of(p, "b1")]
    merge_then_split = apply_overrides(fresh, [merge, split], list(p.values()))
    assert memberships(merge_then_split) == sorted([ids(p, "a1", "b1"), ids(p, "a2")])
    split_then_merge = apply_overrides(fresh, [split, merge], list(p.values()))
    assert memberships(split_then_merge) == [ids(p, "a1", "a2", "b1")]


def test_missing_signals_and_unknown_kinds_are_ignored():
    p = pairs_for("a1", "b1")
    fresh = [cluster_of(p, "a1"), cluster_of(p, "b1")]
    overrides = [
        {"kind": "merge", "payload": {"signal_ids": ["gone-1", sid(p, "a1")]}},  # only one live signal: no-op
        {"kind": "split", "payload": {"signal_ids": ["gone-2"]}},
        {"kind": "rename", "payload": {"signal_ids": ids(p, "a1", "b1")}},
        {"kind": "merge", "payload": {}},
    ]
    out = apply_overrides(fresh, overrides, list(p.values()))
    assert {id(c) for c in out} == {id(c) for c in fresh}


def test_merge_override_skips_signals_that_were_deleted():
    p = pairs_for("a1", "b1", "c1")
    override = {"kind": "merge", "payload": {"signal_ids": ids(p, "a1", "b1", "c1")}}
    live = [p["a1"], p["b1"]]  # c1's document is gone
    fresh = [make_cluster([p["a1"]]), make_cluster([p["b1"]])]
    out = apply_overrides(fresh, [override], live)
    assert memberships(out) == [ids(p, "a1", "b1")]


def test_applying_overrides_is_idempotent():
    p = pairs_for("a1", "a2", "b1", "c1")
    overrides = [{"kind": "merge", "payload": {"signal_ids": ids(p, "a1", "b1")}},
                 {"kind": "split", "payload": {"signal_ids": ids(p, "a2")}}]
    fresh = [cluster_of(p, "a1", "a2"), cluster_of(p, "b1"), cluster_of(p, "c1")]
    once = apply_overrides(fresh, overrides, list(p.values()))
    twice = apply_overrides(once, overrides, list(p.values()))
    assert memberships(once) == memberships(twice)
    assert {c.id for c in once} == {c.id for c in twice}
