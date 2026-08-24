"""Dry-run review orchestration: fetch, parse, gate, retrieve, report.

This is the only module in the dry-run path that performs I/O. The GitHub client
is injectable so tests and CI exercise the whole flow without a network call,
which NFR4 requires.
"""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import nullcontext

from .config import Settings
from .diff import FileDiff, file_diff_from_changed_file
from .github import GitHubClient, parse_repo_reference
from .guardrails import apply_guardrails, reviewable_files
from .reporting import build_review_report
from .retrieval import RetrievalResult, SourceIndex, retrieve_context
from .schemas import ReviewReport
from .workspace import extract_source_archive, temporary_workspace


def gather_context(
    *,
    client: GitHubClient,
    repo: str,
    ref: str,
    file_diffs: Sequence[FileDiff],
    settings: Settings,
) -> tuple[SourceIndex, RetrievalResult]:
    """Snapshot the repository at `ref` and retrieve context for changed files.

    The snapshot lives in a temporary workspace that is removed before this
    returns; only the parsed index and the retrieval result outlive it.
    """

    with temporary_workspace() as root:
        archive = root / "source.tar.gz"
        client.download_source_archive(repo, ref, archive)
        sources = extract_source_archive(archive, root / "src", settings)

    index = SourceIndex(sources)
    return index, retrieve_context(index, file_diffs, settings)


def run_dry_run(
    *,
    repo: str,
    pr_number: int,
    settings: Settings,
    client: GitHubClient | None = None,
    include_context: bool = False,
) -> ReviewReport:
    """Review one pull request without calling a model or posting anything.

    Raises `RepoReferenceError` for an unusable repository reference and
    `GitHubClientError` when GitHub does not return what the review needs.
    """

    repo_full_name = parse_repo_reference(repo)

    owned = client is None
    resolved = client or GitHubClient(
        token=settings.github_token,
        api_base_url=settings.github_api_base_url,
        timeout_seconds=settings.github_request_timeout_seconds,
    )
    with resolved if owned else nullcontext(resolved):
        metadata = resolved.get_pull_request(repo_full_name, pr_number)
        changed_files = resolved.list_changed_files(repo_full_name, pr_number)

        file_diffs = [file_diff_from_changed_file(changed) for changed in changed_files]
        outcomes = apply_guardrails(file_diffs, settings)

        index: SourceIndex | None = None
        retrieval: RetrievalResult | None = None
        if include_context:
            in_scope = reviewable_files(file_diffs, outcomes)
            index, retrieval = gather_context(
                client=resolved,
                repo=repo_full_name,
                ref=metadata.head_sha,
                file_diffs=in_scope,
                settings=settings,
            )

    return build_review_report(
        metadata=metadata,
        file_diffs=file_diffs,
        outcomes=outcomes,
        index=index,
        retrieval=retrieval,
    )
