"""HTTP client that enforces the registry, robots.txt, per-source rate limits and guarded redirects."""

from __future__ import annotations

import time
from typing import Callable
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx

from .config import Settings
from .registry import Registry, RegistryError, RobotsDisallowed

REDIRECT_CODES = {301, 302, 303, 307, 308}


class PoliteClient:
    def __init__(
        self,
        settings: Settings,
        registry: Registry,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.settings = settings
        self.registry = registry
        self.http = httpx.Client(
            timeout=30.0,
            headers={"User-Agent": settings.user_agent},
            transport=transport,
            follow_redirects=False,  # every hop goes back through the registry
        )
        self._sleep = sleep
        self._last_call: dict[str, float] = {}
        self._robots: dict[str, RobotFileParser | None] = {}

    def get(self, url: str, *, source: str, params: dict | None = None, headers: dict | None = None,
            robots: bool = False) -> httpx.Response:
        return self._request("GET", url, source=source, params=params, headers=headers, robots=robots)

    def post(self, url: str, *, source: str, json: dict | None = None, headers: dict | None = None) -> httpx.Response:
        return self._request("POST", url, source=source, json=json, headers=headers)

    def _request(self, method: str, url: str, *, source: str, params=None, headers=None, json=None,
                 robots: bool = False, max_redirects: int = 3) -> httpx.Response:
        for _ in range(max_redirects + 1):
            entry = self.registry.guard(url, source)
            if robots or entry.robots:
                self._check_robots(url)
            self._throttle(source, entry.rate_limit_per_min)
            resp = self.http.request(method, url, params=params, headers=headers, json=json)
            if resp.status_code == 429:
                retry_after = resp.headers.get("retry-after", "30")
                self._sleep(min(float(retry_after) if retry_after.isdigit() else 30.0, 120.0))
                resp = self.http.request(method, url, params=params, headers=headers, json=json)
            if resp.status_code in REDIRECT_CODES and "location" in resp.headers:
                url = str(resp.url.join(resp.headers["location"]))
                params = None  # the location already carries its query string
                continue
            resp.raise_for_status()
            return resp
        raise RegistryError(f"too many redirects for {url}")

    def _throttle(self, source: str, per_minute: float) -> None:
        if not per_minute:
            return
        gap = 60.0 / per_minute
        last = self._last_call.get(source)
        if last is not None:
            wait = gap - (time.monotonic() - last)
            if wait > 0:
                self._sleep(wait)
        self._last_call[source] = time.monotonic()

    def _check_robots(self, url: str) -> None:
        parts = urlparse(url)
        base = f"{parts.scheme}://{parts.netloc}"
        if base not in self._robots:
            parser: RobotFileParser | None = RobotFileParser()
            try:
                resp = self.http.get(base + "/robots.txt")
                if resp.status_code in (401, 403):
                    parser.disallow_all = True
                elif 400 <= resp.status_code < 500:
                    parser.allow_all = True
                elif resp.status_code >= 500:
                    parser = None  # can't tell: be conservative
                else:
                    parser.parse(resp.text.splitlines())
            except httpx.HTTPError:
                parser = None
            self._robots[base] = parser
        parser = self._robots[base]
        if parser is None or not parser.can_fetch(self.settings.user_agent, url):
            raise RobotsDisallowed(f"robots.txt disallows {url}")

    def close(self) -> None:
        self.http.close()
