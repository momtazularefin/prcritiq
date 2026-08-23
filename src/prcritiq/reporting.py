"""Pure construction of dry-run review reports."""

from __future__ import annotations

from collections.abc import Sequence

from .diff import FileDiff
from .github import PullRequestMetadata
from .guardrails import GuardrailDecision, GuardrailOutcome
from .schemas import FileReport, ReviewReport
from .webhooks import build_dry_run_key

_NO_MODEL_NOTE = (
    "Dry run: the diff was parsed and the guardrail gate applied. No model was "
    "called, no context was retrieved, and no comment was posted, so the empty "
    "findings list means not-yet-implemented rather than nothing-to-report."
)


def build_review_report(
    *,
    metadata: PullRequestMetadata,
    file_diffs: Sequence[FileDiff],
    outcomes: Sequence[GuardrailOutcome],
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
        message=_NO_MODEL_NOTE,
    )
