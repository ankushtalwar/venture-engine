"""Source registry: the only thing that decides which hosts the collectors may call."""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from pathlib import Path
from urllib.parse import urlparse

import yaml


class RegistryError(Exception):
    """Raised when a request is not permitted by the registry."""


class RobotsDisallowed(RegistryError):
    """Raised when robots.txt disallows a URL for our user agent."""


@dataclass
class SourceEntry:
    name: str
    domains: list[str] = field(default_factory=list)
    route: str = ""
    allowed: bool = False
    terms_url: str = ""
    reviewed: str = ""
    reviewed_by: str = ""
    rate_limit_per_min: float = 30.0
    robots: bool = False
    attribution: str = ""
    notes: str = ""
    reason: str = ""


class Registry:
    def __init__(self, entries: dict[str, SourceEntry]):
        self.entries = entries

    @classmethod
    def load(cls, config_dir: Path) -> "Registry":
        raw = yaml.safe_load((config_dir / "registry.yaml").read_text()) or {}
        known = {f.name for f in fields(SourceEntry)}
        entries: dict[str, SourceEntry] = {}
        for name, spec in (raw.get("sources") or {}).items():
            spec = dict(spec or {})
            kwargs = {k: v for k, v in spec.items() if k in known and k not in {"name", "domains"}}
            if "reviewed" in kwargs:
                kwargs["reviewed"] = str(kwargs["reviewed"])
            entries[name] = SourceEntry(name=name, domains=[d.lower() for d in spec.get("domains") or []], **kwargs)

        # Pricing pages are registered one URL host at a time, and only once you've checked the site's terms.
        targets_path = config_dir / "pricing_targets.yaml"
        if "pricing_pages" in entries and targets_path.exists():
            targets = (yaml.safe_load(targets_path.read_text()) or {}).get("targets") or []
            for target in targets:
                if target.get("terms_checked"):
                    host = (urlparse(target["url"]).hostname or "").lower()
                    if host:
                        entries["pricing_pages"].domains.append(host)
        return cls(entries)

    @staticmethod
    def _host_matches(host: str, domains: list[str]) -> bool:
        return any(host == d or host.endswith("." + d) for d in domains)

    def guard(self, url: str, source: str) -> SourceEntry:
        host = (urlparse(url).hostname or "").lower()
        entry = self.entries.get(source)
        if entry is None:
            raise RegistryError(f"source '{source}' is not in the registry")
        if not entry.allowed:
            raise RegistryError(f"source '{source}' is not permitted: {entry.reason or 'not approved'}")
        if not self._host_matches(host, entry.domains):
            raise RegistryError(f"host '{host}' is not registered for source '{source}'")
        return entry

    def summary(self) -> list[dict]:
        return [
            {
                "source": e.name,
                "allowed": e.allowed,
                "route": e.route,
                "domains": ", ".join(e.domains) or "-",
                "reviewed": f"{e.reviewed} {e.reviewed_by}".strip(),
                "reason": e.reason or e.notes,
            }
            for e in self.entries.values()
        ]
