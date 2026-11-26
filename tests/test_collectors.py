import httpx
import pytest

from paindisc.collectors import (BraveCollector, CollectorUnavailable, GitHubIssuesCollector, GreenhouseCollector,
                                 HNCollector, LeverCollector, Query)
from paindisc.http import PoliteClient
from paindisc.registry import Registry

RESPONSES = {
    "hn.algolia.com": {"hits": [
        {"objectID": "123", "created_at": "2026-10-01T10:00:00.000Z", "author": "alice", "title": "Ask HN: invoices",
         "comment_text": "<p>We still do this manually &amp; it takes hours every week to re-key invoices from PDFs.</p>",
         "points": 5, "num_comments": 2},
        {"objectID": "124", "created_at": "2026-10-02T10:00:00.000Z", "author": "bob", "comment_text": "too short"},
    ]},
    "api.github.com": {"items": [
        {"html_url": "https://github.com/o/r/issues/7", "repository_url": "https://api.github.com/repos/o/r",
         "number": 7, "title": "Import invoices from PDF", "user": {"login": "carol"}, "state": "open",
         "created_at": "2026-08-01T00:00:00Z", "comments": 4, "labels": [{"name": "enhancement"}],
         "reactions": {"+1": 31, "total_count": 35},
         "body": "Small firms re-key every invoice by hand. Please add PDF import so we can stop doing this weekly."},
        {"html_url": "https://github.com/o/r/pull/8", "repository_url": "https://api.github.com/repos/o/r",
         "number": 8, "title": "PR", "pull_request": {}, "body": "A pull request body that is long enough to keep."},
    ]},
    "boards-api.greenhouse.io": {"jobs": [
        {"id": 1, "title": "Accounts Payable Specialist", "absolute_url": "https://boards.greenhouse.io/acme/jobs/1",
         "updated_at": "2026-10-01T00:00:00-05:00", "location": {"name": "Austin, TX"}, "company_name": "Acme",
         "departments": [{"name": "Finance"}],
         "content": "&lt;ul&gt;&lt;li&gt;Enter vendor invoices into QuickBooks daily&lt;/li&gt;&lt;/ul&gt;"},
        {"id": 2, "title": "Senior Software Engineer", "absolute_url": "https://boards.greenhouse.io/acme/jobs/2",
         "content": "&lt;p&gt;Build things&lt;/p&gt;"},
    ]},
    "api.lever.co": [
        {"id": "abc", "text": "Billing Coordinator", "createdAt": 1759300000000,
         "categories": {"team": "Finance", "location": "Remote", "commitment": "Full-time"},
         "descriptionPlain": "We are growing.",
         "lists": [{"text": "What you'll do", "content": "<li>Reconcile vendor statements every month</li>"}],
         "hostedUrl": "https://jobs.lever.co/northwind/abc",
         "salaryRange": {"currency": "USD", "interval": "per-year-salary", "min": 45000, "max": 52000}},
    ],
    "api.search.brave.com": {"web": {"results": [
        {"url": "https://forum.example.com/t/1", "title": "<strong>Rent roll</strong> pain",
         "description": "We use <strong>Excel</strong> to manage our rent roll and it takes hours every month.",
         "extra_snippets": ["Is there a tool that does this for small landlords?"], "page_age": "2026-09-01T00:00:00"},
    ]}},
}


def handler(request: httpx.Request) -> httpx.Response:
    data = RESPONSES.get(request.url.host)
    return httpx.Response(200, json=data) if data is not None else httpx.Response(404)


@pytest.fixture
def client(settings):
    return PoliteClient(settings, Registry.load(settings.config_dir), transport=httpx.MockTransport(handler),
                        sleep=lambda _: None)


def test_hn_parses_hits_and_hashes_authors(client, settings):
    docs = HNCollector(client, settings).collect(Query("re-key invoices", arm_id="arm1"))
    assert len(docs) == 1  # the short comment is skipped
    doc = docs[0]
    assert doc.url == "https://news.ycombinator.com/item?id=123"
    assert "manually & it takes hours" in doc.text
    assert doc.author_hash and doc.author_hash != "alice"
    assert doc.arm_id == "arm1" and doc.published_at.year == 2026


def test_github_skips_pull_requests_and_keeps_reactions(client, settings):
    docs = GitHubIssuesCollector(client, settings).collect(Query('"invoice" label:enhancement'))
    assert len(docs) == 1
    assert docs[0].metrics["thumbs_up"] == 31 and docs[0].metrics["repo"] == "o/r"
    assert docs[0].source_type == "issue_tracker"


def test_greenhouse_unescapes_content_and_filters_titles(client, settings):
    query = Query("acme", params={"title_filter": "(?i)payable|billing"})
    docs = GreenhouseCollector(client, settings).collect(query)
    assert [d.title for d in docs] == ["Accounts Payable Specialist"]
    assert "- Enter vendor invoices into QuickBooks daily" in docs[0].text
    assert docs[0].metrics["company"] == "Acme"


def test_lever_includes_lists_and_structured_salary(client, settings):
    docs = LeverCollector(client, settings).collect(Query("northwind"))
    assert len(docs) == 1
    assert "- Reconcile vendor statements every month" in docs[0].text
    assert "[structured field] salaryRange: USD 45000-52000 per-year-salary" in docs[0].text
    assert docs[0].published_at.year == 2025


def test_brave_needs_a_key_and_returns_snippet_leads(client, settings):
    with pytest.raises(CollectorUnavailable):
        BraveCollector(client, settings).collect(Query("rent roll excel"))
    settings.brave_api_key = "test-key"
    docs = BraveCollector(client, settings).collect(Query("rent roll excel"))
    assert len(docs) == 1
    assert "<strong>" not in docs[0].text and docs[0].metrics["domain"] == "forum.example.com"
    assert docs[0].route == "search_api"
