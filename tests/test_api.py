"""Review UI JSON API (contract v1, docs/api.md) over a store seeded like `paindisc demo`. Offline, mock LLM."""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import FIXTURES, ROOT
from fastapi.testclient import TestClient

from paindisc.api.app import create_app
from paindisc.cluster import apply_overrides, build_clusters
from paindisc.config import load_settings
from paindisc.pipeline import Pipeline
from paindisc.storage import open_store

CONTRACT_PATHS = {
    "/api/health", "/api/clusters", "/api/clusters/{cluster_id}", "/api/clusters/{cluster_id}/label",
    "/api/signals/{signal_id}/label", "/api/clusters/merge", "/api/clusters/{cluster_id}/split",
    "/api/clusters/{cluster_id}/synthesize", "/api/overrides", "/api/overrides/{override_id}", "/api/trends",
    "/api/sources", "/api/stats", "/api/reports", "/api/reports/{name}",
}
STATUSES = {"observed", "inferred", "derived", "unknown"}


def api_settings(tmp_path: Path, db_path: Path):
    s = load_settings(home=ROOT, db_path=db_path)
    s.llm_mock, s.database_url, s.api_token = True, None, None
    s.report_dir = tmp_path / "reports"
    s.snapshot_dir = tmp_path / "snapshots"
    s.frontend_dist = tmp_path / "dist"
    return s


@pytest.fixture(scope="module")
def seeded_db(tmp_path_factory) -> Path:
    """The `paindisc demo` pipeline once per module; each test gets its own copy of the database."""
    tmp = tmp_path_factory.mktemp("seed")
    s = api_settings(tmp, tmp / "seed.sqlite")
    p = Pipeline(s, store=open_store(s), log=lambda _: None, sleep=lambda _: None)
    p.load_fixture(FIXTURES / "demo_docs.jsonl")
    p.triage()
    p.extract()
    p.jobs()
    p.prices()
    p.cluster(threshold=0.75)
    p.synthesize()
    p.store.close()
    return tmp / "seed.sqlite"


@pytest.fixture
def api(tmp_path, seeded_db):
    db = tmp_path / "api.sqlite"
    shutil.copy(seeded_db, db)
    settings = api_settings(tmp_path, db)
    store = open_store(settings)
    with TestClient(create_app(settings, store=store)) as client:
        yield SimpleNamespace(client=client, store=store, settings=settings)
    store.close()


def clusters(api, **params) -> dict:
    r = api.client.get("/api/clusters", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def top_cluster(api) -> dict:
    """The demo's biggest cluster (3 signals)."""
    return max(clusters(api)["items"], key=lambda c: c["n_signals"])


# ------------------------------------------------------------------ read


def test_health(api):
    r = api.client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["store"] == "sqlite" and body["llm_mock"] is True and body["version"]


def test_openapi_lists_every_contract_endpoint_and_no_solution_fields(api):
    spec = api.client.get("/api/openapi.json").json()
    assert CONTRACT_PATHS <= set(spec["paths"])
    names = set()
    for schema in spec["components"]["schemas"].values():
        names |= set(schema.get("properties", {}))
    assert not [n for n in names if re.search(r"solution|product|idea", n, re.I)]
    assert "author_hash" not in names


def test_list_clusters(api):
    page = clusters(api)
    stored = api.store.clusters()
    assert page["total"] == len(stored) == len(page["items"])
    assert [c["id"] for c in page["items"]] == [c.id for c in stored]
    scores = [c["score"] for c in page["items"]]
    assert scores == sorted(scores, reverse=True)
    item = page["items"][0]
    assert set(item) >= {"id", "title", "canonical", "score", "confidence", "n_signals", "n_authors", "source_types",
                         "first_seen", "last_seen", "components", "label", "trend", "has_synthesis"}
    assert set(item["components"]) >= {"breadth", "spend", "frequency", "underserved", "intensity", "recency",
                                       "automatability"}
    assert item["label"] is None and item["has_synthesis"] is True  # the demo synthesizes every cluster
    assert item["title"] == stored[0].synthesis.title


def test_list_pagination_and_filters(api):
    total = clusters(api)["total"]
    assert total >= 3
    first, second = clusters(api, limit=2), clusters(api, limit=2, offset=2)
    assert len(first["items"]) == 2 and first["total"] == total
    assert not {c["id"] for c in first["items"]} & {c["id"] for c in second["items"]}
    assert clusters(api, offset=total)["items"] == []

    for level in ("High", "Medium", "Low"):
        page = clusters(api, confidence=level)
        assert all(c["confidence"] == level for c in page["items"])
    assert sum(clusters(api, confidence=lvl)["total"] for lvl in ("High", "Medium", "Low")) == total

    ids = [c["id"] for c in clusters(api)["items"]]
    api.client.post(f"/api/clusters/{ids[0]}/label", json={"label": "accept"})
    api.client.post(f"/api/clusters/{ids[1]}/label", json={"label": "reject"})
    assert [c["id"] for c in clusters(api, label="accept")["items"]] == [ids[0]]
    assert [c["id"] for c in clusters(api, label="reject")["items"]] == [ids[1]]
    assert clusters(api, label="unlabelled")["total"] == total - 2


@pytest.mark.parametrize("params", [{"confidence": "Huge"}, {"label": "maybe"}, {"limit": 0}, {"limit": 501},
                                    {"offset": -1}, {"limit": "x"}])
def test_list_rejects_bad_params(api, params):
    r = api.client.get("/api/clusters", params=params)
    assert r.status_code == 422 and "detail" in r.json()


def test_cluster_detail(api):
    summary = top_cluster(api)
    r = api.client.get(f"/api/clusters/{summary['id']}")
    assert r.status_code == 200, r.text
    d = r.json()
    assert {k: d[k] for k in summary} == summary  # detail = summary + more
    assert d["synthesis"]["title"] and isinstance(d["synthesis"]["hypotheses"], list)
    assert all(p["kind"] in ("evidence", "hypothesis") for p in d["skeptic"]["points"])
    assert d["card"]["id"] == d["id"] and d["card"]["evidence"]  # report.card(): verified problem quotes
    assert all({"quote", "url", "label"} <= set(q) for q in d["card"]["evidence"])
    assert len(d["signals"]) == d["n_signals"]
    for sig in d["signals"]:
        assert set(sig["fields"]) == {"problem", "segment", "affected_role", "likely_buyer", "workaround",
                                      "occurrence_freq", "financial_impact", "existing_tool_complaint",
                                      "automatability"}
        for ev in sig["fields"].values():
            assert set(ev) == {"value", "status", "quote", "basis"} and ev["status"] in STATUSES
        assert sig["fields"]["problem"]["status"] == "observed"  # verify.py drops anything else
        assert set(sig["document"]) == {"id", "source", "source_type", "url", "title", "published_at"}
        assert sig["label"] is None


def test_unknown_cluster_is_404(api):
    for method, path, body in [("get", "/api/clusters/nope", None),
                               ("post", "/api/clusters/nope/label", {"label": "accept"}),
                               ("post", "/api/clusters/nope/split", {"signal_ids": ["x"]}),
                               ("post", "/api/clusters/nope/synthesize", None)]:
        r = getattr(api.client, method)(path, **({"json": body} if body is not None else {}))
        assert r.status_code == 404, (path, r.text)
        assert "detail" in r.json()


def test_author_hashes_never_leave_the_api(api):
    hashes = {d.author_hash for _, d in api.store.signals_with_docs() if d.author_hash}
    assert hashes
    ids = [c["id"] for c in clusters(api)["items"]]
    bodies = [api.client.get(p).text for p in
              ["/api/clusters", "/api/overrides", "/api/trends", "/api/stats", "/api/sources"]]
    bodies += [api.client.get(f"/api/clusters/{i}").text for i in ids]
    for text in bodies:
        assert "author_hash" not in text
        assert not [h for h in hashes if h in text]


def test_trends_need_two_runs(api):
    body = api.client.get("/api/trends").json()
    assert body["run_at"] and body["previous_run_at"] is None and body["items"] == []
    assert {c["trend"] for c in clusters(api)["items"]} == {None}


def test_trends_against_an_older_run(api):
    """Fake a baseline run 8 days ago: top cluster scored lower, one cluster absent, one cluster since gone."""
    current = api.store.clusters()
    top, absent = current[0], current[-1]
    old_top = top.model_copy(update={"score": top.score - 0.2})
    gone = absent.model_copy(update={"id": "oldgone00000", "signal_ids": ["no-such-signal"], "score": 0.3})
    baseline = [old_top, *current[1:-1], gone]
    api.store.replace_clusters(baseline, snapshot=True, run_at=datetime.now(timezone.utc) - timedelta(days=8))
    api.store.replace_clusters(current, snapshot=False)  # back to the live clusters; the latest run is the seed run

    body = api.client.get("/api/trends").json()
    assert body["run_at"] and body["previous_run_at"] and body["previous_run_at"] < body["run_at"]
    by_status = {i["status"]: i for i in body["items"]}
    assert set(by_status) == {"rising", "new", "gone"}  # steady clusters are not listed
    assert by_status["rising"]["cluster_id"] == top.id and by_status["rising"]["delta"] == pytest.approx(0.2)
    assert by_status["new"]["cluster_id"] == absent.id and by_status["new"]["previous_score"] is None
    assert by_status["gone"]["cluster_id"] == "oldgone00000" and by_status["gone"]["score"] is None

    page = {c["id"]: c for c in clusters(api)["items"]}
    for status in ("rising", "new"):  # trend rows show the same title as the card they link to
        assert by_status[status]["title"] == page[by_status[status]["cluster_id"]]["title"]
    trend = {cid: c["trend"] for cid, c in page.items()}
    assert trend[top.id] == "rising" and trend[absent.id] == "new"
    assert {trend[c.id] for c in current[1:-1]} == {"steady"}
    assert api.client.get(f"/api/clusters/{top.id}").json()["trend"] == "rising"


def test_sources_and_stats(api):
    rows = api.client.get("/api/sources").json()
    by_name = {r["source"]: r for r in rows}
    assert by_name["hn"]["allowed"] is True and by_name["reddit"]["allowed"] is False
    assert set(rows[0]) == {"source", "allowed", "route", "domains", "reviewed", "reason"}

    stats = api.client.get("/api/stats").json()
    assert stats["sources"] and all(isinstance(s["docs"], int) for s in stats["sources"])
    assert sum(s["signals"] for s in stats["sources"]) == len(api.store.signals_with_docs())
    assert isinstance(stats["arms"], list) and len(stats["arms"]) <= 20


# ------------------------------------------------------------------ labels


def test_cluster_label_set_and_clear(api):
    cid = top_cluster(api)["id"]
    r = api.client.post(f"/api/clusters/{cid}/label", json={"label": "accept", "note": "worth interviews"})
    assert r.status_code == 200 and r.json()["label"] == "accept" and r.json()["id"] == cid
    assert api.store.get_label("cluster", cid)["note"] == "worth interviews"
    assert api.client.get(f"/api/clusters/{cid}").json()["label"] == "accept"

    assert api.client.post(f"/api/clusters/{cid}/label", json={"label": "reject"}).json()["label"] == "reject"
    r = api.client.post(f"/api/clusters/{cid}/label", json={"label": None})
    assert r.status_code == 200 and r.json()["label"] is None
    assert api.store.get_label("cluster", cid) is None


@pytest.mark.parametrize("body", [{"label": "maybe"}, {}, {"label": "valid"}, {"label": "accept", "note": 5}])
def test_cluster_label_validation(api, body):
    cid = top_cluster(api)["id"]
    assert api.client.post(f"/api/clusters/{cid}/label", json=body).status_code == 422


def test_signal_label_set_and_clear(api):
    sid = api.store.signals_with_docs()[0][0].id
    r = api.client.post(f"/api/signals/{sid}/label", json={"label": "duplicate"})
    assert r.status_code == 200 and r.json() == {"id": sid, "label": "duplicate"}
    cid = next(c.id for c in api.store.clusters() if sid in c.signal_ids)
    detail = api.client.get(f"/api/clusters/{cid}").json()
    assert next(s for s in detail["signals"] if s["id"] == sid)["label"] == "duplicate"

    assert api.client.post(f"/api/signals/{sid}/label", json={"label": None}).json() == {"id": sid, "label": None}
    assert api.store.get_label("signal", sid) is None
    assert api.client.post("/api/signals/nope/label", json={"label": "valid"}).status_code == 404
    assert api.client.post(f"/api/signals/{sid}/label", json={"label": "accept"}).status_code == 422


# ------------------------------------------------------------------ merge / split / synthesize


def test_merge(api):
    a, b = clusters(api)["items"][:2]
    r = api.client.post("/api/clusters/merge", json={"ids": [a["id"], b["id"]]})
    assert r.status_code == 200, r.text
    merged = r.json()
    assert merged["id"] not in (a["id"], b["id"])
    assert merged["n_signals"] == a["n_signals"] + b["n_signals"] == len(merged["signals"])
    assert merged["synthesis"] is None and merged["skeptic"] is None and merged["has_synthesis"] is False
    assert merged["label"] is None
    for old in (a["id"], b["id"]):
        assert api.client.get(f"/api/clusters/{old}").status_code == 404
    page = clusters(api)
    assert merged["id"] in {c["id"] for c in page["items"]}
    assert page["total"] == len(api.store.clusters())

    overrides = api.client.get("/api/overrides").json()
    assert len(overrides) == 1 and overrides[0]["kind"] == "merge"
    assert sorted(overrides[0]["payload"]["signal_ids"]) == sorted(s["id"] for s in merged["signals"])


def test_merge_errors(api):
    ids = [c["id"] for c in clusters(api)["items"]]
    assert api.client.post("/api/clusters/merge", json={"ids": [ids[0], ids[0]]}).status_code == 400
    assert api.client.post("/api/clusters/merge", json={"ids": [ids[0]]}).status_code == 422
    assert api.client.post("/api/clusters/merge", json={}).status_code == 422
    r = api.client.post("/api/clusters/merge", json={"ids": [ids[0], "nope"]})
    assert r.status_code == 404 and "nope" in r.json()["detail"]
    assert api.client.get("/api/overrides").json() == []
    assert len(api.store.clusters()) == len(ids)  # nothing changed


def test_split(api):
    top = api.client.get(f"/api/clusters/{top_cluster(api)['id']}").json()
    moved = top["signals"][-1]["id"]
    r = api.client.post(f"/api/clusters/{top['id']}/split", json={"signal_ids": [moved]})
    assert r.status_code == 200, r.text
    off, rest = r.json()["clusters"]
    assert off["n_signals"] == 1 and rest["n_signals"] == top["n_signals"] - 1
    assert not off["has_synthesis"] and not rest["has_synthesis"]
    assert api.client.get(f"/api/clusters/{top['id']}").status_code == 404
    assert api.client.get(f"/api/clusters/{off['id']}").json()["signals"][0]["id"] == moved
    overrides = api.client.get("/api/overrides").json()
    assert [(o["kind"], o["payload"]["signal_ids"]) for o in overrides] == [("split", [moved])]


def test_split_errors(api):
    top = api.client.get(f"/api/clusters/{top_cluster(api)['id']}").json()
    url = f"/api/clusters/{top['id']}/split"
    other = next(c for c in api.store.clusters() if c.id != top["id"]).signal_ids[0]
    assert api.client.post(url, json={"signal_ids": [other]}).status_code == 400  # not in this cluster
    assert api.client.post(url, json={"signal_ids": [s["id"] for s in top["signals"]]}).status_code == 400  # all
    assert api.client.post(url, json={"signal_ids": []}).status_code == 422
    assert api.client.get("/api/overrides").json() == []


def test_synthesize(api):
    a, b = clusters(api)["items"][:2]
    merged = api.client.post("/api/clusters/merge", json={"ids": [a["id"], b["id"]]}).json()
    r = api.client.post(f"/api/clusters/{merged['id']}/synthesize")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["id"] == merged["id"] and d["has_synthesis"] is True
    assert d["synthesis"]["title"] and d["skeptic"] is not None and d["title"] == d["synthesis"]["title"]
    assert api.store.get_cluster(merged["id"]).synthesis is not None


def test_synthesize_model_failure_is_502(api, monkeypatch):
    from paindisc import synth
    from paindisc.llm import LLMError

    def boom(*_args, **_kwargs):
        raise LLMError("overloaded")

    monkeypatch.setattr(synth, "synthesize_cluster", boom)
    r = api.client.post(f"/api/clusters/{top_cluster(api)['id']}/synthesize")
    assert r.status_code == 502 and "overloaded" in r.json()["detail"]


def test_overrides_delete(api):
    a, b = clusters(api)["items"][:2]
    api.client.post("/api/clusters/merge", json={"ids": [a["id"], b["id"]]})
    [override] = api.client.get("/api/overrides").json()
    assert isinstance(override["id"], int) and override["created_at"]
    r = api.client.delete(f"/api/overrides/{override['id']}")
    assert r.status_code == 200 and r.json() == {"id": override["id"], "deleted": True}
    assert api.client.get("/api/overrides").json() == []
    assert api.client.delete(f"/api/overrides/{override['id']}").status_code == 404
    assert api.client.delete("/api/overrides/abc").status_code == 422


def test_merge_and_split_survive_reclustering(api):
    """A fresh automatic clustering of the same signals, plus the stored overrides, keeps the reviewer's edits."""
    items = clusters(api)["items"]
    small = [c for c in items if c["n_signals"] == 1][:2]
    merged = api.client.post("/api/clusters/merge", json={"ids": [c["id"] for c in small]}).json()
    top = api.client.get(f"/api/clusters/{top_cluster(api)['id']}").json()
    moved = top["signals"][0]["id"]
    api.client.post(f"/api/clusters/{top['id']}/split", json={"signal_ids": [moved]})
    before = {frozenset(c.signal_ids) for c in api.store.clusters()}

    cfg = api.settings.load_yaml("scoring.yaml")
    pairs = api.store.signals_with_docs()
    fresh = build_clusters(pairs, 0.75, cfg.get("weights"), cfg.get("confidence"))
    assert {frozenset(c.signal_ids) for c in fresh} != before  # automatic clustering alone loses the edits
    out = apply_overrides(fresh, api.store.overrides(), pairs, cfg.get("weights"), cfg.get("confidence"))
    after = {frozenset(c.signal_ids) for c in out}
    assert frozenset(s["id"] for s in merged["signals"]) in after
    assert frozenset([moved]) in after
    assert after == before


# ------------------------------------------------------------------ reports


def test_reports_list_and_serve(api):
    md, html = Pipeline(api.settings, store=api.store, log=lambda _: None).report(demo=True, stem="demo")
    (api.settings.report_dir / "notes.txt").write_text("not a report")
    (api.settings.report_dir / ".hidden.md").write_text("hidden")
    rows = api.client.get("/api/reports").json()
    assert sorted((r["name"], r["kind"]) for r in rows) == [("demo.html", "html"), ("demo.md", "md")]
    assert all(r["size"] > 0 and r["modified"] for r in rows)

    r = api.client.get("/api/reports/demo.md")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/markdown")
    assert r.text == md.read_text()
    r = api.client.get("/api/reports/demo.html")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert "sandbox" in r.headers["content-security-policy"] and r.headers["x-content-type-options"] == "nosniff"


def test_reports_empty_without_dir(api):
    assert api.client.get("/api/reports").json() == []


@pytest.mark.parametrize("path", ["/api/reports/..%2Fsecret.md", "/api/reports/%2e%2e%2fsecret.md",
                                  "/api/reports/..", "/api/reports/.hidden.md", "/api/reports/notes.txt",
                                  "/api/reports/link.md", "/api/reports/missing.md",
                                  "/api/reports/..%5Csecret.md"])
def test_report_path_traversal(api, path):
    root = api.settings.report_dir
    root.mkdir(parents=True)
    secret = root.parent / "secret.md"
    secret.write_text("TOP SECRET")
    (root / ".hidden.md").write_text("TOP SECRET")
    (root / "notes.txt").write_text("TOP SECRET")
    (root / "link.md").symlink_to(secret)
    r = api.client.get(path)
    assert r.status_code == 404 and "TOP SECRET" not in r.text
    assert "link.md" not in {row["name"] for row in api.client.get("/api/reports").json()}


# ------------------------------------------------------------------ SPA + auth


def test_spa_fallback(api):
    dist = api.settings.frontend_dist
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><div id=root></div>")
    (dist / "assets" / "app.js").write_text("console.log('app')")
    (dist.parent / "secret.txt").write_text("TOP SECRET")

    for path in ("/", "/clusters/abc123", "/review?x=1", "/%2e%2e/secret.txt"):
        r = api.client.get(path)
        assert r.status_code == 200 and "id=root" in r.text and "TOP SECRET" not in r.text, path
    r = api.client.get("/assets/app.js")
    assert r.status_code == 200 and "console.log" in r.text
    r = api.client.get("/api/does-not-exist")
    assert r.status_code == 404 and r.headers["content-type"].startswith("application/json")
    assert api.client.get("/api").status_code == 404


def test_spa_missing_build_is_404(api):
    r = api.client.get("/")
    assert r.status_code == 404 and "frontend build not found" in r.json()["detail"]


def test_auth_only_guards_writes(api):
    api.settings.api_token = "s3cret"
    cid = top_cluster(api)["id"]
    url = f"/api/clusters/{cid}/label"
    for headers in ({}, {"Authorization": "Bearer wrong"}, {"Authorization": "Basic s3cret"},
                    {"Authorization": "s3cret"}):
        r = api.client.post(url, json={"label": "accept"}, headers=headers)
        assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer", headers
    assert api.client.delete("/api/overrides/1").status_code == 401
    assert api.client.post("/api/clusters/merge", json={"ids": ["a", "b"]}).status_code == 401
    assert api.store.get_label("cluster", cid) is None

    ok = {"Authorization": "Bearer s3cret"}
    assert api.client.post(url, json={"label": "accept"}, headers=ok).status_code == 200
    assert api.client.delete("/api/overrides/999", headers=ok).status_code == 404
    for path in ("/api/health", "/api/clusters", f"/api/clusters/{cid}", "/api/overrides", "/api/trends",
                 "/api/sources", "/api/stats", "/api/reports"):
        assert api.client.get(path).status_code == 200, path


def test_auth_off_by_default(api):
    cid = top_cluster(api)["id"]
    assert api.client.post(f"/api/clusters/{cid}/label", json={"label": "reject"}).status_code == 200


def test_cors_allows_vite_dev_origin(api):
    r = api.client.options("/api/clusters/merge", headers={"Origin": "http://localhost:5173",
                                                          "Access-Control-Request-Method": "POST"})
    assert r.headers.get("access-control-allow-origin") == "http://localhost:5173"
    r = api.client.get("/api/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in r.headers


def test_source_text_is_plain_json_strings(api):
    """Quotes come back as JSON strings exactly as stored; the API never turns them into markup."""
    cid = top_cluster(api)["id"]
    d = api.client.get(f"/api/clusters/{cid}").json()
    stored = {s.id: s for s, _ in api.store.signals_with_docs()}
    for sig in d["signals"]:
        assert sig["fields"]["problem"]["quote"] == stored[sig["id"]].problem.quote
    assert json.loads(json.dumps(d)) == d


def test_merge_and_split_survive_a_pipeline_run(api):
    """The real `paindisc cluster` step re-applies the reviewer's edits and keeps their write-ups."""
    items = clusters(api)["items"]
    small = [c for c in items if c["n_signals"] == 1][:2]
    merged = api.client.post("/api/clusters/merge", json={"ids": [c["id"] for c in small]}).json()
    api.client.post(f"/api/clusters/{merged['id']}/synthesize")
    top = api.client.get(f"/api/clusters/{top_cluster(api)['id']}").json()
    moved = top["signals"][0]["id"]
    api.client.post(f"/api/clusters/{top['id']}/split", json={"signal_ids": [moved]})
    untouched = {c.id for c in api.store.clusters() if c.synthesis is not None} - {merged["id"]}
    before = {frozenset(c.signal_ids) for c in api.store.clusters()}

    Pipeline(api.settings, store=api.store, log=lambda _: None).cluster(threshold=0.75)

    after = {c.id: c for c in api.store.clusters()}
    assert {frozenset(c.signal_ids) for c in after.values()} == before
    assert after[merged["id"]].synthesis is not None  # same membership -> same id -> write-up carried over
    assert all(after[cid].synthesis is not None for cid in untouched)
    page = clusters(api)
    assert merged["id"] in {c["id"] for c in page["items"]}
