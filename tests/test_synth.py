"""synthesize_cluster: one cluster's synthesis + skeptic pass, in mock mode and against a fake Claude API."""

from __future__ import annotations

import json

import anthropic
import httpx2
import pytest
from conftest import FIXTURES, make_pipeline

from paindisc.llm import LLM, LLMError
from paindisc.models import SkepticReview, Synthesis
from paindisc.synth import evidence_pack, synthesize_cluster


def clustered(settings):
    p = make_pipeline(settings)
    p.load_fixture(FIXTURES / "demo_docs.jsonl")
    p.triage()
    p.extract()
    p.cluster(threshold=0.75)
    return p


def test_mock_synthesis_is_saved_for_one_cluster_only(settings):
    p = clustered(settings)
    first, second = p.store.clusters()[:2]
    assert first.synthesis is None and second.synthesis is None

    result = synthesize_cluster(p.store, p.llm, settings, first.id)
    assert isinstance(result.synthesis, Synthesis) and isinstance(result.skeptic, SkepticReview)
    assert result.synthesis.hypotheses[0].startswith("Hypothesis:")
    saved = p.store.get_cluster(first.id)
    assert saved.synthesis == result.synthesis and saved.skeptic == result.skeptic
    assert p.store.get_cluster(second.id).synthesis is None


def test_unknown_cluster_raises_key_error(settings):
    p = clustered(settings)
    with pytest.raises(KeyError):
        synthesize_cluster(p.store, p.llm, settings, "no-such-cluster")


class FakeAPI:
    def __init__(self, synthesis: dict, skeptic: dict, status: int = 200):
        self.synthesis, self.skeptic, self.status = synthesis, skeptic, status
        self.bodies: list[dict] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        self.bodies.append(body)
        if self.status != 200:
            return httpx2.Response(self.status, json={"type": "error", "error": {
                "type": "invalid_request_error", "message": "bad request"}})
        answer = self.skeptic if "skeptical investor" in body["system"][0]["text"] else self.synthesis
        return httpx2.Response(200, json={
            "id": "msg_1", "type": "message", "role": "assistant", "model": body["model"],
            "content": [{"type": "text", "text": json.dumps(answer)}], "stop_reason": "end_turn",
            "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 5}})


def fake_llm(settings, api: FakeAPI) -> LLM:
    client = anthropic.Anthropic(api_key="test", max_retries=0,
                                 http_client=httpx2.Client(transport=httpx2.MockTransport(api)))
    return LLM(settings, log=lambda _: None, client=client)


SYNTHESIS = {"title": "Re-keying vendor invoices", "summary": "Several firms re-key invoices by hand.",
             "hypotheses": ["Hypothesis: month-end is the worst point."]}


def test_claude_synthesis_grounds_skeptic_citations(settings):
    p = clustered(settings)
    cluster = p.store.clusters()[0]
    real = cluster.signal_ids[0]
    skeptic = {"points": [
        {"claim": "Only two firms say this.", "kind": "evidence", "signal_ids": [real, "not-a-member"]},
        {"claim": "A made-up citation.", "kind": "evidence", "signal_ids": ["not-a-member"]},
        {"claim": "Existing tools may cover it.", "kind": "hypothesis", "signal_ids": []},
    ]}
    api = FakeAPI(SYNTHESIS, skeptic)
    result = synthesize_cluster(p.store, fake_llm(settings, api), settings, cluster.id)

    assert len(api.bodies) == 2 and {b["model"] for b in api.bodies} == {settings.model_synth}
    members = p.store.signals_with_docs(cluster.signal_ids)
    ordered = sorted(members, key=lambda pair: cluster.signal_ids.index(pair[0].id))
    assert all(b["messages"][0]["content"] == evidence_pack(cluster, ordered) for b in api.bodies)

    assert result.synthesis == Synthesis(**SYNTHESIS)
    points = result.skeptic.points
    assert points[0].signal_ids == [real] and points[0].kind == "evidence"
    assert points[1].signal_ids == [] and points[1].kind == "hypothesis"  # uncited evidence is downgraded
    assert p.store.get_cluster(cluster.id).skeptic == result.skeptic


def test_api_failure_raises_llm_error_and_saves_nothing(settings):
    p = clustered(settings)
    cluster = p.store.clusters()[0]
    with pytest.raises(LLMError):
        synthesize_cluster(p.store, fake_llm(settings, FakeAPI(SYNTHESIS, {}, status=400)), settings, cluster.id)
    assert p.store.get_cluster(cluster.id).synthesis is None
