"""Data contract for the pipeline.

LLM-facing models (TriageResult, ExtractionResult, JobExtraction, PriceExtraction,
Synthesis, SkepticReview) use only required fields and empty strings instead of
nulls, which keeps them friendly to structured-output JSON schemas.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class Status(str, Enum):
    observed = "observed"  # backed by a verbatim quote from the source document
    inferred = "inferred"  # model hypothesis, labelled as such
    derived = "derived"  # financial only: stated quantity x stated/cited rate, formula in basis
    unknown = "unknown"


class Evidence(BaseModel):
    value: str = Field(description="The value in plain words, or empty string.")
    status: Status
    quote: str = Field(
        description="Verbatim span copied character-for-character from the document. "
        "Required when status is observed; empty string otherwise."
    )
    basis: str = Field(
        description="For inferred: one-sentence reason. For derived: formula and source of the rate. Else empty."
    )

    @classmethod
    def unknown(cls) -> "Evidence":
        return cls(value="", status=Status.unknown, quote="", basis="")

    @property
    def known(self) -> bool:
        return self.status != Status.unknown and bool(self.value)


SpendType = Literal["software", "staff", "freelancer", "service", "penalty", "other"]


class SpendEvidence(BaseModel):
    type: SpendType
    amount: str = Field(description="Amount exactly as written in the quote, or empty string.")
    quote: str = Field(description="Verbatim span showing money or people already spent on the problem.")


# ---------------------------------------------------------------- triage


class TriageResult(BaseModel):
    is_pain: bool = Field(description="True only if the text describes a recurring problem a business experiences.")
    kind: Literal["business_pain", "consumer", "job_seeking", "vendor_promo", "question_only", "other"]
    reason: str = Field(description="One short sentence.")


# ---------------------------------------------------------------- extraction


class ExtractedSignal(BaseModel):
    problem: Evidence
    segment: Evidence
    affected_role: Evidence
    likely_buyer: Evidence
    workaround: Evidence
    occurrence_freq: Evidence
    financial_impact: Evidence
    spend_evidence: list[SpendEvidence]
    existing_tool_complaint: Evidence
    automatability: Evidence
    specificity: Literal["A", "B", "C"]
    author_type: Literal["operator", "employee", "consultant", "vendor", "unknown"]
    intensity: Literal[1, 2, 3]
    canonical: str = Field(
        description="One sentence: '[segment] must [job] because [cause]; today they [workaround]'. No solutions."
    )


class ExtractionResult(BaseModel):
    signals: list[ExtractedSignal]


class PainSignal(ExtractedSignal):
    id: str
    document_id: str
    notes: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------- job posts


class JobTask(BaseModel):
    task: str = Field(description="One atomic, repeatable task in plain words.")
    quote: str = Field(description="Verbatim responsibility text this task comes from.")
    frequency: Evidence
    tools: list[str] = Field(description="Software named in the quote, e.g. Excel, QuickBooks.")
    inputs: list[str] = Field(description="Inputs named in the quote, e.g. PDFs, email, portals.")
    rules_based: bool = Field(description="Inferred: does the task follow fixed rules on structured inputs?")


class JobExtraction(BaseModel):
    role_title: str
    company: str
    pay: Evidence
    tasks: list[JobTask]


# ---------------------------------------------------------------- pricing pages


class PriceTier(BaseModel):
    name: str
    price: str = Field(description="Exactly as written, e.g. '$49'.")
    unit: str = Field(description="e.g. 'per user / month'. Empty string if not stated.")
    quote: str = Field(description="Verbatim span containing the tier name or price.")


class PriceExtraction(BaseModel):
    has_public_pricing: bool
    contact_sales_only: bool
    tiers: list[PriceTier]


# ---------------------------------------------------------------- synthesis


class Synthesis(BaseModel):
    title: str = Field(description="Short problem name, max 10 words. No product idea.")
    summary: str = Field(description="2-3 sentences using only the evidence provided.")
    hypotheses: list[str] = Field(description="1-4 open questions for customer interviews, each starting 'Hypothesis:'.")


class SkepticPoint(BaseModel):
    claim: str
    kind: Literal["evidence", "hypothesis"]
    signal_ids: list[str]


class SkepticReview(BaseModel):
    points: list[SkepticPoint]


class MergeGroup(BaseModel):
    ids: list[str] = Field(description="Two or more cluster ids describing the same job to be done.")
    reason: str


class MergePlan(BaseModel):
    groups: list[MergeGroup]


# ---------------------------------------------------------------- stored records


class Document(BaseModel):
    id: str
    source: str  # collector name
    source_type: str  # forum | issue_tracker | job_post | search_snippet | pricing_page | review
    route: str  # official_api | public_api | search_api | polite_crawl | manual | fixture
    external_id: str
    url: str
    title: str = ""
    text: str
    author_hash: str | None = None
    published_at: datetime | None = None
    captured_at: datetime
    metrics: dict = Field(default_factory=dict)
    query: str | None = None
    arm_id: str | None = None
    content_hash: str = ""
    duplicate_of: str | None = None

    @staticmethod
    def make_id(source: str, external_id: str) -> str:
        return hashlib.sha256(f"{source}:{external_id}".encode()).hexdigest()[:16]

    def model_post_init(self, __context) -> None:
        if not self.content_hash:
            normalized = " ".join(self.text.lower().split())
            self.content_hash = hashlib.sha256(normalized.encode()).hexdigest()[:16]

    @property
    def reference_text(self) -> str:
        """The text quotes are verified against."""
        return f"{self.title}\n{self.text}"

    @property
    def author_key(self) -> str:
        """Distinct-author key; falls back to domain or document for sources without authors."""
        return self.author_hash or f"{self.source}:{self.metrics.get('domain') or self.metrics.get('company') or self.id}"


class Cluster(BaseModel):
    id: str
    canonical: str
    signal_ids: list[str]
    n_signals: int
    n_authors: int
    source_types: list[str]
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    components: dict[str, float] = Field(default_factory=dict)
    score: float = 0.0
    confidence: Literal["High", "Medium", "Low"] = "Low"
    synthesis: Synthesis | None = None
    skeptic: SkepticReview | None = None
