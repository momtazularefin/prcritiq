"""Self-critique: the gate between what a model drafted and what a reader sees.

The model is asked to follow rules. This layer assumes it sometimes will not.
Every check here re-derives its verdict from the diff, the retrieved context, and
the tool output rather than trusting what the finding claims about itself, which
is the only way a prompt-injected or over-eager draft gets stopped.

Suppressed candidates are kept, not dropped, so a run can report its own
invalid-line and no-evidence rates instead of hiding them (AC8, AC11).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Final

from .config import Settings
from .diff import DiffIndex
from .findings import CandidateFinding, ReviewedFinding, SuppressionReason
from .retrieval import RetrievalResult
from .tools import ToolRun

#: Phrases that carry no reviewable claim. A finding whose whole substance is one
#: of these is the generic filler that makes review bots easy to ignore.
_GENERIC_PATTERNS: Final[tuple[re.Pattern[str], ...]] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"^consider (adding|using|refactoring|reviewing)\b.{0,40}$",
        r"^(this )?(code|change|function|method) (could|might|may) be (improved|better)",
        r"\b(please )?(add|write) (more )?tests\b.{0,20}$",
        r"^(consider )?(adding|improving) (documentation|comments|docstrings)\b.{0,30}$",
        r"^(this )?(looks|seems) (fine|good|correct|okay)\b",
        r"^ensure (proper|correct|appropriate)\b.{0,40}$",
    )
)

_MINIMUM_FINDING_CHARACTERS: Final = 25
_MINIMUM_EVIDENCE_CHARACTERS: Final = 20


def _is_generic(candidate: CandidateFinding) -> bool:
    text = candidate.finding.strip()
    if len(text) < _MINIMUM_FINDING_CHARACTERS:
        return True
    return any(pattern.search(text) for pattern in _GENERIC_PATTERNS)


def _known_source_refs(
    retrieval: RetrievalResult | None,
    tool_runs: Sequence[ToolRun] | None,
) -> set[str]:
    known: set[str] = set()
    if retrieval is not None:
        known.update(retrieval.chunk_ids)
    if tool_runs is not None:
        for run in tool_runs:
            known.update(item.code for item in run.diagnostics)
            known.add(run.tool)
    return known


def _duplicate_key(candidate: CandidateFinding) -> tuple[str, int, str]:
    return (candidate.file_path, candidate.line, candidate.category.value)


def critique(
    candidates: Sequence[CandidateFinding],
    *,
    diff_index: DiffIndex,
    settings: Settings,
    retrieval: RetrievalResult | None = None,
    tool_runs: Sequence[ToolRun] | None = None,
) -> tuple[ReviewedFinding, ...]:
    """Rule on every candidate, returning published and suppressed alike.

    Checks run cheapest and most objective first, so the recorded reason is the
    most defensible one: a finding on a line the pull request never touched is
    reported as an invalid line, not as low confidence.
    """

    known_refs = _known_source_refs(retrieval, tool_runs)
    seen: set[tuple[str, int, str]] = set()
    reviewed: list[ReviewedFinding] = []

    for candidate in candidates:
        reason: SuppressionReason | None = None

        if not diff_index.validate_line(candidate.file_path, candidate.line).valid:
            reason = SuppressionReason.INVALID_LINE
        elif len(candidate.evidence.strip()) < _MINIMUM_EVIDENCE_CHARACTERS:
            reason = SuppressionReason.NO_EVIDENCE
        elif known_refs and candidate.source_refs and not set(candidate.source_refs) & known_refs:
            # Every cited ref is one nothing in this run produced, which means the
            # citation was invented rather than drawn from the material.
            reason = SuppressionReason.UNKNOWN_SOURCE_REF
        elif _is_generic(candidate):
            reason = SuppressionReason.GENERIC
        elif _duplicate_key(candidate) in seen:
            reason = SuppressionReason.DUPLICATE
        elif candidate.confidence < settings.min_publish_confidence:
            reason = SuppressionReason.LOW_CONFIDENCE

        if reason is None:
            seen.add(_duplicate_key(candidate))

        reviewed.append(
            ReviewedFinding(
                candidate=candidate,
                publish_decision="suppress" if reason else "publish",
                suppression_reason=reason,
            )
        )

    return tuple(reviewed)
