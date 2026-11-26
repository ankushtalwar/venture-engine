"""Slack delivery: registry-gated, skips quietly when off, posts the expected digest when allowed."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import httpx
import pytest
import yaml

from paindisc.deliver import build_message, deliver, latest_report
from paindisc.models import Cluster, Synthesis
from paindisc.store import Store

WEBHOOK = "https://hooks.slack.com/services/T000/B000/not-a-real-secret"


def _allow_slack(config_dir):
    path = config_dir / "registry.yaml"
    raw = yaml.safe_load(path.read_text())
    raw["sources"]["slack"]["allowed"] = True
    path.write_text(yaml.safe_dump(raw))


def _cluster(cid: str, score: float, title: str | None = None, confidence: str = "Medium") -> Cluster:
    now = datetime.now(timezone.utc)
    return Cluster(
        id=cid, canonical=f"firms must do {cid} by hand", signal_ids=[f"s-{cid}-1", f"s-{cid}-2"], n_signals=2,
        n_authors=2, source_types=["forum"], first_seen=now, last_seen=now, score=score, confidence=confidence,
        synthesis=Synthesis(title=title, summary="s", hypotheses=["Hypothesis: x"]) if title else None,
    )


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "deliver.sqlite")
    s.save_cluster(_cluster("c1", 0.71, "Re-keying supplier invoices", "High"))
    s.save_cluster(_cluster("c2", 0.52))  # no synthesis: falls back to the canonical statement
    s.save_cluster(_cluster("c3", 0.40, "Rejected card"))
    s.set_label("cluster", "c3", "reject")
    return s


@pytest.fixture
def trends():
    return {
        "run_at": "2026-11-30T06:00:00+00:00", "previous_run_at": "2026-11-23T06:00:00+00:00",
        "items": [
            {"cluster_id": "c1", "title": "Re-keying supplier invoices", "status": "rising", "score": 0.71,
             "previous_score": 0.55, "delta": 0.16},
            {"cluster_id": "c2", "title": "Chasing late timesheets", "status": "new", "score": 0.52,
             "previous_score": None, "delta": None},
            {"cluster_id": "old", "title": "Gone one", "status": "gone", "score": 0.0, "previous_score": 0.3,
             "delta": -0.3},
            {"cluster_id": "c9", "title": "Steady one", "status": "falling", "score": 0.2, "previous_score": 0.3,
             "delta": -0.1},
        ],
    }


def _recorder(calls: list):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, text="ok")
    return handler


def test_unset_webhook_skips_without_network(settings, store, config_copy):
    _allow_slack(config_copy)
    settings.config_dir = config_copy
    settings.slack_webhook_url = None
    calls: list = []
    result = deliver(settings, store, transport=httpx.MockTransport(_recorder(calls)), trends={})
    assert result["status"] == "skipped"
    assert result["summary"] == "skipped: SLACK_WEBHOOK_URL is not set"
    assert calls == []


def test_blocked_registry_refuses(settings, store, config_copy):
    settings.config_dir = config_copy  # shipped registry: slack allowed: false
    settings.slack_webhook_url = WEBHOOK
    calls: list = []
    result = deliver(settings, store, transport=httpx.MockTransport(_recorder(calls)), trends={})
    assert result["status"] == "skipped"
    assert "not permitted" in result["reason"]
    assert "not-a-real-secret" not in result["summary"]
    assert calls == []


def test_unregistered_host_refuses(settings, store, config_copy):
    _allow_slack(config_copy)
    settings.config_dir = config_copy
    settings.slack_webhook_url = "https://evil.example.com/hook"
    calls: list = []
    result = deliver(settings, store, transport=httpx.MockTransport(_recorder(calls)), trends={})
    assert result["status"] == "skipped"
    assert "not registered" in result["reason"]
    assert calls == []


def test_allowed_posts_expected_json(settings, store, config_copy, trends):
    _allow_slack(config_copy)
    settings.config_dir = config_copy
    settings.slack_webhook_url = WEBHOOK
    settings.report_dir.mkdir(parents=True)
    report = settings.report_dir / "pain-report-2026-11-30.md"
    report.write_text("# report")
    calls: list = []

    result = deliver(settings, store, top=10, trends=trends, transport=httpx.MockTransport(_recorder(calls)),
                     sleep=lambda _: None)

    assert result["status"] == "sent", result
    assert result["n_cards"] == 2 and result["n_alerts"] == 2
    assert result["report"] == str(report)
    assert len(calls) == 1
    request = calls[0]
    assert request.method == "POST"
    assert str(request.url) == WEBHOOK
    payload = json.loads(request.content)
    assert set(payload) == {"text"}
    text = payload["text"]
    assert text == result["text"]
    lines = text.splitlines()
    assert lines[0].startswith("*Pain Report: weekly digest*") and "mock LLM" in lines[0]
    assert "1. *Re-keying supplier invoices* (High, 0.71; 2 signals, 2 authors)" in lines
    assert "2. *firms must do c2 by hand* (Medium, 0.52; 2 signals, 2 authors)" in lines
    assert "Rejected card" not in text  # reviewer-rejected cards are left out
    assert "Trend alerts (vs 2026-11-23):" in lines
    assert "• NEW *Chasing late timesheets* (0.52)" in lines
    assert "• RISING *Re-keying supplier invoices* 0.55 → 0.71 (+0.16)" in lines
    assert "• 1 falling, 1 gone" in lines
    assert f"Report: `{report}`" in lines


def test_titles_are_escaped(settings, tmp_path):
    s = Store(tmp_path / "esc.sqlite")
    s.save_cluster(_cluster("x", 0.5, "<!channel> *click* <https://evil.example|here> & more"))
    text, n, _ = build_message(settings, s, None, trends=None)
    assert n == 1
    assert "<!channel>" not in text and "<https://" not in text
    assert "&lt;!channel&gt; click &lt;https://evil.example|here&gt; &amp; more" in text
    assert "Trend alerts" not in text  # no trend data: no section
    assert "Report: none written yet." in text


def test_http_error_fails_without_leaking_webhook(settings, store, config_copy):
    _allow_slack(config_copy)
    settings.config_dir = config_copy
    settings.slack_webhook_url = WEBHOOK
    result = deliver(settings, store, trends={},
                     transport=httpx.MockTransport(lambda r: httpx.Response(403, text="invalid_token")),
                     sleep=lambda _: None)
    assert result["status"] == "failed"
    assert result["reason"] == "Slack returned HTTP 403"
    assert "not-a-real-secret" not in json.dumps(result)


def test_redirect_off_slack_is_refused(settings, store, config_copy):
    _allow_slack(config_copy)
    settings.config_dir = config_copy
    settings.slack_webhook_url = WEBHOOK
    calls: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(302, headers={"location": "https://evil.example.com/steal"})

    result = deliver(settings, store, trends={}, transport=httpx.MockTransport(handler), sleep=lambda _: None)
    assert result["status"] == "failed"
    assert "not registered" in result["reason"]
    assert len(calls) == 1  # the redirect target was never contacted


def test_latest_report_picks_newest(tmp_path):
    assert latest_report(tmp_path / "missing") is None
    old, new = tmp_path / "pain-report-2026-11-23.md", tmp_path / "pain-report-2026-11-30.md"
    old.write_text("a")
    new.write_text("b")
    (tmp_path / "demo-pain-report.md").write_text("c")
    os.utime(old, (1_000, 1_000))
    os.utime(new, (2_000, 2_000))
    assert latest_report(tmp_path) == new


def test_real_trends_flow_into_the_digest(settings, tmp_path, config_copy):
    """No injected trends: deliver computes them with paindisc.trends from two snapshot runs a week apart."""
    from datetime import timedelta

    _allow_slack(config_copy)
    settings.config_dir = config_copy
    settings.slack_webhook_url = WEBHOOK
    s = Store(tmp_path / "trends.sqlite")
    week_ago = datetime.now(timezone.utc) - timedelta(days=8)
    s.replace_clusters([_cluster("old", 0.30, "Steady-ish problem"), _cluster("rise", 0.20, "Rising problem")],
                       run_at=week_ago)
    s.replace_clusters([_cluster("old", 0.30, "Steady-ish problem"), _cluster("rise", 0.60, "Rising problem"),
                        _cluster("fresh", 0.50, "Brand new problem")])
    calls: list = []

    result = deliver(settings, s, transport=httpx.MockTransport(_recorder(calls)), sleep=lambda _: None)

    assert result["status"] == "sent", result
    text = json.loads(calls[0].content)["text"]
    assert f"Trend alerts (vs {week_ago.date().isoformat()}):" in text
    assert "• NEW *Brand new problem* (0.50)" in text
    assert "• RISING *Rising problem* 0.20 → 0.60 (+0.40)" in text
    assert "Steady-ish problem* 0.30" not in text  # steady clusters are not alerts
    assert result["n_alerts"] == 2
