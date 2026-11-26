"""Greenhouse and Lever public job-board collectors."""

from __future__ import annotations

import html
import re

from ..config import Settings
from ..http import PoliteClient
from ..models import Document
from .base import Query, html_to_text, now_utc, parse_dt


def _title_filter(q: Query):
    pattern = q.params.get("title_filter")
    return re.compile(pattern) if pattern else None


class GreenhouseCollector:
    name = "greenhouse"
    source_type = "job_post"
    route = "public_api"

    def __init__(self, client: PoliteClient, settings: Settings):
        self.client = client
        self.settings = settings

    def collect(self, q: Query, limit: int = 500) -> list[Document]:
        token = q.text.strip()
        url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs"
        data = self.client.get(url, source=self.name, params={"content": "true"}).json()
        title_re = _title_filter(q)
        docs = []
        for job in data.get("jobs", [])[:limit]:
            title = job.get("title") or ""
            if title_re and not title_re.search(title):
                continue
            body = html_to_text(html.unescape(job.get("content") or ""))
            company = job.get("company_name") or token
            location = (job.get("location") or {}).get("name", "")
            external_id = f"{token}:{job.get('id')}"
            docs.append(Document(
                id=Document.make_id(self.name, external_id),
                source=self.name,
                source_type=self.source_type,
                route=self.route,
                external_id=external_id,
                url=job.get("absolute_url") or "",
                title=title,
                text=f"{title}\n{company} - {location}\n\n{body}".strip(),
                published_at=parse_dt(job.get("first_published") or job.get("updated_at")),
                captured_at=now_utc(),
                metrics={
                    "company": company,
                    "board": token,
                    "location": location,
                    "departments": [d.get("name") for d in job.get("departments") or []],
                },
                query=token,
                arm_id=q.arm_id,
            ))
        return docs


class LeverCollector:
    name = "lever"
    source_type = "job_post"
    route = "public_api"

    def __init__(self, client: PoliteClient, settings: Settings):
        self.client = client
        self.settings = settings

    def collect(self, q: Query, limit: int = 500) -> list[Document]:
        site = q.text.strip()
        url = f"https://api.lever.co/v0/postings/{site}"
        data = self.client.get(url, source=self.name, params={"mode": "json"}).json()
        title_re = _title_filter(q)
        docs = []
        for post in (data if isinstance(data, list) else [])[:limit]:
            title = post.get("text") or ""
            if title_re and not title_re.search(title):
                continue
            categories = post.get("categories") or {}
            parts = [
                title,
                " | ".join(v for v in (categories.get("team"), categories.get("location"),
                                       categories.get("commitment")) if v),
                post.get("descriptionPlain") or html_to_text(post.get("description")),
            ]
            for block in post.get("lists") or []:
                parts.append(block.get("text") or "")
                parts.append(html_to_text(block.get("content")))
            parts.append(post.get("additionalPlain") or html_to_text(post.get("additional")))
            salary = post.get("salaryRange") or {}
            if salary.get("min") is not None:
                parts.append(
                    f"[structured field] salaryRange: {salary.get('currency', '')} "
                    f"{salary.get('min')}-{salary.get('max')} {salary.get('interval', '')}".strip()
                )
            if post.get("salaryDescriptionPlain"):
                parts.append(post["salaryDescriptionPlain"])
            external_id = f"{site}:{post.get('id')}"
            docs.append(Document(
                id=Document.make_id(self.name, external_id),
                source=self.name,
                source_type=self.source_type,
                route=self.route,
                external_id=external_id,
                url=post.get("hostedUrl") or "",
                title=title,
                text="\n".join(p for p in parts if p).strip(),
                published_at=parse_dt(post.get("createdAt")),
                captured_at=now_utc(),
                metrics={"company": site, "board": site, "location": categories.get("location", "")},
                query=site,
                arm_id=q.arm_id,
            ))
        return docs
