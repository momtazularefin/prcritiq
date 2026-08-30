"""Dry-run review orchestration: fetch, parse, gate, retrieve, report.

This is the only module in the dry-run path that performs I/O. The GitHub client
is injectable so tests and CI exercise the whole flow without a network call,
which NFR4 requires.
"""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import nullcontext
from typing import Any

from . import store as run_store
from .config import Settings
from .diff import FileDiff, build_diff_index, file_diff_from_changed_file
from .findings import ReviewedFinding
from .github import GitHubClient, parse_repo_reference
from .graph import run_review_graph
from .guardrails import apply_guardrails, reviewable_files
from .posting import post_findings
from .providers import ModelChoice, ModelProvider
from .reporting import build_review_report
from .retrieval import RetrievalResult, SourceIndex, retrieve_context
from .schemas import PostingReport, ReviewReport, RunRecord
from .tools import ToolRun, run_static_analysis
from .tracing import TraceHandle, trace_run
from .webhooks import build_dry_run_key
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
    include_review: bool = False,
    persist: bool = False,
    post: bool = False,
    provider: ModelProvider | None = None,
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

    reviewed: tuple[ReviewedFinding, ...] | None = None
    summary: str | None = None
    model_choice: ModelChoice | None = None
    trace: TraceHandle | None = None
    if include_review:
        with trace_run(settings) as handle:
            trace = handle
            state = run_review_graph(
                settings=settings,
                metadata=metadata,
                changed_files=changed_files,
                retrieval=retrieval,
                tool_runs=tool_runs if include_tools else None,
                provider=provider,
            )
        reviewed = state.get("reviewed") or ()
        summary = state.get("summary") or ""
        model_choice = state.get("model_choice")

    if post and not include_review:
        raise run_store.StoreError(
            "Posting requires --review: there are no findings to post without a review."
        )

    run_record: RunRecord | None = None
    if persist or post:
        run_record = _persist_run(
            settings=settings,
            report_key=build_dry_run_key(
                repo=repo_full_name, pr_number=pr_number, head_sha=metadata.head_sha
            ),
            repo_full_name=repo_full_name,
            pr_number=pr_number,
            head_sha=metadata.head_sha,
            file_diffs=file_diffs,
            outcomes=outcomes,
            tool_runs=tool_runs if include_tools else None,
            reviewed=reviewed,
            summary=summary,
            model_choice=model_choice,
            trace=trace,
        )

    posting_report: PostingReport | None = None
    if post and run_record is not None:
        posting_report = _post_findings(
            settings=settings,
            run_record=run_record,
            repo_full_name=repo_full_name,
            pr_number=pr_number,
            head_sha=metadata.head_sha,
            reviewed=reviewed or (),
            diff_index=build_diff_index(file_diffs),
            client=resolved if client is not None else None,
        )

    return build_review_report(
        metadata=metadata,
        file_diffs=file_diffs,
        outcomes=outcomes,
        index=index,
        retrieval=retrieval,
        tool_runs=tool_runs if include_tools else None,
        reviewed=reviewed,
        summary=summary,
        model_choice=model_choice,
        run_record=run_record,
        posting=posting_report,
    )


def _persist_run(
    *,
    settings: Settings,
    report_key: str,
    repo_full_name: str,
    pr_number: int,
    head_sha: str,
    file_diffs: Sequence[FileDiff],
    outcomes: Sequence[Any],
    tool_runs: Sequence[Any] | None,
    reviewed: Sequence[Any] | None,
    summary: str | None,
    model_choice: ModelChoice | None,
    trace: TraceHandle | None,
) -> RunRecord:
    """Write one run to the store, or resolve to the run that already exists.

    A repeated idempotency key does not produce a second set of findings. The
    existing run is returned untouched, which is the behavior AC1 and AC10 ask
    for when a webhook is redelivered.
    """

    if not settings.database_url:
        raise run_store.StoreError(
            "Persistence was requested but DATABASE_URL is not set. "
            "Start the bundled Postgres with `docker compose up -d` and export DATABASE_URL."
        )

    with run_store.connect(settings.database_url) as connection:
        run_store.migrate(connection)
        row, created = run_store.create_or_get_run(
            connection,
            idempotency_key=report_key,
            repo_full_name=repo_full_name,
            pr_number=pr_number,
            head_sha=head_sha,
        )
        run_id = int(row["id"])
        if not created:
            return RunRecord(
                run_id=run_id,
                status=str(row["status"]),
                created=False,
                trace_provider=row.get("trace_provider"),
                trace_id=row.get("trace_id"),
                note="A run already existed for this idempotency key; nothing was written again.",
            )

        run_store.update_run_status(connection, run_id, run_store.RunStatus.RUNNING)
        run_store.record_routing(
            connection,
            run_id,
            provider=model_choice.provider if model_choice else None,
            model=model_choice.model if model_choice else None,
            reason=model_choice.reason if model_choice else None,
            trace_provider=trace.stored_provider if trace else None,
            trace_id=trace.trace_id if trace else None,
        )
        run_store.record_files(connection, run_id, file_diffs, outcomes)
        if tool_runs:
            run_store.record_tool_runs(connection, run_id, tool_runs)
        if reviewed:
            run_store.record_findings(connection, run_id, reviewed)
        final = run_store.update_run_status(
            connection,
            run_id,
            run_store.RunStatus.SUMMARIZED,
            summary=summary or "",
        )

    return RunRecord(
        run_id=run_id,
        status=str(final["status"]),
        created=True,
        trace_provider=trace.stored_provider if trace else None,
        trace_id=trace.trace_id if trace else None,
    )


def _post_findings(
    *,
    settings: Settings,
    run_record: RunRecord,
    repo_full_name: str,
    pr_number: int,
    head_sha: str,
    reviewed: Sequence[ReviewedFinding],
    diff_index: Any,
    client: GitHubClient | None,
) -> PostingReport:
    """Post validated findings, opening a client only if one was not supplied."""

    if not settings.github_token and client is None:
        raise run_store.StoreError(
            "Posting requires GITHUB_TOKEN. PRCritiq will not attempt an unauthenticated write."
        )

    owned = client is None
    resolved = client or GitHubClient(
        token=settings.github_token,
        api_base_url=settings.github_api_base_url,
        timeout_seconds=settings.github_request_timeout_seconds,
    )
    try:
        with run_store.connect(str(settings.database_url)) as connection:
            outcome = post_findings(
                client=resolved,
                connection=connection,
                run_id=run_record.run_id,
                repo=repo_full_name,
                pr_number=pr_number,
                head_sha=head_sha,
                reviewed=reviewed,
                diff_index=diff_index,
            )
            if outcome.posted:
                run_store.update_run_status(
                    connection, run_record.run_id, run_store.RunStatus.POSTED
                )
    finally:
        if owned:
            resolved.close()

    return PostingReport(
        attempted=outcome.attempted,
        posted=outcome.posted_count,
        skipped=[
            {"file_path": item.file_path, "line": item.line, "reason": item.reason}
            for item in outcome.skipped
        ],
        comment_ids=[item.comment_id for item in outcome.posted],
        summary=outcome.summary,
    )
