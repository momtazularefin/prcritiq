"""Pure construction of dry-run review reports."""

from __future__ import annotations

from collections.abc import Sequence

from .diff import FileDiff
from .github import PullRequestMetadata
from .guardrails import GuardrailDecision, GuardrailOutcome
from .retrieval import RetrievalResult, RetrievedChunk, SourceIndex
from .schemas import ContextChunkReport, ContextReport, FileReport, ReviewReport
from .webhooks import build_dry_run_key

_NO_MODEL_NOTE = (
    "Dry run: the diff was parsed and the guardrail gate applied. No model was "
    "called, no context was retrieved, and no comment was posted, so the empty "
    "findings list means not-yet-implemented rather than nothing-to-report."
)

_NO_MODEL_WITH_CONTEXT_NOTE = (
    "Dry run: the diff was parsed, the guardrail gate applied, and review context "
    "retrieved. No model was called and no comment was posted, so the empty "
    "findings list means not-yet-implemented rather than nothing-to-report."
)


def _describe(item: RetrievedChunk) -> ContextChunkReport:
    return ContextChunkReport(
        chunk_id=item.chunk.chunk_id,
        path=item.chunk.path,
        symbol=item.chunk.symbol,
        symbol_type=item.chunk.symbol_type,
        start_line=item.chunk.start_line,
        end_line=item.chunk.end_line,
        reason=item.reason,
    )


def build_context_report(index: SourceIndex, retrieval: RetrievalResult) -> ContextReport:
    """Describe retrieved context by chunk id so a finding can cite it."""

    return ContextReport(
        indexed_files=len(index.paths),
        indexed_chunks=len(index.chunks),
        focus=[_describe(item) for item in retrieval.focus],
        related=[_describe(item) for item in retrieval.related],
        total_bytes=retrieval.total_bytes,
        truncated=retrieval.truncated,
    )


def build_review_report(
    *,
    metadata: PullRequestMetadata,
    file_diffs: Sequence[FileDiff],
    outcomes: Sequence[GuardrailOutcome],
    index: SourceIndex | None = None,
    retrieval: RetrievalResult | None = None,
) -> ReviewReport:
    """Assemble the dry-run report from parsed diffs and guardrail verdicts."""

    if len(file_diffs) != len(outcomes):
        raise ValueError("file diffs and guardrail outcomes must be the same length")

    files = [
        FileReport(
            path=file_diff.path,
            previous_path=file_diff.previous_path,
            status=file_diff.status,
            language=file_diff.language,
            decision=outcome.decision.value,
            reason=outcome.reason,
            changed_lines=len(file_diff.changed_new_lines),
        )
        for file_diff, outcome in zip(file_diffs, outcomes, strict=True)
    ]

    skipped: dict[str, int] = {}
    for outcome in outcomes:
        if outcome.decision is not GuardrailDecision.REVIEWABLE:
            skipped[outcome.decision.value] = skipped.get(outcome.decision.value, 0) + 1

    reviewable = [
        file_diff
        for file_diff, outcome in zip(file_diffs, outcomes, strict=True)
        if outcome.reviewable
    ]

    return ReviewReport(
        repo=metadata.repo,
        pr_number=metadata.number,
        idempotency_key=build_dry_run_key(
            repo=metadata.repo,
            pr_number=metadata.number,
            head_sha=metadata.head_sha,
        ),
        title=metadata.title,
        state=metadata.state,
        author_login=metadata.author_login,
        base_sha=metadata.base_sha,
        head_sha=metadata.head_sha,
        html_url=metadata.html_url,
        files=files,
        total_files=len(files),
        reviewable_files=len(reviewable),
        skipped_files=len(files) - len(reviewable),
        skipped_by_decision=dict(sorted(skipped.items())),
        commentable_lines=sum(len(file_diff.changed_new_lines) for file_diff in reviewable),
        context=(
            build_context_report(index, retrieval)
            if index is not None and retrieval is not None
            else None
        ),
        message=(_NO_MODEL_WITH_CONTEXT_NOTE if retrieval is not None else _NO_MODEL_NOTE),
    )
