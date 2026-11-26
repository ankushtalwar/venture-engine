from __future__ import annotations

import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from paindisc.config import load_settings
from paindisc.models import Document, Evidence, PainSignal, SpendEvidence, Status
from paindisc.pipeline import Pipeline
from paindisc.store import Store

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_MOCK", "true")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    s = load_settings(home=ROOT, db_path=tmp_path / "test.sqlite")
    s.llm_mock = True
    s.report_dir = tmp_path / "reports"
    s.snapshot_dir = tmp_path / "snapshots"
    return s


@pytest.fixture
def config_copy(tmp_path) -> Path:
    target = tmp_path / "config"
    shutil.copytree(ROOT / "config", target)
    return target


def make_pipeline(settings, handler=None) -> Pipeline:
    transport = httpx.MockTransport(handler) if handler else None
    return Pipeline(settings, store=Store(settings.db_path), transport=transport, log=lambda _: None,
                    sleep=lambda _: None)


def ev(value: str = "", status: str = "unknown", quote: str = "", basis: str = "") -> Evidence:
    return Evidence(value=value, status=Status(status), quote=quote, basis=basis)


def make_pair(author: str, source_type: str = "forum", days_ago: int = 10, spend: bool = False,
              text: str = "We re-key invoices by hand every week.") -> tuple[PainSignal, Document]:
    published = datetime.now(timezone.utc) - timedelta(days=days_ago)
    doc = Document(id=f"d-{author}-{source_type}-{days_ago}", source="test", source_type=source_type, route="fixture",
                   external_id=author, url=f"https://example.com/{author}", text=text, author_hash=author,
                   published_at=published, captured_at=published)
    signal = PainSignal(
        id=f"s-{doc.id}", document_id=doc.id,
        problem=ev("re-keying invoices", "observed", "We re-key invoices by hand"),
        segment=ev(), affected_role=ev(), likely_buyer=ev(), workaround=ev("by hand", "observed", "by hand"),
        occurrence_freq=ev("every week", "observed", "every week"), financial_impact=ev(),
        spend_evidence=[SpendEvidence(type="freelancer", amount="$15/hr", quote="we pay a VA $15/hr")] if spend else [],
        existing_tool_complaint=ev(), automatability=ev("high", "inferred", basis="structured"),
        specificity="A", author_type="operator", intensity=2, canonical="firms must re-key invoices",
    )
    return signal, doc
