"""Public response schemas."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, HttpUrl, PositiveInt

#: How far the implementation has actually progressed. Declared once so the
#: label cannot go stale in one surface while another still reports it.
IMPLEMENTATION_STATUS = "m2_diff_guardrails"
ImplementationStatus = Literal["m2_diff_guardrails"]


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
    findings: list[dict[str, object]] = Field(default_factory=list)
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
    message: str
