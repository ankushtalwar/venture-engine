"""Hacker News (Algolia), GitHub Issues and Brave Search collectors."""

from __future__ import annotations

import time
from urllib.parse import urlparse

from ..config import Settings
from ..http import PoliteClient
from ..models import Document
from .base import CollectorUnavailable, Query, hash_author, html_to_text, now_utc, parse_dt


class HNCollector:
    name = "hn"
    source_type = "forum"
    route = "public_api"
    URL = "https://hn.algolia.com/api/v1/search"

    def __init__(self, client: PoliteClient, settings: Settings):
        self.client = client
        self.settings = settings

    def collect(self, q: Query, limit: int = 50, since_days: int = 730) -> list[Document]:
        params = {
            "query": q.text,
            "tags": "(story,comment)",
            "hitsPerPage": min(limit, 100),
            "numericFilters": f"created_at_i>{int(time.time()) - since_days * 86400}",
        }
        data = self.client.get(self.URL, source=self.name, params=params).json()
        docs = []
        for hit in data.get("hits", []):
            text = html_to_text(hit.get("comment_text") or hit.get("story_text") or "")
            if len(text) < 60:
                continue
            item_id = str(hit["objectID"])
            docs.append(Document(
                id=Document.make_id(self.name, item_id),
                source=self.name,
                source_type=self.source_type,
                route=self.route,
                external_id=item_id,
                url=f"https://news.ycombinator.com/item?id={item_id}",
                title=hit.get("title") or hit.get("story_title") or "",
                text=text,
                author_hash=hash_author(self.name, hit.get("author")),
                published_at=parse_dt(hit.get("created_at")),
                captured_at=now_utc(),
                metrics={"points": hit.get("points"), "num_comments": hit.get("num_comments")},
                query=q.text,
                arm_id=q.arm_id,
            ))
        return docs


class GitHubIssuesCollector:
    name = "github"
    source_type = "issue_tracker"
    route = "official_api"
    URL = "https://api.github.com/search/issues"

    def __init__(self, client: PoliteClient, settings: Settings):
        self.client = client
        self.settings = settings

    def collect(self, q: Query, limit: int = 30) -> list[Document]:
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if self.settings.github_token:
            headers["Authorization"] = f"Bearer {self.settings.github_token}"
        query = q.text if "is:issue" in q.text else f"{q.text} is:issue"
        params = {"q": query, "sort": "reactions-+1", "order": "desc", "per_page": min(limit, 100)}
        data = self.client.get(self.URL, source=self.name, params=params, headers=headers).json()
        docs = []
        for item in data.get("items", []):
            if "pull_request" in item:
                continue
            body = (item.get("body") or "").strip()
            if len(body) < 60:
                continue
            repo = (item.get("repository_url") or "").split("/repos/")[-1]
            reactions = item.get("reactions") or {}
            external_id = f"{repo}#{item.get('number')}"
            docs.append(Document(
                id=Document.make_id(self.name, external_id),
                source=self.name,
                source_type=self.source_type,
                route=self.route,
                external_id=external_id,
                url=item.get("html_url", ""),
                title=item.get("title") or "",
                text=body,
                author_hash=hash_author(self.name, (item.get("user") or {}).get("login")),
                published_at=parse_dt(item.get("created_at")),
                captured_at=now_utc(),
                metrics={
                    "repo": repo,
                    "thumbs_up": reactions.get("+1", 0),
                    "reactions": reactions.get("total_count", 0),
                    "comments": item.get("comments", 0),
                    "state": item.get("state"),
                    "labels": [label.get("name") for label in item.get("labels") or []],
                },
                query=q.text,
                arm_id=q.arm_id,
            ))
        return docs


class BraveCollector:
    """Discovery only: stores Brave's result snippets as leads. Never fetches the result pages."""

    name = "brave"
    source_type = "search_snippet"
    route = "search_api"
    URL = "https://api.search.brave.com/res/v1/web/search"

    def __init__(self, client: PoliteClient, settings: Settings):
        self.client = client
        self.settings = settings

    def search(self, query: str, count: int = 20) -> list[dict]:
        if not self.settings.brave_api_key:
            raise CollectorUnavailable("BRAVE_API_KEY is not set")
        headers = {"Accept": "application/json", "X-Subscription-Token": self.settings.brave_api_key}
        params = {"q": query, "count": min(count, 20), "extra_snippets": "true"}
        data = self.client.get(self.URL, source=self.name, params=params, headers=headers).json()
        return (data.get("web") or {}).get("results", [])

    def collect(self, q: Query, limit: int = 20) -> list[Document]:
        docs = []
        for result in self.search(q.text, limit):
            snippets = [result.get("description", "")] + list(result.get("extra_snippets") or [])
            text = "\n".join(html_to_text(s) for s in snippets if s).strip()
            if len(text) < 60:
                continue
            url = result.get("url", "")
            docs.append(Document(
                id=Document.make_id(self.name, url),
                source=self.name,
                source_type=self.source_type,
                route=self.route,
                external_id=url,
                url=url,
                title=html_to_text(result.get("title", "")),
                text=text,
                published_at=parse_dt(result.get("page_age")),
                captured_at=now_utc(),
                metrics={"domain": urlparse(url).netloc, "lead": True},
                query=q.text,
                arm_id=q.arm_id,
            ))
        return docs
