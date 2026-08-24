"""Pure construction of dry-run review reports."""

from __future__ import annotations

from collections.abc import Sequence

from .diff import FileDiff
from .findings import ReviewedFinding, SuppressionReason
from .github import PullRequestMetadata
from .graph import NODE_SEQUENCE
from .guardrails import GuardrailDecision, GuardrailOutcome
from .providers import ModelChoice
from .retrieval import RetrievalResult, RetrievedChunk, SourceIndex
from .schemas import (
    ContextChunkReport,
    ContextReport,
    DiagnosticReport,
    FileReport,
    FindingReport,
    ReviewOutcome,
    ReviewReport,
    RunRecord,
    ToolRunReport,
)
from .tools import ToolRun
from .webhooks import build_dry_run_key

_FINDINGS_CAVEAT = (
    "No model was called and no comment was posted, so the empty findings list "
    "means not-yet-implemented rather than nothing-to-report."
)

_POSTING_CAVEAT = (
    "Findings were drafted and critiqued but nothing was posted; posting arrives "
    "in a later milestone."
)


def _describe_evidence(
    retrieval: RetrievalResult | None,
    tool_runs: Sequence[ToolRun] | None,
    reviewed: Sequence[ReviewedFinding] | None = None,
) -> str:
    """State exactly which evidence stages ran, so the report never overclaims."""

    gathered = ["the diff was parsed", "the guardrail gate applied"]
    if retrieval is not None:
        gathered.append("review context retrieved")
    if tool_runs is not None:
        gathered.append("static analysis run")
    if reviewed is not None:
        gathered.append("the review graph run")
    if len(gathered) == 2:
        stages = " and ".join(gathered)
    else:
        stages = f"{', '.join(gathered[:-1])}, and {gathered[-1]}"
    caveat = _POSTING_CAVEAT if reviewed is not None else _FINDINGS_CAVEAT
    return f"Dry run: {stages}. {caveat}"


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


def build_tool_reports(tool_runs: Sequence[ToolRun]) -> list[ToolRunReport]:
    """Describe every tool considered, including the ones that did not run."""

    return [
        ToolRunReport(
            tool=run.tool,
            status=run.status.value,
            reason=run.reason,
            exit_code=run.exit_code,
            duration_seconds=round(run.duration_seconds, 3),
            diagnostics=[
                DiagnosticReport(
                    tool=item.tool,
                    path=item.path,
                    line=item.line,
                    column=item.column,
                    code=item.code,
                    message=item.message,
                    on_changed_line=item.on_changed_line,
                )
                for item in run.diagnostics
            ],
            diagnostics_on_changed_lines=sum(1 for item in run.diagnostics if item.on_changed_line),
        )
        for run in tool_runs
    ]


def _describe_finding(item: ReviewedFinding) -> FindingReport:
    candidate = item.candidate
    return FindingReport(
        file_path=candidate.file_path,
        line=candidate.line,
        severity=candidate.severity.value,
        confidence=candidate.confidence,
        category=candidate.category.value,
        finding=candidate.finding,
        evidence=candidate.evidence,
        suggested_fix=candidate.suggested_fix,
        source_refs=list(candidate.source_refs),
        publish_decision=item.publish_decision,
        suppression_reason=(item.suppression_reason.value if item.suppression_reason else None),
        comment=item.as_comment() if item.published else None,
    )


def build_review_outcome(
    reviewed: Sequence[ReviewedFinding],
    summary: str,
    choice: ModelChoice | None,
) -> ReviewOutcome:
    """Summarize the graph run, including the rates a benchmark needs.

    The invalid-line rate is reported rather than inferred from silence, because
    AC8 requires it to be measurably zero rather than merely unobserved.
    """

    by_reason: dict[str, int] = {}
    for item in reviewed:
        if item.suppression_reason is not None:
            key = item.suppression_reason.value
            by_reason[key] = by_reason.get(key, 0) + 1
    published = sum(1 for item in reviewed if item.published)
    invalid = by_reason.get(SuppressionReason.INVALID_LINE.value, 0)
    return ReviewOutcome(
        provider=choice.provider if choice else None,
        model=choice.model if choice else None,
        routing_reason=choice.reason if choice else None,
        summary=summary,
        candidates=len(reviewed),
        published=published,
        suppressed=len(reviewed) - published,
        suppressed_by_reason=dict(sorted(by_reason.items())),
        invalid_line_rate=round(invalid / len(reviewed), 4) if reviewed else 0.0,
        node_sequence=list(NODE_SEQUENCE),
    )


def build_review_report(
    *,
    metadata: PullRequestMetadata,
    file_diffs: Sequence[FileDiff],
    outcomes: Sequence[GuardrailOutcome],
    index: SourceIndex | None = None,
    retrieval: RetrievalResult | None = None,
    tool_runs: Sequence[ToolRun] | None = None,
    reviewed: Sequence[ReviewedFinding] | None = None,
    summary: str | None = None,
    model_choice: ModelChoice | None = None,
    run_record: RunRecord | None = None,
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
        tools=build_tool_reports(tool_runs) if tool_runs is not None else None,
        run=run_record,
        review=(
            build_review_outcome(reviewed, summary or "", model_choice)
            if reviewed is not None
            else None
        ),
        findings=[_describe_finding(i) for i in (reviewed or ()) if i.published],
        suppressed_findings=[_describe_finding(i) for i in (reviewed or ()) if not i.published],
        context=(
            build_context_report(index, retrieval)
            if index is not None and retrieval is not None
            else None
        ),
        message=_describe_evidence(retrieval, tool_runs, reviewed),
    )
