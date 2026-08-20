"""Scaffold report helpers."""

from __future__ import annotations

from .schemas import ReviewReport
from .webhooks import build_dry_run_key


def build_scaffold_report(repo: str, pr_number: int) -> ReviewReport:
    """Return the explicit M1 dry-run report."""

    return ReviewReport(
        repo=repo,
        pr_number=pr_number,
        idempotency_key=build_dry_run_key(repo=repo, pr_number=pr_number),
        message=(
            "M1 dry-run skeleton: no review findings were generated, no model was called, "
            "and no PR comment was posted. GitHub intake boundaries are now in place."
        ),
    )
