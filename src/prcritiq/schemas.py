"""Public response schemas for M0 scaffold endpoints."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, HttpUrl, PositiveInt


class HealthResponse(BaseModel):
    service: Literal["prcritiq"]
    status: Literal["ok"]
    version: str
    environment: str
    acceleration: Literal["none", "gpu", "npu"]
    implementation_status: Literal["m0_scaffold"]


class ReviewRequest(BaseModel):
    repo: HttpUrl
    pr_number: PositiveInt = Field(alias="pr")
    mode: Literal["dry-run"] = "dry-run"


class ReviewReport(BaseModel):
    service: Literal["prcritiq"] = "prcritiq"
    implementation_status: Literal["m0_scaffold"] = "m0_scaffold"
    mode: Literal["dry-run"] = "dry-run"
    repo: str
    pr_number: int
    findings: list[dict[str, object]] = Field(default_factory=list)
    posted_comments: int = 0
    message: str
