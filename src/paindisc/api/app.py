"""FastAPI app for the review UI: JSON API under /api, plus the built SPA at / (contract: docs/api.md)."""

from __future__ import annotations

import logging
import os
import re
import secrets
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..cluster import merge_clusters, split_cluster
from ..config import Settings, load_settings
from ..models import Cluster, Document, PainSignal
from ..registry import Registry
from ..report import card as build_card, latest_prices
from ..storage import open_store
from ..store import cluster_title
from ..trends import compute_trends
from . import schemas as S

log = logging.getLogger("paindisc.api")

DEV_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]  # Vite dev server (it also proxies /api)
REPORT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.(md|html)$")
# Reports embed text fetched from the web. Serve them inert: no scripts, no same-origin access to the API.
REPORT_CSP = ("default-src 'none'; style-src 'unsafe-inline'; img-src data:; "
              "sandbox allow-popups allow-popups-to-escape-sandbox")
SIGNAL_FIELDS = tuple(S.SignalFields.model_fields)


def _version() -> str:
    try:
        return package_version("venture-engine")
    except PackageNotFoundError:  # pragma: no cover - running from a bare checkout
        return "0.0.0"


def store_kind(store) -> Literal["sqlite", "postgres"]:
    backend = getattr(store, "backend", None)
    if backend in ("sqlite", "postgres"):
        return backend
    return "postgres" if "postgres" in type(store).__name__.lower() else "sqlite"


def create_app(settings: Settings, store=None) -> FastAPI:
    owns_store = store is None
    store = open_store(settings) if store is None else store

    @asynccontextmanager
    async def lifespan(app_: FastAPI):
        yield
        llm = app_.state.llm
        if llm is not None and hasattr(llm, "close"):
            llm.close()
        if owns_store:
            store.close()

    app = FastAPI(title="paindisc review API", version=_version(), lifespan=lifespan,
                  openapi_url="/api/openapi.json", docs_url="/api/docs", redoc_url=None,
                  description="Review UI API for the Pain Discovery Agent. Finds problems, not solutions.")
    app.add_middleware(CORSMiddleware, allow_origins=DEV_ORIGINS, allow_methods=["GET", "POST", "DELETE"],
                       allow_headers=["Authorization", "Content-Type"])
    app.state.settings, app.state.store = settings, store
    app.state.write_lock = threading.RLock()  # merge/split/synthesize rewrite clusters; one writer at a time
    app.state.llm = None
    app.include_router(_router(app))
    _mount_spa(app, settings)
    return app


# ---------------------------------------------------------------------------- helpers


def _router(app: FastAPI) -> APIRouter:
    settings: Settings = app.state.settings
    store = app.state.store
    lock: threading.RLock = app.state.write_lock
    bearer = HTTPBearer(auto_error=False, description="Needed for POST/DELETE when PAINDISC_API_TOKEN is set.")

    def require_token(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> None:
        token = settings.api_token
        if not token:
            return
        given = credentials.credentials if credentials and credentials.scheme.lower() == "bearer" else ""
        if not secrets.compare_digest(given.encode(), token.encode()):
            raise HTTPException(401, "missing or invalid bearer token", headers={"WWW-Authenticate": "Bearer"})

    write = [Depends(require_token)]
    errors = {code: {"model": S.ErrorBody} for code in (400, 401, 404, 422)}
    router = APIRouter(prefix="/api", responses={404: {"model": S.ErrorBody}})

    def scoring() -> tuple[dict | None, dict | None]:
        cfg = settings.load_yaml("scoring.yaml")
        return cfg.get("weights"), cfg.get("confidence")

    def trends_raw() -> dict:
        """Latest clustering run vs the baseline run (trends.compute_trends; snapshots come from pipeline runs)."""
        return compute_trends(store, settings.load_yaml("scoring.yaml").get("trends") or {})

    def trend_map() -> dict[str, str]:
        """cluster id -> new/rising/falling/steady for clusters in the latest run. Clusters made by a manual
        merge or split since then aren't in it, so their trend is null."""
        allowed = {"new", "rising", "falling", "steady"}
        return {cid: status for cid, status in (trends_raw().get("status_by_cluster") or {}).items()
                if status in allowed}

    def cluster_labels() -> dict[str, str]:
        return {k: v for k, v in store.labels("cluster").items() if v in ("accept", "reject")}

    def get_cluster(cluster_id: str) -> Cluster:
        cluster = store.get_cluster(cluster_id)
        if cluster is None:
            raise HTTPException(404, f"unknown cluster {cluster_id}")
        return cluster

    def summary(c: Cluster, labels: dict[str, str], trends: dict[str, str]) -> S.ClusterSummary:
        return S.ClusterSummary(
            id=c.id, title=cluster_title(c), canonical=c.canonical,
            score=c.score, confidence=c.confidence, n_signals=c.n_signals, n_authors=c.n_authors,
            source_types=c.source_types, first_seen=c.first_seen, last_seen=c.last_seen,
            components=S.Components(**c.components), label=labels.get(c.id), trend=trends.get(c.id),
            has_synthesis=c.synthesis is not None)

    def signal_view(s: PainSignal, d: Document, label: str | None) -> S.SignalView:
        return S.SignalView(
            id=s.id, label=label if label in ("valid", "invalid", "duplicate") else None,
            specificity=s.specificity, intensity=s.intensity, author_type=s.author_type, canonical=s.canonical,
            fields=S.SignalFields(**{name: getattr(s, name) for name in SIGNAL_FIELDS}),
            spend_evidence=s.spend_evidence,
            document=S.DocumentView(id=d.id, source=d.source, source_type=d.source_type, url=d.url, title=d.title,
                                    published_at=d.published_at))  # never author_hash

    def detail(c: Cluster) -> S.ClusterDetail:
        members = store.signals_with_docs(c.signal_ids) if c.signal_ids else []
        members.sort(key=lambda m: (m[0].specificity, -m[0].intensity, m[0].id))
        signal_labels = store.labels("signal")
        card = build_card(c, members, latest_prices(store.prices_with_docs()))
        base = summary(c, cluster_labels(), trend_map())
        return S.ClusterDetail(**base.model_dump(), synthesis=c.synthesis, skeptic=c.skeptic, card=card,
                               signals=[signal_view(s, d, signal_labels.get(s.id)) for s, d in members])

    def llm():
        if app.state.llm is None:
            from ..llm import LLM
            app.state.llm = LLM(settings, log=log.info)
        return app.state.llm

    # ------------------------------------------------------------------------ read

    @router.get("/health", response_model=S.Health)
    def health():
        return S.Health(store=store_kind(store), llm_mock=bool(settings.llm_mock), version=_version())

    @router.get("/clusters", response_model=S.ClusterPage)
    def list_clusters(limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0),
                      confidence: S.Confidence | None = None,
                      label: Literal["accept", "reject", "unlabelled"] | None = None):
        labels, trends = cluster_labels(), trend_map()
        rows = store.clusters()  # highest score first
        if confidence:
            rows = [c for c in rows if c.confidence == confidence]
        if label == "unlabelled":
            rows = [c for c in rows if c.id not in labels]
        elif label:
            rows = [c for c in rows if labels.get(c.id) == label]
        return S.ClusterPage(items=[summary(c, labels, trends) for c in rows[offset: offset + limit]],
                             total=len(rows))

    @router.get("/clusters/{cluster_id}", response_model=S.ClusterDetail)
    def get_cluster_detail(cluster_id: str):
        return detail(get_cluster(cluster_id))

    @router.get("/overrides", response_model=list[S.Override])
    def list_overrides():
        return store.overrides()

    @router.get("/trends", response_model=S.Trends)
    def get_trends():
        raw = trends_raw()
        items = [i for i in raw.get("items") or [] if i.get("status") != "steady"]
        return S.Trends(run_at=raw.get("run_at"), previous_run_at=raw.get("previous_run_at"), items=items)

    @router.get("/sources", response_model=list[S.SourceRow])
    def sources():
        return Registry.load(settings.config_dir).summary()

    @router.get("/stats", response_model=S.Stats)
    def stats():
        arms = sorted(store.arm_stats().values(), key=lambda a: (a["verified"] or 0, a["docs"] or 0), reverse=True)
        sources = [{k: 0 if v is None and k != "source" else v for k, v in row.items()} for row in store.source_stats()]
        return S.Stats(sources=sources, arms=arms[:20])

    def report_path(name: str) -> Path | None:
        """A report file directly inside report_dir, or None. No traversal, no hidden files, no symlinks out."""
        if not REPORT_NAME.match(name):
            return None
        root = settings.report_dir.resolve()
        path = (root / name).resolve()
        return path if path.parent == root and path.is_file() else None

    @router.get("/reports", response_model=list[S.ReportFile])
    def list_reports():
        root = settings.report_dir
        if not root.is_dir():
            return []
        rows = []
        for entry in root.iterdir():
            path = report_path(entry.name)
            if path is not None:
                st = path.stat()
                rows.append(S.ReportFile(name=entry.name, kind=entry.suffix[1:], size=st.st_size,
                                         modified=datetime.fromtimestamp(st.st_mtime, timezone.utc)))
        rows.sort(key=lambda r: (r.modified, r.name), reverse=True)
        return rows

    @router.get("/reports/{name}", response_class=FileResponse,
                responses={200: {"content": {"text/markdown": {}, "text/html": {}}}})
    def get_report(name: str):
        path = report_path(name)
        if path is None:
            raise HTTPException(404, "no such report")
        html = name.endswith(".html")
        headers = {"X-Content-Type-Options": "nosniff"}
        if html:
            headers["Content-Security-Policy"] = REPORT_CSP
        return FileResponse(path, media_type="text/html; charset=utf-8" if html else "text/markdown; charset=utf-8",
                            headers=headers)

    # ------------------------------------------------------------------------ write

    @router.post("/clusters/{cluster_id}/label", response_model=S.ClusterSummary, dependencies=write,
                 responses=errors)
    def label_cluster(cluster_id: str, body: S.ClusterLabelIn):
        with lock:
            cluster = get_cluster(cluster_id)
            if body.label is None:
                store.delete_label("cluster", cluster_id)
            else:
                store.set_label("cluster", cluster_id, body.label, body.note)
            return summary(cluster, cluster_labels(), trend_map())

    @router.post("/signals/{signal_id}/label", response_model=S.SignalLabelOut, dependencies=write,
                 responses=errors)
    def label_signal(signal_id: str, body: S.SignalLabelIn):
        with lock:
            if not store.signals_with_docs([signal_id]):
                raise HTTPException(404, f"unknown signal {signal_id}")
            if body.label is None:
                store.delete_label("signal", signal_id)
            else:
                store.set_label("signal", signal_id, body.label, body.note)
            return S.SignalLabelOut(id=signal_id, label=body.label)

    @router.post("/clusters/merge", response_model=S.ClusterDetail, dependencies=write, responses=errors)
    def merge(body: S.MergeIn):
        ids = list(dict.fromkeys(body.ids))
        if len(ids) < 2:
            raise HTTPException(400, "merge needs at least two distinct cluster ids")
        with lock:
            clusters = [store.get_cluster(i) for i in ids]
            missing = [i for i, c in zip(ids, clusters) if c is None]
            if missing:
                raise HTTPException(404, f"unknown cluster(s): {', '.join(missing)}")
            pairs = store.signals_with_docs(sorted({sid for c in clusters for sid in c.signal_ids}))
            try:
                merged = merge_clusters(clusters, pairs, *scoring())
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
            gone = set(ids)
            store.replace_clusters([c for c in store.clusters() if c.id not in gone] + [merged], snapshot=False)
            store.add_override("merge", {"signal_ids": merged.signal_ids})
            return detail(merged)

    @router.post("/clusters/{cluster_id}/split", response_model=S.SplitResult, dependencies=write, responses=errors)
    def split(cluster_id: str, body: S.SplitIn):
        with lock:
            cluster = get_cluster(cluster_id)
            try:
                moved, rest = split_cluster(cluster, list(dict.fromkeys(body.signal_ids)),
                                            store.signals_with_docs(cluster.signal_ids), *scoring())
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
            store.replace_clusters([c for c in store.clusters() if c.id != cluster.id] + [moved, rest],
                                   snapshot=False)
            store.add_override("split", {"signal_ids": moved.signal_ids})
            labels, trends = cluster_labels(), trend_map()
            return S.SplitResult(clusters=[summary(moved, labels, trends), summary(rest, labels, trends)])

    @router.post("/clusters/{cluster_id}/synthesize", response_model=S.ClusterDetail, dependencies=write,
                 responses={**errors, 502: {"model": S.ErrorBody}})
    def synthesize(cluster_id: str):
        from ..llm import LLMError
        from ..synth import synthesize_cluster
        with lock:  # held across the LLM call so a concurrent merge/split can't be overwritten by a stale save
            get_cluster(cluster_id)
            try:
                updated = synthesize_cluster(store, llm(), settings, cluster_id)
            except KeyError as exc:
                raise HTTPException(404, f"unknown cluster {cluster_id}") from exc
            except LLMError as exc:  # the model call failed; nothing was saved
                log.exception("synthesis failed for %s", cluster_id)
                raise HTTPException(502, f"synthesis failed: {type(exc).__name__}: {exc}") from exc
            return detail(updated)

    @router.delete("/overrides/{override_id}", response_model=S.OverrideDeleted, dependencies=write,
                   responses=errors)
    def delete_override(override_id: int):
        with lock:
            if not store.delete_override(override_id):
                raise HTTPException(404, f"unknown override {override_id}")
            return S.OverrideDeleted(id=override_id)

    return router


def _mount_spa(app: FastAPI, settings: Settings) -> None:
    """Serve the built frontend (frontend/dist) at /, falling back to index.html for client-side routes."""

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        if full_path == "api" or full_path.startswith("api/"):
            raise HTTPException(404, "not found")
        dist = settings.frontend_dist
        index = dist / "index.html" if dist else None
        if index is None or not index.is_file():
            raise HTTPException(404, "frontend build not found (cd frontend && npm run build)")
        root = dist.resolve()
        if full_path:
            candidate = (root / full_path).resolve()
            if candidate.is_relative_to(root) and candidate.is_file():
                return FileResponse(candidate)
        return FileResponse(index, headers={"Cache-Control": "no-cache"})


# ---------------------------------------------------------------------------- entry points


def _settings_env(settings: Settings) -> dict[str, str]:
    """Settings that must survive into uvicorn's reload worker process (it rebuilds the app from env)."""
    env = {"PAINDISC_HOME": str(settings.home)}
    if settings.database_url:
        env["DATABASE_URL"] = settings.database_url
    else:
        env["PAINDISC_API_DB"] = str(settings.db_path)
    if settings.llm_mock:
        env["LLM_MOCK"] = "true"
    if settings.api_token:
        env["PAINDISC_API_TOKEN"] = settings.api_token
    if settings.frontend_dist:
        env["PAINDISC_FRONTEND_DIST"] = str(settings.frontend_dist)
    return env


def app_from_env() -> FastAPI:
    """uvicorn factory used with --reload."""
    db = os.environ.get("PAINDISC_API_DB")
    settings = load_settings(home=os.environ.get("PAINDISC_HOME"), db_path=db or None)
    if db:
        settings.database_url = None
    return create_app(settings)


def run(settings: Settings, host: str | None = None, port: int | None = None, reload: bool = False) -> None:
    import uvicorn

    host, port = host or settings.api_host, port or settings.api_port
    if reload:
        os.environ.update(_settings_env(settings))
        uvicorn.run("paindisc.api.app:app_from_env", factory=True, host=host, port=port, reload=True,
                    reload_dirs=[str(Path(__file__).resolve().parents[1])])
    else:
        uvicorn.run(create_app(settings), host=host, port=port)
