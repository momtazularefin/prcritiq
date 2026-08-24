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
from .tools import ToolRun, run_static_analysis
from .workspace import extract_source_archive, temporary_workspace


def gather_evidence(
    *,
    client: GitHubClient,
    repo: str,
    ref: str,
    file_diffs: Sequence[FileDiff],
    settings: Settings,
    want_context: bool,
    want_tools: bool,
) -> tuple[SourceIndex | None, RetrievalResult | None, tuple[ToolRun, ...]]:
    """Snapshot the repository once, then take every kind of evidence from it.

    Context retrieval and static analysis both need the source on disk, so they
    share a single download and a single temporary workspace. Tools must run
    inside that workspace, before it is removed.
    """

    tool_runs: tuple[ToolRun, ...] = ()
    with temporary_workspace() as root:
        archive = root / "source.tar.gz"
        client.download_source_archive(repo, ref, archive)
        extracted = root / "snapshot"
        sources = extract_source_archive(archive, extracted, settings)
        if want_tools:
            tool_runs = run_static_analysis(extracted, file_diffs, settings)

    if not want_context:
        return None, None, tool_runs

    index = SourceIndex(sources)
    return index, retrieve_context(index, file_diffs, settings), tool_runs


def run_dry_run(
    *,
    repo: str,
    pr_number: int,
    settings: Settings,
    client: GitHubClient | None = None,
    include_context: bool = False,
    include_tools: bool = False,
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
        tool_runs: tuple[ToolRun, ...] = ()
        if include_context or include_tools:
            in_scope = reviewable_files(file_diffs, outcomes)
            index, retrieval, tool_runs = gather_evidence(
                client=resolved,
                repo=repo_full_name,
                ref=metadata.head_sha,
                file_diffs=in_scope,
                settings=settings,
                want_context=include_context,
                want_tools=include_tools,
            )

    return build_review_report(
        metadata=metadata,
        file_diffs=file_diffs,
        outcomes=outcomes,
        index=index,
        retrieval=retrieval,
        tool_runs=tool_runs if include_tools else None,
    )
