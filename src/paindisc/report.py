"""Weekly Pain Report (Markdown + HTML). Every quote links to its source; inferred values are labelled."""

from __future__ import annotations

import html
import re
from collections import Counter
from urllib.parse import quote, urlparse
from datetime import datetime, timezone
from pathlib import Path

from .models import Cluster, Document, PainSignal, PriceExtraction, Status
from .score import freq_bucket
from .store import cluster_title

Members = list[tuple[PainSignal, Document]]


def _date(dt: datetime | None) -> str:
    return dt.date().isoformat() if dt else "undated"


def _ref(doc: Document) -> dict:
    return {"url": doc.url, "label": f"{doc.source} · {_date(doc.published_at)}"}


def _clean(text: str, limit: int = 280) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _top_values(members: Members, field: str, n: int = 3) -> list[str]:
    counts: Counter = Counter()
    statuses: dict[str, set] = {}
    for signal, _ in members:
        ev = getattr(signal, field)
        if ev.known:
            key = " ".join(ev.value.lower().split())
            counts[key] += 1
            statuses.setdefault(key, set()).add(ev.status.value)
    return [f"{value} ({'/'.join(sorted(statuses[value]))}, {k}x)" for value, k in counts.most_common(n)]


def _quotes(members: Members, field: str, n: int = 3) -> list[dict]:
    seen, out = set(), []
    for signal, doc in members:
        ev = getattr(signal, field)
        if ev.status == Status.observed and ev.quote:
            key = ev.quote.lower()
            if key not in seen:
                seen.add(key)
                out.append({"quote": _clean(ev.quote), **_ref(doc)})
    return out[:n]


def _spend(members: Members, n: int = 6) -> list[dict]:
    out = []
    for signal, doc in members:
        for spend in signal.spend_evidence:
            out.append({"type": spend.type, "amount": spend.amount, "quote": _clean(spend.quote), **_ref(doc)})
    return out[:n]


def _financial(members: Members) -> list[dict]:
    out = []
    for signal, doc in members:
        fi = signal.financial_impact
        if fi.status == Status.observed:
            out.append({"value": fi.value, "status": "observed", "quote": _clean(fi.quote), "basis": "", **_ref(doc)})
        elif fi.status == Status.derived:
            out.append({"value": fi.value, "status": "derived", "quote": _clean(fi.quote), "basis": fi.basis,
                        **_ref(doc)})
    return out


def _frequency(members: Members) -> str:
    buckets = Counter(freq_bucket(s.occurrence_freq.value) for s, _ in members
                      if s.occurrence_freq.status == Status.observed)
    return ", ".join(f"{b} ({k}x)" for b, k in buckets.most_common() if b) or "not stated"


def latest_prices(prices: list[tuple[PriceExtraction, Document]]) -> dict[str, tuple[PriceExtraction, Document]]:
    latest: dict[str, tuple[PriceExtraction, Document]] = {}
    for extraction, doc in prices:
        vendor = doc.metrics.get("vendor")
        if not vendor:
            continue
        current = latest.get(vendor)
        if current is None or (doc.published_at or datetime.min.replace(tzinfo=timezone.utc)) > \
                (current[1].published_at or datetime.min.replace(tzinfo=timezone.utc)):
            latest[vendor] = (extraction, doc)
    return latest


def _tier_summary(extraction: PriceExtraction) -> str:
    if extraction.tiers:
        return "; ".join(f"{t.name} {t.price} {t.unit}".strip() for t in extraction.tiers)
    return "contact sales only" if extraction.contact_sales_only else "no public prices"


def _vendor_prices(members: Members, latest: dict) -> list[dict]:
    haystack = " ".join(f"{s.problem.quote} {s.workaround.quote} {s.existing_tool_complaint.quote} {d.title}"
                        for s, d in members).lower()
    return [{"vendor": vendor, "tiers": _tier_summary(extraction), **_ref(doc)}
            for vendor, (extraction, doc) in latest.items() if vendor.lower() in haystack]


def price_history(prices: list[tuple[PriceExtraction, Document]]) -> list[dict]:
    by_vendor: dict[str, list[tuple[PriceExtraction, Document]]] = {}
    for extraction, doc in prices:
        by_vendor.setdefault(doc.metrics.get("vendor", "?"), []).append((extraction, doc))
    rows = []
    for vendor, snaps in sorted(by_vendor.items()):
        snaps.sort(key=lambda p: p[1].published_at or datetime.min.replace(tzinfo=timezone.utc))
        previous = None
        for extraction, doc in snaps:
            summary = _tier_summary(extraction)
            rows.append({"vendor": vendor, "date": _date(doc.published_at), "tiers": summary,
                         "changed": previous is not None and summary != previous, "url": doc.url})
            previous = summary
    return rows


def card(cluster: Cluster, members: Members, latest: dict) -> dict:
    members = sorted(members, key=lambda m: (m[0].specificity, -m[0].intensity))
    return {
        "id": cluster.id,
        "title": cluster_title(cluster),  # same title as trend rows and the API
        "summary": cluster.synthesis.summary if cluster.synthesis else "",
        "hypotheses": cluster.synthesis.hypotheses if cluster.synthesis else [],
        "skeptic": [p.model_dump() for p in cluster.skeptic.points] if cluster.skeptic else [],
        "canonical": cluster.canonical,
        "confidence": cluster.confidence,
        "score": cluster.score,
        "n_signals": cluster.n_signals,
        "n_authors": cluster.n_authors,
        "source_types": cluster.source_types,
        "span": f"{_date(cluster.first_seen)} → {_date(cluster.last_seen)}",
        "segment": _top_values(members, "segment"),
        "affected_role": _top_values(members, "affected_role"),
        "likely_buyer": _top_values(members, "likely_buyer"),
        "frequency": _frequency(members),
        "frequency_quotes": _quotes(members, "occurrence_freq", 2),
        "financial": _financial(members),
        "workarounds": _quotes(members, "workaround"),
        "tool_complaints": _quotes(members, "existing_tool_complaint"),
        "spend": _spend(members),
        "evidence": _quotes(members, "problem"),
        "pricing": _vendor_prices(members, latest),
        "components": cluster.components,
    }


def build_context(store, top: int = 20, job_tasks: list[dict] | None = None, demo: bool = False) -> dict:
    by_id = {s.id: (s, d) for s, d in store.signals_with_docs()}
    prices = store.prices_with_docs()
    latest = latest_prices(prices)
    cards = []
    for cluster in store.clusters()[:top]:
        members = [by_id[i] for i in cluster.signal_ids if i in by_id]
        if members:
            cards.append(card(cluster, members, latest))
    notes = store.extraction_notes()
    return {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "demo": demo,
        "cards": cards,
        "jobs": (job_tasks or [])[:25],
        "prices": price_history(prices),
        "sources": store.source_stats(),
        "verification": {
            "dropped_signals": sum(1 for n in notes if n.startswith("dropped")),
            "downgrades": Counter(n.split(":")[0] for n in notes if not n.startswith("dropped")).most_common(),
        },
    }


# ------------------------------------------------------------------ markdown


def safe_url(url: str | None) -> str | None:
    """Only absolute http(s) URLs become links; anything else (javascript:, data:, relative) is shown as text."""
    url = (url or "").strip()
    try:
        parts = urlparse(url)
    except ValueError:
        return None
    return url if parts.scheme.lower() in ("http", "https") and parts.netloc else None


_MD_SPECIAL = re.compile(r"([\\\[\]<>|])")  # enough for no links/images ([ ]), HTML/autolinks (< >), table breaks (|)


def _md(text) -> str:
    """Source and model text as inert Markdown: no links, images, HTML or table breaks; otherwise readable raw."""
    return _MD_SPECIAL.sub(r"\\\1", " ".join(str(text or "").split()))


def _md_url(url: str) -> str:
    return quote(url, safe=":/?#@!$&'*+,;=%~-._[]")  # encodes spaces, parentheses, <, > and |


def _md_ref(item: dict) -> str:
    url = safe_url(item.get("url"))
    return f"[{_md(item.get('label', ''))}]({_md_url(url)})" if url else _md(item.get("label", ""))


def render_markdown(ctx: dict) -> str:
    out = [f"# Pain Report — {ctx['generated']}", ""]
    if ctx["demo"]:
        out += ["> **DEMO** — built from synthetic fixture documents (tests/fixtures). Not research findings.", ""]
    out += ["Every quote below was found verbatim in its source by code. Values marked *inferred* are model "
            "hypotheses. Financial figures appear only when a source states them (or a derivation is shown).", ""]

    for rank, c in enumerate(ctx["cards"], 1):
        out += [f"## {rank}. {_md(c['title'])}", "",
                f"**Confidence:** {c['confidence']} · **Score:** {c['score']:.2f} · {c['n_signals']} signals · "
                f"{c['n_authors']} distinct authors · {', '.join(c['source_types'])} · {c['span']}", ""]
        if c["summary"]:
            out += [_md(c["summary"]), ""]
        financial = "; ".join(
            f"{_md(f['value'])} ({f['status']}) — \"{_md(f['quote'])}\" {_md_ref(f)}"
            + (f" — basis: {_md(f['basis'])}" if f["basis"] else "")
            for f in c["financial"]
        ) or "Not documented in sources"
        out += ["| Field | Evidence |", "|---|---|",
                f"| Problem (canonical) | {_md(c['canonical'])} |",
                f"| Segment | {_md('; '.join(c['segment']) or 'unknown')} |",
                f"| Affected role | {_md('; '.join(c['affected_role']) or 'unknown')} |",
                f"| Likely buyer | {_md('; '.join(c['likely_buyer']) or 'unknown')} |",
                f"| Frequency | {_md(c['frequency'])} |",
                f"| Financial impact | {financial} |", ""]
        for heading, key in (("Evidence", "evidence"), ("Workarounds", "workarounds"),
                             ("Complaints about existing tools", "tool_complaints")):
            if c[key]:
                out += [f"**{heading}**", ""] + [f"- \"{_md(q['quote'])}\" — {_md_ref(q)}" for q in c[key]] + [""]
        if c["spend"]:
            out += ["**Spend evidence**", ""]
            out += [f"- {_md(s['type'])}{' · ' + _md(s['amount']) if s['amount'] else ''} — \"{_md(s['quote'])}\" — {_md_ref(s)}"
                    for s in c["spend"]] + [""]
        if c["pricing"]:
            out += ["**Pricing on record**", ""] + [f"- {_md(p['vendor'])}: {_md(p['tiers'])} — {_md_ref(p)}"
                                                     for p in c["pricing"]] + [""]
        comps = " · ".join(f"{k} {v:.2f}" for k, v in c["components"].items())
        out += [f"**Score components:** {comps}", ""]
        if c["hypotheses"]:
            out += ["**Open hypotheses (to test in interviews)**", ""] + [f"- {_md(h)}" for h in c["hypotheses"]] + [""]
        if c["skeptic"]:
            out += ["**Skeptic**", ""]
            out += [f"- ({p['kind']}) {_md(p['claim'])}" + (f" [{', '.join(p['signal_ids'])}]" if p["signal_ids"] else "")
                    for p in c["skeptic"]] + [""]

    if ctx["jobs"]:
        out += ["## Repetitive tasks in job posts", "",
                "| Task | O*NET | Postings | Companies | Tools | Frequency | Posted pay |", "|---|---|---|---|---|---|---|"]
        for j in ctx["jobs"]:
            out.append(f"| {_md(j['task'])} | {_md(j['onet'])} | {j['postings']} | {j['companies']} | "
                       f"{_md(', '.join(j['tools']))} | {_md(', '.join(j['frequency']))} | "
                       f"{_md('; '.join(j['pay_quotes'][:2]))} |")
        out += ["", "Posted pay is the role's pay, not the cost of the task: the share of the role spent on a task "
                "is a hypothesis.", ""]

    if ctx["prices"]:
        out += ["## Pricing history", "", "| Vendor | Date | Tiers | Changed |", "|---|---|---|---|"]
        out += [f"| {_md(p['vendor'])} | {_md_ref({'url': p['url'], 'label': p['date']})} | {_md(p['tiers'])} | {'yes' if p['changed'] else ''} |"
                for p in ctx["prices"]] + [""]

    out += ["## Coverage and verification", "", "| Source | Docs | Duplicates | Triaged | Pain | Signals | Dropped |",
            "|---|---|---|---|---|---|---|"]
    out += [f"| {s['source']} | {s['docs']} | {s['duplicates']} | {s['triaged']} | {s['pain']} | {s['signals']} | "
            f"{s['dropped']} |" for s in ctx["sources"]]
    downgrades = ", ".join(f"{k} ({n})" for k, n in ctx["verification"]["downgrades"]) or "none"
    out += ["", f"Signals dropped because the problem quote wasn't in the source: "
            f"{ctx['verification']['dropped_signals']}. Field downgrades: {downgrades}.", ""]
    return "\n".join(out)


# ------------------------------------------------------------------ html

_CSS = """
body{font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;color:#1d2430;background:#f6f7f9;margin:0}
main{max-width:980px;margin:0 auto;padding:32px 20px 64px}
h1{font-size:26px;margin:0 0 4px}h2{font-size:19px;margin:0 0 6px}h3{font-size:14px;margin:18px 0 6px;text-transform:uppercase;letter-spacing:.04em;color:#5b6472}
.sub{color:#5b6472;margin:0 0 20px}.demo{background:#fff4d6;border:1px solid #f0d488;padding:10px 14px;border-radius:8px;margin:0 0 20px}
.card{background:#fff;border:1px solid #e3e6eb;border-radius:12px;padding:20px 22px;margin:0 0 18px}
.meta{color:#5b6472;font-size:13px;margin-bottom:10px}.badge{display:inline-block;padding:1px 9px;border-radius:999px;font-size:12px;font-weight:600;margin-right:6px}
.High{background:#dff5e3;color:#1c6b2c}.Medium{background:#fff1cc;color:#7a5a00}.Low{background:#eef0f3;color:#4b5563}
table{border-collapse:collapse;width:100%;font-size:14px}td,th{border-top:1px solid #eef0f3;padding:6px 8px;vertical-align:top;text-align:left}
th{color:#5b6472;font-weight:600;width:170px}ul{margin:4px 0 0;padding-left:20px}li{margin:3px 0}
q{font-style:italic}a{color:#2456c7;text-decoration:none}.muted{color:#8a929e}.comp{font-size:13px;color:#5b6472}
"""


def _e(text) -> str:
    return html.escape(str(text or ""))


def _href(item: dict) -> str:
    url = safe_url(item.get("url"))
    label = _e(item.get("label", ""))
    return f'<a href="{_e(url)}" rel="noopener noreferrer">{label}</a>' if url else label


def _quote_list(items: list[dict]) -> str:
    return "<ul>" + "".join(f"<li><q>{_e(i['quote'])}</q> — {_href(i)}</li>" for i in items) + "</ul>"


def render_html(ctx: dict) -> str:
    parts = [f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>Pain Report {_e(ctx['generated'])}</title>",
             f"<style>{_CSS}</style></head><body><main>",
             f"<h1>Pain Report</h1><p class='sub'>{_e(ctx['generated'])} · every quote was found verbatim in its "
             "source by code · <em>inferred</em> values are model hypotheses</p>"]
    if ctx["demo"]:
        parts.append("<div class='demo'><strong>DEMO</strong> — built from synthetic fixture documents. "
                     "Not research findings.</div>")
    for rank, c in enumerate(ctx["cards"], 1):
        financial = "".join(
            f"<li>{_e(f['value'])} ({_e(f['status'])}) — <q>{_e(f['quote'])}</q> — {_href(f)}"
            + (f"<br><span class='muted'>basis: {_e(f['basis'])}</span>" if f["basis"] else "") + "</li>"
            for f in c["financial"])
        rows = [("Problem", _e(c["canonical"])),
                ("Segment", _e("; ".join(c["segment"]) or "unknown")),
                ("Affected role", _e("; ".join(c["affected_role"]) or "unknown")),
                ("Likely buyer", _e("; ".join(c["likely_buyer"]) or "unknown")),
                ("Frequency", _e(c["frequency"]) + (_quote_list(c["frequency_quotes"]) if c["frequency_quotes"] else "")),
                ("Financial impact", f"<ul>{financial}</ul>" if financial else "<span class='muted'>Not documented in sources</span>")]
        parts.append(f"<section class='card'><h2>{rank}. {_e(c['title'])}</h2>"
                     f"<div class='meta'><span class='badge {c['confidence']}'>{c['confidence']}</span>"
                     f"score {c['score']:.2f} · {c['n_signals']} signals · {c['n_authors']} distinct authors · "
                     f"{_e(', '.join(c['source_types']))} · {_e(c['span'])}</div>")
        if c["summary"]:
            parts.append(f"<p>{_e(c['summary'])}</p>")
        parts.append("<table>" + "".join(f"<tr><th>{k}</th><td>{v}</td></tr>" for k, v in rows) + "</table>")
        for heading, key in (("Evidence", "evidence"), ("Workarounds", "workarounds"),
                             ("Complaints about existing tools", "tool_complaints")):
            if c[key]:
                parts.append(f"<h3>{heading}</h3>{_quote_list(c[key])}")
        if c["spend"]:
            parts.append("<h3>Spend evidence</h3><ul>" + "".join(
                f"<li>{_e(s['type'])}{' · ' + _e(s['amount']) if s['amount'] else ''} — <q>{_e(s['quote'])}</q> — "
                f"{_href(s)}</li>" for s in c["spend"]) + "</ul>")
        if c["pricing"]:
            parts.append("<h3>Pricing on record</h3><ul>" + "".join(
                f"<li>{_e(p['vendor'])}: {_e(p['tiers'])} — {_href(p)}</li>" for p in c["pricing"]) + "</ul>")
        if c["hypotheses"]:
            parts.append("<h3>Open hypotheses</h3><ul>" + "".join(f"<li>{_e(h)}</li>" for h in c["hypotheses"]) + "</ul>")
        if c["skeptic"]:
            parts.append("<h3>Skeptic</h3><ul>" + "".join(
                f"<li><span class='muted'>({_e(p['kind'])})</span> {_e(p['claim'])}"
                + (f" <span class='muted'>[{_e(', '.join(p['signal_ids']))}]</span>" if p["signal_ids"] else "")
                + "</li>" for p in c["skeptic"]) + "</ul>")
        parts.append("<p class='comp'>" + " · ".join(f"{_e(k)} {v:.2f}" for k, v in c["components"].items())
                     + "</p></section>")

    if ctx["jobs"]:
        parts.append("<section class='card'><h2>Repetitive tasks in job posts</h2><table><tr><th>Task</th><th>O*NET</th>"
                     "<th>Posts</th><th>Cos.</th><th>Tools</th><th>Frequency</th><th>Posted pay</th></tr>")
        for j in ctx["jobs"]:
            parts.append(f"<tr><td>{_e(j['task'])}</td><td>{_e(j['onet'])}</td><td>{j['postings']}</td>"
                         f"<td>{j['companies']}</td><td>{_e(', '.join(j['tools']))}</td>"
                         f"<td>{_e(', '.join(j['frequency']))}</td><td>{_e('; '.join(j['pay_quotes'][:2]))}</td></tr>")
        parts.append("</table><p class='muted'>Posted pay is the role's pay, not the cost of the task.</p></section>")

    if ctx["prices"]:
        parts.append("<section class='card'><h2>Pricing history</h2><table><tr><th>Vendor</th><th>Date</th>"
                     "<th>Tiers</th><th>Changed</th></tr>" + "".join(
                         f"<tr><td>{_e(p['vendor'])}</td><td>{_href({'url': p['url'], 'label': p['date']})}</td>"
                         f"<td>{_e(p['tiers'])}</td><td>{'yes' if p['changed'] else ''}</td></tr>"
                         for p in ctx["prices"]) + "</table></section>")

    parts.append("<section class='card'><h2>Coverage and verification</h2><table><tr><th>Source</th><th>Docs</th>"
                 "<th>Dupes</th><th>Triaged</th><th>Pain</th><th>Signals</th><th>Dropped</th></tr>" + "".join(
                     f"<tr><td>{_e(s['source'])}</td><td>{s['docs']}</td><td>{s['duplicates']}</td><td>{s['triaged']}</td>"
                     f"<td>{s['pain']}</td><td>{s['signals']}</td><td>{s['dropped']}</td></tr>" for s in ctx["sources"])
                 + "</table></section></main></body></html>")
    return "".join(parts)


def write_report(store, report_dir: Path, top: int = 20, job_tasks: list[dict] | None = None,
                 demo: bool = False, stem: str | None = None) -> tuple[Path, Path]:
    ctx = build_context(store, top=top, job_tasks=job_tasks, demo=demo)
    report_dir.mkdir(parents=True, exist_ok=True)
    stem = stem or f"pain-report-{datetime.now(timezone.utc).date().isoformat()}"
    md_path, html_path = report_dir / f"{stem}.md", report_dir / f"{stem}.html"
    md_path.write_text(render_markdown(ctx))
    html_path.write_text(render_html(ctx))
    return md_path, html_path
