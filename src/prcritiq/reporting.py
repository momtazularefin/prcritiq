"""Scaffold report helpers."""

from __future__ import annotations

from .schemas import ReviewReport


def build_scaffold_report(repo: str, pr_number: int) -> ReviewReport:
    """Return the explicit M0 dry-run report."""

    return ReviewReport(
        repo=repo,
        pr_number=pr_number,
        message=(
            "M0 scaffold only: no GitHub data was fetched, no model was called, "
            "and no PR comment was posted."
        ),
    )
