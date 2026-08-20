"""Public response schemas."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, HttpUrl, PositiveInt


class HealthResponse(BaseModel):
    service: Literal["prcritiq"]
    status: Literal["ok"]
    version: str
    environment: str
    acceleration: Literal["none", "gpu", "npu"]
    implementation_status: Literal["m1_intake_dry_run"]


class ReviewRequest(BaseModel):
    repo: HttpUrl
    pr_number: PositiveInt = Field(alias="pr")
    mode: Literal["dry-run"] = "dry-run"


class ReviewReport(BaseModel):
    service: Literal["prcritiq"] = "prcritiq"
    implementation_status: Literal["m1_intake_dry_run"] = "m1_intake_dry_run"
    mode: Literal["dry-run"] = "dry-run"
    repo: str
    pr_number: int
    idempotency_key: str
    findings: list[dict[str, object]] = Field(default_factory=list)
    posted_comments: int = 0
    message: str


class WebhookAck(BaseModel):
    service: Literal["prcritiq"] = "prcritiq"
    implementation_status: Literal["m1_intake_dry_run"] = "m1_intake_dry_run"
    accepted: bool
    event: str
    delivery_id: str
    action: str | None = None
    idempotency_key: str | None = None
    message: str
