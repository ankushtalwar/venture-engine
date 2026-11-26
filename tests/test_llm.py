"""Exercise the real Claude code path (structured outputs + Message Batches) against a fake API server."""

import json

import anthropic
import httpx2 as httpx  # the Anthropic SDK ships its own httpx fork

from paindisc.llm import LLM
from paindisc.models import TriageResult

ANSWER = {"is_pain": True, "kind": "business_pain", "reason": "Re-keys invoices weekly."}
SENT: list[dict] = []


def message(text: str) -> dict:
    return {"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-haiku-5-5",
            "content": [{"type": "text", "text": text}], "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 5}}


BATCH = {"id": "msgbatch_1", "type": "message_batch", "processing_status": "ended",
         "request_counts": {"processing": 0, "succeeded": 2, "errored": 0, "canceled": 0, "expired": 0},
         "created_at": "2026-11-26T00:00:00Z", "ended_at": "2026-11-26T00:10:00Z",
         "expires_at": "2026-11-27T00:00:00Z", "cancel_initiated_at": None, "archived_at": None,
         "results_url": "https://api.anthropic.com/v1/messages/batches/msgbatch_1/results"}


def handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if request.method == "POST":
        SENT.append(json.loads(request.content))
    if path == "/v1/messages":
        return httpx.Response(200, json=message(json.dumps(ANSWER)))
    if path == "/v1/messages/batches" and request.method == "POST":
        return httpx.Response(200, json=BATCH)
    if path == "/v1/messages/batches/msgbatch_1":
        return httpx.Response(200, json=BATCH)
    if path == "/v1/messages/batches/msgbatch_1/results":
        lines = [
            {"custom_id": "doc1", "result": {"type": "succeeded", "message": message(json.dumps(ANSWER))}},
            {"custom_id": "doc2", "result": {"type": "errored", "error": {"type": "error", "error": {
                "type": "overloaded_error", "message": "busy"}}}},
        ]
        return httpx.Response(200, text="\n".join(json.dumps(line) for line in lines),
                              headers={"content-type": "application/binary"})
    return httpx.Response(404, json={"type": "error", "error": {"type": "not_found_error", "message": path}})


def make_llm(settings) -> LLM:
    settings.llm_mock = False
    client = anthropic.Anthropic(api_key="test", http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    return LLM(settings, log=lambda _: None, client=client)


def test_structured_call_sends_schema_and_parses(settings):
    SENT.clear()
    result = make_llm(settings).structured(model="claude-haiku-5-5", system="sys", user="doc",
                                           output_type=TriageResult, max_tokens=100)
    assert result == TriageResult(**ANSWER)
    fmt = SENT[0]["output_config"]["format"]
    assert fmt["type"] == "json_schema" and "is_pain" in fmt["schema"]["properties"]
    assert SENT[0]["system"][0]["cache_control"] == {"type": "ephemeral"}


def test_batch_call_parses_successes_and_reports_errors(settings):
    SENT.clear()
    results = make_llm(settings).batch(model="claude-haiku-5-5", system="sys", items=[("doc1", "a"), ("doc2", "b")],
                                       output_type=TriageResult, poll_seconds=0)
    assert results["doc1"] == TriageResult(**ANSWER)
    assert isinstance(results["doc2"], Exception)
    params = SENT[0]["requests"][0]["params"]
    assert params["output_config"]["format"]["type"] == "json_schema"
