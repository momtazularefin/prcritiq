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


class ReviewReport(BaseModel):
    service: Literal["prcritiq"] = "prcritiq"
    implementation_status: ImplementationStatus = IMPLEMENTATION_STATUS
    mode: Literal["dry-run"] = "dry-run"
    repo: str
    pr_number: int
    idempotency_key: str
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
