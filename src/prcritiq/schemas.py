"""Public response schemas."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, HttpUrl, PositiveInt

#: How far the implementation has actually progressed. Declared once so the
#: label cannot go stale in one surface while another still reports it.
IMPLEMENTATION_STATUS = "m9_deployment"
ImplementationStatus = Literal["m9_deployment"]


class HealthResponse(BaseModel):
    service: Literal["prcritiq"]
    status: Literal["ok"]
    version: str
    environment: str
    acceleration: Literal["none", "gpu", "npu"]
    implementation_status: ImplementationStatus


class ReviewRequest(BaseModel):
    repo: HttpUrl
    pr_number: PositiveInt = Field(alias="pr")
    mode: Literal["dry-run"] = "dry-run"


class FileReport(BaseModel):
    """One changed file and what the guardrail gate decided about it."""

    path: str
    previous_path: str | None = None
    status: str
    language: str
    decision: str
    reason: str
    changed_lines: int


class ContextChunkReport(BaseModel):
    """One retrieved chunk, identified so a later finding can cite it."""

    chunk_id: str
    path: str
    symbol: str
    symbol_type: str
    start_line: int
    end_line: int
    reason: str


class ContextReport(BaseModel):
    """What retrieval put in front of the reviewer, and what it left out."""

    indexed_files: int
    indexed_chunks: int
    focus: list[ContextChunkReport] = Field(default_factory=list)
    related: list[ContextChunkReport] = Field(default_factory=list)
    total_bytes: int = 0
    truncated: bool = False


class DiagnosticReport(BaseModel):
    """One structured diagnostic from a static-analysis tool."""

    tool: str
    path: str
    line: int
    column: int
    code: str
    message: str
    on_changed_line: bool


class ToolRunReport(BaseModel):
    """One tool's outcome, including why it did not run."""

    tool: str
    status: str
    reason: str
    exit_code: int | None = None
    duration_seconds: float = 0.0
    diagnostics: list[DiagnosticReport] = Field(default_factory=list)
    diagnostics_on_changed_lines: int = 0


class FindingReport(BaseModel):
    """One finding as reported, published or suppressed."""

    file_path: str
    line: int
    severity: str
    confidence: int
    category: str
    finding: str
    evidence: str
    suggested_fix: str
    source_refs: list[str] = Field(default_factory=list)
    publish_decision: str
    suppression_reason: str | None = None
    comment: str | None = None


class ReviewOutcome(BaseModel):
    """What the review graph did, and which model it used."""

    provider: str | None = None
    model: str | None = None
    routing_reason: str | None = None
    summary: str
    candidates: int = 0
    published: int = 0
    suppressed: int = 0
    suppressed_by_reason: dict[str, int] = Field(default_factory=dict)
    invalid_line_rate: float = 0.0
    node_sequence: list[str] = Field(default_factory=list)


class RunRecord(BaseModel):
    """Where this run was persisted, and how it is traced."""

    run_id: int
    status: str
    created: bool
    trace_provider: str | None = None
    trace_id: str | None = None
    note: str | None = None


class PostingReport(BaseModel):
    """What posting did, or declined to do."""

    attempted: int = 0
    posted: int = 0
    skipped: list[dict[str, object]] = Field(default_factory=list)
    comment_ids: list[int] = Field(default_factory=list)
    summary: str


class ReviewReport(BaseModel):
    service: Literal["prcritiq"] = "prcritiq"
    implementation_status: ImplementationStatus = IMPLEMENTATION_STATUS
    mode: Literal["dry-run"] = "dry-run"
    repo: str
    pr_number: int
    idempotency_key: str
    title: str
    state: str
    author_login: str
    base_sha: str
    head_sha: str
    html_url: str
    files: list[FileReport] = Field(default_factory=list)
    total_files: int = 0
    reviewable_files: int = 0
    skipped_files: int = 0
    skipped_by_decision: dict[str, int] = Field(default_factory=dict)
    commentable_lines: int = 0
    context: ContextReport | None = None
    tools: list[ToolRunReport] | None = None
    run: RunRecord | None = None
    review: ReviewOutcome | None = None
    posting: PostingReport | None = None
    findings: list[FindingReport] = Field(default_factory=list)
    suppressed_findings: list[FindingReport] = Field(default_factory=list)
    posted_comments: int = 0
    message: str


class WebhookAck(BaseModel):
    service: Literal["prcritiq"] = "prcritiq"
    implementation_status: ImplementationStatus = IMPLEMENTATION_STATUS
    accepted: bool
    event: str
    delivery_id: str
    action: str | None = None
    idempotency_key: str | None = None
    run_id: int | None = None
    run_status: str | None = None
    message: str


class RunStatusResponse(BaseModel):
    """Where one webhook or CLI run stands, without the report body.

    Counts rather than findings: the status endpoint is public, and a count says
    whether the run did its work without republishing what it found.
    """

    service: Literal["prcritiq"] = "prcritiq"
    run_id: int
    repo: str
    pr_number: int
    head_sha: str
    mode: str
    status: str
    summary: str | None = None
    error: str | None = None
    files: int = 0
    reviewable_files: int = 0
    findings: int = 0
    publishable_findings: int = 0
    created_at: str
    updated_at: str
