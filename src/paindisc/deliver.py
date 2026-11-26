"""Deliver the weekly digest (top pain cards, trend alerts, report path) to Slack.

The post goes through `PoliteClient` with `source="slack"`, so the registry decides whether it may leave
the machine: the `slack` entry must be `allowed: true` and the webhook host must be `hooks.slack.com`.
When the webhook isn't set or the registry blocks it, delivery is skipped with a reason; it never raises
for that, so a scheduled run still finishes.

Email is deliberately not implemented: SMTP would open a network path outside `PoliteClient` and the
registry (CLAUDE.md non-negotiable). Use a Slack channel's email integration if you need mail.
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import httpx

from .config import Settings
from .http import PoliteClient
from .registry import Registry, RegistryError

SOURCE = "slack"
TITLE_CHARS = 90
ALERT_LIMIT = 8


def _slack_escape(text: str) -> str:
    """Fetched content is untrusted: escape Slack control characters so no title can ping or link."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _one_line(text: str, limit: int = TITLE_CHARS) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    # An asterisk or backtick inside a title would break the surrounding *bold*.
    text = text.replace("*", "").replace("`", "'")
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return _slack_escape(text)


def _redact(message: str, secret: str | None) -> str:
    """Webhook URLs are credentials: never echo one into logs or results."""
    if secret:
        message = message.replace(secret, "<webhook>")
    return re.sub(r"https://hooks\.slack\.com/\S+", "<webhook>", message)


def _date(value) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    return str(value)[:10] if value else ""


def latest_report(report_dir: Path) -> Path | None:
    """Newest dated weekly report (pain-report-*.md), or None."""
    if not report_dir.is_dir():
        return None
    reports = sorted(report_dir.glob("pain-report-*.md"), key=lambda p: (p.stat().st_mtime, p.name))
    return reports[-1] if reports else None


def load_trends(settings: Settings, store) -> dict | None:
    """Trend diff from `paindisc.trends`, or None if that module isn't available."""
    try:
        from .trends import compute_trends
    except ImportError:
        return None
    return compute_trends(store, settings.load_yaml("scoring.yaml").get("trends") or {})


def _trend_lines(trends: dict | None) -> tuple[list[str], int]:
    if not trends:
        return [], 0
    items = list(trends.get("items") or [])
    flagged = [i for i in items if i.get("status") in ("new", "rising")]
    flagged.sort(key=lambda i: (i.get("status") != "new", -(i.get("delta") or 0), -(i.get("score") or 0)))
    lines: list[str] = []
    for item in flagged[:ALERT_LIMIT]:
        title = _one_line(item.get("title") or item.get("cluster_id") or "untitled")
        score = item.get("score") or 0.0
        if item.get("status") == "new":
            lines.append(f"• NEW *{title}* ({score:.2f})")
        else:
            prev = item.get("previous_score") or 0.0
            lines.append(f"• RISING *{title}* {prev:.2f} → {score:.2f} ({item.get('delta') or score - prev:+.2f})")
    if len(flagged) > ALERT_LIMIT:
        lines.append(f"• …and {len(flagged) - ALERT_LIMIT} more new or rising")
    falling = sum(1 for i in items if i.get("status") == "falling")
    gone = sum(1 for i in items if i.get("status") == "gone")
    if falling or gone:
        lines.append(f"• {falling} falling, {gone} gone")
    return lines, len(flagged)


def build_message(settings: Settings, store, report_path: Path | None, top: int = 10,
                  trends: dict | None = None) -> tuple[str, int, int]:
    """Compact Slack mrkdwn text. Returns (text, n_cards, n_alerts)."""
    rejected = {cid for cid, label in store.labels("cluster").items() if label == "reject"}
    clusters = [c for c in store.clusters() if c.id not in rejected][:top]

    today = datetime.now(timezone.utc).date().isoformat()
    header = f"*Pain Report: weekly digest* ({today})"
    if settings.llm_mock:
        header += " [mock LLM, not real analysis]"
    lines = [header]

    if clusters:
        lines.append(f"Top {len(clusters)} pain cards (computed confidence, score):")
        for n, c in enumerate(clusters, 1):
            title = _one_line(c.synthesis.title if c.synthesis else c.canonical)
            lines.append(f"{n}. *{title}* ({c.confidence}, {c.score:.2f}; "
                         f"{c.n_signals} signals, {c.n_authors} authors)")
    else:
        lines.append("No pain cards yet.")

    alert_lines, n_alerts = _trend_lines(trends)
    if trends is not None:
        since = _date(trends.get("previous_run_at"))
        lines.append(f"Trend alerts{f' (vs {since})' if since else ''}:" if alert_lines
                     else f"Trend alerts: none{f' (vs {since})' if since else ''}.")
        lines.extend(alert_lines)

    lines.append(f"Report: `{_slack_escape(str(report_path))}`" if report_path else "Report: none written yet.")
    lines.append("_Titles are model-written summaries; the verified quotes are in the report. Problems, not products._")
    return "\n".join(lines), len(clusters), n_alerts


def _result(status: str, reason: str, text: str = "", n_cards: int = 0, n_alerts: int = 0,
            report: Path | None = None) -> dict:
    return {"status": status, "reason": reason, "summary": f"{status}: {reason}", "text": text,
            "n_cards": n_cards, "n_alerts": n_alerts, "report": str(report) if report else None}


def deliver(settings: Settings, store, report_path: Path | str | None = None, top: int = 10, *,
            trends: dict | None = None, transport: httpx.BaseTransport | None = None,
            sleep: Callable[[float], None] = time.sleep) -> dict:
    """Post the digest to `settings.slack_webhook_url`. Returns {status: sent|skipped|failed, reason, summary, ...}.

    `trends` defaults to `paindisc.trends.compute_trends(store, scoring.yaml trends block)`.
    """
    url = settings.slack_webhook_url
    if not url:
        return _result("skipped", "SLACK_WEBHOOK_URL is not set")

    registry = Registry.load(settings.config_dir)
    try:
        registry.guard(url, SOURCE)
    except RegistryError as exc:
        return _result("skipped", f"registry refused Slack delivery: {_redact(str(exc), url)}")

    report = Path(report_path) if report_path else latest_report(settings.report_dir)
    if trends is None:
        trends = load_trends(settings, store)
    text, n_cards, n_alerts = build_message(settings, store, report, top=top, trends=trends)

    client = PoliteClient(settings, registry, transport=transport, sleep=sleep)
    try:
        client.post(url, source=SOURCE, json={"text": text})
    except httpx.HTTPStatusError as exc:
        return _result("failed", f"Slack returned HTTP {exc.response.status_code}", text, n_cards, n_alerts, report)
    except (httpx.HTTPError, RegistryError) as exc:
        return _result("failed", _redact(f"{type(exc).__name__}: {exc}", url), text, n_cards, n_alerts, report)
    finally:
        client.close()
    return _result("sent", f"{n_cards} cards, {n_alerts} trend alerts", text, n_cards, n_alerts, report)
