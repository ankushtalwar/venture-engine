from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser


class CollectorUnavailable(Exception):
    """The collector can't run (missing key, source not approved)."""


@dataclass
class Query:
    text: str
    arm_id: str | None = None
    params: dict = field(default_factory=dict)


_BLOCK_TAGS = {
    "p", "div", "br", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section", "article",
    "header", "footer", "table", "blockquote", "pre", "dd", "dt",
}
_SKIP_TAGS = {"script", "style", "noscript", "svg", "template"}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip += 1
        elif tag == "li":
            self.parts.append("\n- ")
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
        elif tag in _BLOCK_TAGS or tag == "li":
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(raw: str | None) -> str:
    if not raw:
        return ""
    parser = _TextExtractor()
    parser.feed(raw)
    parser.close()
    text = "".join(parser.parts)
    text = re.sub(r"[ \t\r\f\v ]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def hash_author(source: str, username: str | None) -> str | None:
    """Store a salted hash, never the username itself."""
    if not username:
        return None
    salt = os.environ.get("PAINDISC_SALT", "paindisc")
    return hashlib.sha256(f"{salt}:{source}:{username}".encode()).hexdigest()[:12]


def parse_dt(value) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        seconds = value / 1000 if value > 1e11 else value
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    text = str(value).strip()
    if re.fullmatch(r"\d{14}", text):  # Wayback timestamps
        return datetime.strptime(text, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
