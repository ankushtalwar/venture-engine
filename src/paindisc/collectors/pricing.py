"""Vendor pricing pages (polite crawl, robots.txt enforced) and their Wayback Machine history."""

from __future__ import annotations

from ..config import Settings
from ..http import PoliteClient
from ..models import Document
from .base import html_to_text, now_utc, parse_dt


class PricingCollector:
    name = "pricing_pages"
    source_type = "pricing_page"
    route = "polite_crawl"

    def __init__(self, client: PoliteClient, settings: Settings):
        self.client = client
        self.settings = settings

    def collect_target(self, target: dict) -> Document:
        resp = self.client.get(target["url"], source=self.name, robots=True)
        captured = now_utc()
        external_id = f"{target['url']}@{captured.date().isoformat()}"
        return Document(
            id=Document.make_id(self.name, external_id),
            source=self.name,
            source_type=self.source_type,
            route=self.route,
            external_id=external_id,
            url=target["url"],
            title=f"{target['vendor']} pricing",
            text=html_to_text(resp.text),
            published_at=captured,
            captured_at=captured,
            metrics={"vendor": target["vendor"], "category": target.get("category", ""), "page_url": target["url"]},
        )


class WaybackCollector:
    name = "wayback"
    source_type = "pricing_page"
    route = "public_api"
    CDX = "https://web.archive.org/cdx/search/cdx"

    def __init__(self, client: PoliteClient, settings: Settings):
        self.client = client
        self.settings = settings

    def snapshots(self, url: str, since: str = "2023") -> list[tuple[str, str]]:
        params = {
            "url": url,
            "output": "json",
            "fl": "timestamp,original,statuscode,digest",
            "filter": "statuscode:200",
            "collapse": "digest",
            "from": since,
        }
        rows = self.client.get(self.CDX, source=self.name, params=params).json() or []
        if rows and rows[0] and rows[0][0] == "timestamp":
            rows = rows[1:]
        per_quarter: dict[str, tuple[str, str]] = {}
        for row in rows:
            timestamp, original = row[0], row[1]
            quarter = f"{timestamp[:4]}Q{(int(timestamp[4:6]) - 1) // 3 + 1}"
            per_quarter.setdefault(quarter, (timestamp, original))
        return [per_quarter[k] for k in sorted(per_quarter)]

    def collect_target(self, target: dict, since: str = "2023") -> list[Document]:
        docs = []
        for timestamp, original in self.snapshots(target["url"], since):
            raw_url = f"https://web.archive.org/web/{timestamp}id_/{original}"
            resp = self.client.get(raw_url, source=self.name)
            external_id = f"{original}@{timestamp}"
            docs.append(Document(
                id=Document.make_id(self.name, external_id),
                source=self.name,
                source_type=self.source_type,
                route=self.route,
                external_id=external_id,
                url=f"https://web.archive.org/web/{timestamp}/{original}",
                title=f"{target['vendor']} pricing (archived {timestamp[:8]})",
                text=html_to_text(resp.text),
                published_at=parse_dt(timestamp),
                captured_at=now_utc(),
                metrics={"vendor": target["vendor"], "category": target.get("category", ""),
                         "page_url": target["url"], "archived": timestamp},
            ))
        return docs
