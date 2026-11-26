"""Claude wrapper: structured outputs, parallel sync calls, and the Message Batches API."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, TypeVar

from pydantic import BaseModel

from .config import Settings

T = TypeVar("T", bound=BaseModel)
Results = dict[str, "BaseModel | Exception"]


class LLMError(Exception):
    pass


def output_schema(model: type[BaseModel]) -> dict:
    """JSON schema in the shape structured outputs accept (uses the SDK's transformer when present)."""
    try:
        from anthropic.lib._parse._transform import transform_schema
        return transform_schema(model)
    except ImportError:  # pragma: no cover - older/newer SDK layouts
        return model.model_json_schema()


def _system(text: str) -> list[dict]:
    return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]


class LLM:
    def __init__(self, settings: Settings, log: Callable[[str], None] = print, client=None):
        self.settings = settings
        self.mock = settings.llm_mock and client is None
        self.log = log
        self._client = client
        if not self.mock and self._client is None:
            import anthropic
            self._client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    def structured(self, *, model: str, system: str, user: str, output_type: type[T], max_tokens: int = 4096) -> T:
        resp = self._client.messages.parse(
            model=model,
            max_tokens=max_tokens,
            system=_system(system),
            messages=[{"role": "user", "content": user}],
            output_format=output_type,
        )
        parsed = resp.parsed_output
        if parsed is None:
            raise LLMError(f"no parsed output (stop_reason={resp.stop_reason})")
        return parsed

    def map(self, *, model: str, system: str, items: list[tuple[str, str]], output_type: type[T],
            max_tokens: int = 4096, workers: int = 8) -> Results:
        results: Results = {}
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(self.structured, model=model, system=system, user=user, output_type=output_type,
                            max_tokens=max_tokens): cid
                for cid, user in items
            }
            for future in as_completed(futures):
                cid = futures[future]
                try:
                    results[cid] = future.result()
                except Exception as exc:  # keep going; failures are reported per item
                    results[cid] = exc
        return results

    def batch(self, *, model: str, system: str, items: list[tuple[str, str]], output_type: type[T],
              max_tokens: int = 4096, poll_seconds: int = 30, chunk: int = 5000) -> Results:
        """Message Batches API: asynchronous and 50% cheaper. Most batches finish within an hour."""
        schema = output_schema(output_type)
        results: Results = {}
        for start in range(0, len(items), chunk):
            requests = [
                {
                    "custom_id": cid,
                    "params": {
                        "model": model,
                        "max_tokens": max_tokens,
                        "system": _system(system),
                        "messages": [{"role": "user", "content": user}],
                        "output_config": {"format": {"type": "json_schema", "schema": schema}},
                    },
                }
                for cid, user in items[start:start + chunk]
            ]
            batch = self._client.messages.batches.create(requests=requests)
            self.log(f"  batch {batch.id}: {len(requests)} requests submitted, polling every {poll_seconds}s")
            while batch.processing_status != "ended":
                time.sleep(poll_seconds)
                batch = self._client.messages.batches.retrieve(batch.id)
            for entry in self._client.messages.batches.results(batch.id):
                if entry.result.type == "succeeded":
                    text = "".join(getattr(block, "text", "") for block in entry.result.message.content)
                    try:
                        results[entry.custom_id] = output_type.model_validate_json(text)
                    except Exception as exc:
                        results[entry.custom_id] = exc
                else:
                    results[entry.custom_id] = LLMError(entry.result.type)
        return results

    def run(self, *, model: str, system: str, items: list[tuple[str, str]], output_type: type[T],
            max_tokens: int = 4096, batch: bool = False) -> Results:
        if not items:
            return {}
        if batch:
            return self.batch(model=model, system=system, items=items, output_type=output_type, max_tokens=max_tokens)
        return self.map(model=model, system=system, items=items, output_type=output_type, max_tokens=max_tokens)
