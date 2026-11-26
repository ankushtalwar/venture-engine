"""HTTP API contract v1: request and response models (see docs/api.md).

Rules baked in here: no solution/product/idea fields, no author hashes, and source text goes out as plain
JSON strings (the UI renders it as text, never as HTML).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..models import Evidence, SkepticReview, SpendEvidence, Synthesis

Confidence = Literal["High", "Medium", "Low"]
ClusterLabel = Literal["accept", "reject"]
SignalLabel = Literal["valid", "invalid", "duplicate"]
ClusterTrend = Literal["new", "rising", "falling", "steady"]
TrendStatus = Literal["new", "rising", "falling", "gone"]


class ErrorBody(BaseModel):
    detail: Any


# ---------------------------------------------------------------- health


class Health(BaseModel):
    status: Literal["ok"] = "ok"
    store: Literal["sqlite", "postgres"]
    llm_mock: bool
    version: str


# ---------------------------------------------------------------- clusters


class Components(BaseModel):
    """Score components, each in [0, 1]. `trend` (last 90 vs previous 90 days) is informational, not scored."""
    model_config = ConfigDict(extra="allow")

    breadth: float = 0.0
    spend: float = 0.0
    frequency: float = 0.0
    underserved: float = 0.0
    intensity: float = 0.0
    recency: float = 0.0
    automatability: float = 0.0


class ClusterSummary(BaseModel):
    id: str
    title: str = Field(description="Synthesis title when present, else the shortened canonical statement.")
    canonical: str
    score: float
    confidence: Confidence
    n_signals: int
    n_authors: int
    source_types: list[str]
    first_seen: datetime | None
    last_seen: datetime | None
    components: Components
    label: ClusterLabel | None = Field(description="Human review label; null when unlabelled.")
    trend: ClusterTrend | None = Field(description="Status against the previous comparable run; null without one.")
    has_synthesis: bool


class ClusterPage(BaseModel):
    items: list[ClusterSummary]
    total: int


class QuoteRef(BaseModel):
    model_config = ConfigDict(extra="allow")
    quote: str
    url: str = ""
    label: str = ""


class SpendRef(QuoteRef):
    type: str
    amount: str = ""


class FinancialRef(QuoteRef):
    value: str
    status: Literal["observed", "derived"]
    basis: str = ""


class PricingRef(BaseModel):
    model_config = ConfigDict(extra="allow")
    vendor: str
    tiers: str
    url: str = ""
    label: str = ""


class Card(BaseModel):
    """`report.card()`: the same pain card the Markdown/HTML report shows."""
    model_config = ConfigDict(extra="allow")

    id: str
    title: str
    summary: str = ""
    hypotheses: list[str] = Field(default_factory=list)
    skeptic: list[dict[str, Any]] = Field(default_factory=list)
    canonical: str = ""
    confidence: Confidence
    score: float
    n_signals: int
    n_authors: int
    source_types: list[str] = Field(default_factory=list)
    span: str = ""
    segment: list[str] = Field(default_factory=list, description="'value (status, Nx)' strings.")
    affected_role: list[str] = Field(default_factory=list)
    likely_buyer: list[str] = Field(default_factory=list)
    frequency: str = ""
    frequency_quotes: list[QuoteRef] = Field(default_factory=list)
    financial: list[FinancialRef] = Field(default_factory=list)
    workarounds: list[QuoteRef] = Field(default_factory=list)
    tool_complaints: list[QuoteRef] = Field(default_factory=list)
    spend: list[SpendRef] = Field(default_factory=list)
    evidence: list[QuoteRef] = Field(default_factory=list)
    pricing: list[PricingRef] = Field(default_factory=list, description="Vendor prices on record (vendor_prices).")
    components: dict[str, float] = Field(default_factory=dict)


class SignalFields(BaseModel):
    problem: Evidence
    segment: Evidence
    affected_role: Evidence
    likely_buyer: Evidence
    workaround: Evidence
    occurrence_freq: Evidence
    financial_impact: Evidence
    existing_tool_complaint: Evidence
    automatability: Evidence


class DocumentView(BaseModel):
    id: str
    source: str
    source_type: str
    url: str
    title: str
    published_at: datetime | None


class SignalView(BaseModel):
    id: str
    label: SignalLabel | None
    specificity: Literal["A", "B", "C"]
    intensity: int
    author_type: str
    canonical: str
    fields: SignalFields
    spend_evidence: list[SpendEvidence]
    document: DocumentView


class ClusterDetail(ClusterSummary):
    synthesis: Synthesis | None = Field(description="Model-written; the UI labels it as such.")
    skeptic: SkepticReview | None
    card: Card
    signals: list[SignalView]


class SplitResult(BaseModel):
    clusters: list[ClusterSummary] = Field(
        description="Two clusters: [0] the split-off signals, [1] the remaining signals.")


# ---------------------------------------------------------------- requests


class ClusterLabelIn(BaseModel):
    label: ClusterLabel | None = Field(description="accept, reject, or null to clear.")
    note: str = ""


class SignalLabelIn(BaseModel):
    label: SignalLabel | None = Field(description="valid, invalid, duplicate, or null to clear.")
    note: str = ""


class SignalLabelOut(BaseModel):
    id: str
    label: SignalLabel | None


class MergeIn(BaseModel):
    ids: list[str] = Field(min_length=2, description="Two or more distinct cluster ids.")


class SplitIn(BaseModel):
    signal_ids: list[str] = Field(min_length=1, description="Signals to move into their own cluster.")


# ---------------------------------------------------------------- overrides / trends / sources / stats / reports


class Override(BaseModel):
    id: int
    kind: Literal["merge", "split"]
    payload: dict[str, Any]
    created_at: datetime | str | None = None


class OverrideDeleted(BaseModel):
    id: int
    deleted: bool = True


class TrendItem(BaseModel):
    model_config = ConfigDict(extra="allow")
    cluster_id: str
    title: str = ""
    status: TrendStatus
    score: float | None = None
    previous_score: float | None = None
    delta: float | None = None


class Trends(BaseModel):
    run_at: datetime | str | None = None
    previous_run_at: datetime | str | None = None
    items: list[TrendItem] = Field(default_factory=list)


class SourceRow(BaseModel):
    source: str
    allowed: bool
    route: str
    domains: str
    reviewed: str
    reason: str


class SourceStat(BaseModel):
    model_config = ConfigDict(extra="allow")
    source: str
    docs: int = 0
    duplicates: int = 0
    triaged: int = 0
    pain: int = 0
    signals: int = 0
    dropped: int = 0


class ArmStat(BaseModel):
    model_config = ConfigDict(extra="allow")
    arm_id: str
    source: str | None = None
    query: str | None = None
    runs: int = 0
    empty_runs: int = 0
    docs: int = 0
    verified: int = 0


class Stats(BaseModel):
    sources: list[SourceStat]
    arms: list[ArmStat] = Field(description="Top 20 query arms by verified documents, then documents.")


class ReportFile(BaseModel):
    name: str
    kind: Literal["md", "html"]
    size: int
    modified: datetime
