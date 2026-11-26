from conftest import make_pair

from paindisc.planner import Arm, build_arms, choose
from paindisc.score import freq_bucket, score_members


def test_build_arms_from_config(settings):
    arms = build_arms(settings.load_yaml("phrases.yaml"), settings.load_yaml("segments.yaml"))
    assert {"hn", "github", "brave"} <= set(arms)
    assert len({a.arm_id for a in arms["hn"]}) == len(arms["hn"])
    # issue-tracker queries use the job vocabulary, not forum phrases
    assert all(a.phrase == "" for a in arms["github"])


def test_choose_explores_first_then_exploits_winners():
    arms = [Arm(str(i), "hn", "f", "p", "s", "t", f"q{i}") for i in range(20)]
    assert len(choose(arms, {}, budget=5, seed=1)) == 5
    stats = {a.arm_id: {"runs": 3, "docs": 30, "verified": 0, "empty_runs": 0} for a in arms}
    stats["7"] = {"runs": 3, "docs": 30, "verified": 25, "empty_runs": 0}
    picks = {a.arm_id for a in choose(arms, stats, budget=5, explore=0.2, seed=1)}
    assert "7" in picks


def test_confidence_rules():
    high = [make_pair(f"a{i}", "forum" if i % 2 else "job_post", spend=(i == 0)) for i in range(5)]
    assert score_members(high)["confidence"] == "High"

    single_author = [make_pair("solo", spend=True), make_pair("solo", "issue_tracker", spend=True)]
    assert score_members(single_author)["confidence"] == "Low"

    three_authors = [make_pair(f"m{i}") for i in range(3)]
    assert score_members(three_authors)["confidence"] == "Medium"

    stale = [make_pair(f"s{i}", "forum" if i % 2 else "job_post", days_ago=800, spend=True) for i in range(5)]
    assert score_members(stale)["confidence"] == "Medium"  # old evidence can't be High


def test_frequency_buckets():
    assert freq_bucket("every Friday") == "weekly"
    assert freq_bucket("at month-end") == "monthly"
    assert freq_bucket("daily") == "daily"
    assert freq_bucket("when a tenant moves out") == "per-event"
    assert freq_bucket("") is None
