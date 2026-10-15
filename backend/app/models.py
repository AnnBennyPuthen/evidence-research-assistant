from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field, HttpUrl, field_validator


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


class ResearchRequest(BaseModel):
    question: str = Field(min_length=12, max_length=700)
    depth: Literal["quick", "standard"] = "quick"
    preferred_domains: list[str] = Field(default_factory=list, max_length=8)

    @field_validator("preferred_domains")
    @classmethod
    def clean_domains(cls, domains: list[str]) -> list[str]:
        return [domain.lower().strip().removeprefix("www.") for domain in domains if domain.strip()]


class ResearchTask(BaseModel):
    id: str = Field(default_factory=lambda: new_id("task"))
    question: str
    search_queries: list[str] = Field(max_length=4)
    success_criteria: list[str] = Field(max_length=4)


class ResearchPlan(BaseModel):
    overview: str
    tasks: list[ResearchTask] = Field(min_length=1, max_length=3)
    comparison_dimensions: list[str] = Field(default_factory=list, max_length=5)


class SourceDocument(BaseModel):
    id: str = Field(default_factory=lambda: new_id("src"))
    canonical_url: HttpUrl
    title: str
    domain: str
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    published_at: datetime | None = None
    content_type: str = "text/html"
    normalized_text: str
    content_hash: str
    extraction_status: Literal["ok", "partial", "failed"] = "ok"
    source_category: Literal["primary", "secondary", "unknown"] = "unknown"
    category_basis: str = "No reviewed basis available."
    synthetic: bool = False


class EvidencePassage(BaseModel):
    id: str = Field(default_factory=lambda: new_id("ev"))
    source_id: str
    excerpt: str = Field(min_length=1)
    start_offset: int = Field(ge=0)
    end_offset: int = Field(gt=0)
    page_number: int | None = Field(default=None, ge=1)
    usable: bool = True


class CandidateClaim(BaseModel):
    id: str = Field(default_factory=lambda: new_id("claim"))
    text: str
    evidence_ids: list[str] = Field(default_factory=list)


class AssessmentDraft(BaseModel):
    claim_id: str
    status: Literal["supported", "conflicting", "insufficient"]
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    contradicting_evidence_ids: list[str] = Field(default_factory=list)
    explanation: str


class VerificationOutput(BaseModel):
    assessments: list[AssessmentDraft]
    follow_up_questions: list[str] = Field(default_factory=list, max_length=3)


class ReportWordingAssessment(BaseModel):
    finding_index: int = Field(ge=0)
    status: Literal["supported", "conflicting", "insufficient"]
    explanation: str


class ReportWordingOutput(BaseModel):
    assessments: list[ReportWordingAssessment]


class ClaimAssessment(BaseModel):
    id: str = Field(default_factory=lambda: new_id("assessment"))
    claim_id: str
    text: str
    status: Literal["supported", "conflicting", "insufficient"]
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    contradicting_evidence_ids: list[str] = Field(default_factory=list)
    explanation: str


class FollowUpRequest(BaseModel):
    task: ResearchTask
    rationale: str


class ReportFinding(BaseModel):
    heading: str
    text: str
    claim_ids: list[str] = Field(default_factory=list)
    kind: Literal["evidence", "synthesis", "recommendation"] = "evidence"
    premise_claim_ids: list[str] = Field(default_factory=list)


class Report(BaseModel):
    overview: str
    findings: list[ReportFinding] = Field(default_factory=list)
    comparison: list[ReportFinding] = Field(default_factory=list)
    conflicts: list[ReportFinding] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    demo_label: str | None = None


class WorkflowEvent(BaseModel):
    id: int | None = None
    run_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    kind: str
    message: str
    data: dict = Field(default_factory=dict)


class Usage(BaseModel):
    model_requests: int = 0
    scripted_model_steps: int = 0
    metrics_scope: Literal["live", "demo"] = "demo"
    input_tokens: int | None = None
    output_tokens: int | None = None
    search_requests: int = 0
    fetch_attempts: int = 0
    cached_calls: int = 0
    cached_calls: int = 0
    elapsed_seconds: float = 0
    ended_by_limit: str | None = None
    cost_estimate: str = "Unavailable"


class ResearchResult(BaseModel):
    run_id: str
    status: Literal["completed", "partial", "failed", "cancelled", "interrupted"]
    request: ResearchRequest
    plan: ResearchPlan | None = None
    sources: list[SourceDocument] = Field(default_factory=list)
    evidence: list[EvidencePassage] = Field(default_factory=list)
    claims: list[ClaimAssessment] = Field(default_factory=list)
    report: Report | None = None
    usage: Usage = Field(default_factory=Usage)
    error: str | None = None
