"""The finding schema a review produces, and the verdicts applied to it.

Every field the design names is required of the model, not optional. A finding
without evidence or a suggested fix is not a cheaper finding, it is the kind of
comment this project exists to avoid.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class Severity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Category(StrEnum):
    BUG = "bug"
    REGRESSION = "regression"
    CONTRACT = "contract"
    TEST_GAP = "test_gap"
    ERROR_HANDLING = "error_handling"
    SECURITY = "security"
    TOOL = "tool"


class SuppressionReason(StrEnum):
    """Why a candidate did not reach a reader."""

    INVALID_LINE = "invalid_line"
    NO_EVIDENCE = "no_evidence"
    UNKNOWN_SOURCE_REF = "unknown_source_ref"
    GENERIC = "generic"
    DUPLICATE = "duplicate"
    LOW_CONFIDENCE = "low_confidence"


class CandidateFinding(BaseModel):
    """One finding as drafted by a model, before any validation."""

    file_path: str = Field(description="Repository path of the file the finding is about")
    line: int = Field(description="Line number in the head revision the finding targets")
    severity: Severity
    confidence: int = Field(ge=0, le=100, description="Confidence percentage, 0 to 100")
    category: Category
    finding: str = Field(description="What is wrong, in one or two sentences")
    evidence: str = Field(description="The concrete observation that supports the finding")
    suggested_fix: str = Field(description="A specific change that would resolve it")
    source_refs: list[str] = Field(
        default_factory=list,
        description="Chunk ids or tool diagnostic codes the evidence came from",
    )


class DraftedFindings(BaseModel):
    """The structured output contract for the drafting call."""

    findings: list[CandidateFinding] = Field(default_factory=list)


class ReviewedFinding(BaseModel):
    """A candidate after self-critique has ruled on it."""

    candidate: CandidateFinding
    publish_decision: Literal["publish", "suppress"]
    suppression_reason: SuppressionReason | None = None

    @property
    def published(self) -> bool:
        return self.publish_decision == "publish"

    def as_comment(self) -> str:
        """Render the human-facing comment shape from the design."""

        return (
            f"Severity: {self.candidate.severity.value}\n"
            f"Confidence: {self.candidate.confidence}%\n"
            f"Finding: {self.candidate.finding}\n"
            f"Evidence: {self.candidate.evidence}\n"
            f"Suggested fix: {self.candidate.suggested_fix}"
        )
