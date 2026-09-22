"""Webhook intake: turn a pull_request event into a recorded, dry-run review.

Acceptance and execution are separate steps. Accepting writes one pending run
under the event's idempotency key and returns, so GitHub gets its answer well
inside its ten-second delivery timeout. Execution then fetches the pull
request, applies the guardrails, optionally runs the model review, and records
the result against that run.

Webhook runs never post. A public webhook URL that could write to a pull
request on its own is a different product with a different review bar, and
ADR-019 keeps the deployed service to dry-run.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from . import store as run_store
from .config import Settings
from .diff import file_diff_from_changed_file
from .github import GitHubClient
from .github_app import client_for_installation
from .graph import run_review_graph
from .guardrails import apply_guardrails, reviewable_files
from .providers import ModelProvider
from .review import gather_evidence
from .tracing import trace_run
from .webhooks import WebhookPayloadError, build_review_run_key

#: The mode stored on webhook runs, so they are never mistaken for a CLI run
#: of the same pull request.
WEBHOOK_MODE = "webhook-dry-run"

#: Stored error text is shown by the run status endpoint, so it is kept short.
_MAX_ERROR_CHARS = 500

ClientFactory = Callable[[Settings, int | None], GitHubClient]


@dataclass(frozen=True)
class PullRequestEvent:
    """The fields of a pull_request delivery that a review run needs."""

    action: str
    delivery_id: str
    installation_id: int
    repository_id: int
    repo_full_name: str
    repo_private: bool
    pr_number: int
    head_sha: str

    @property
    def idempotency_key(self) -> str:
        return build_review_run_key(
            installation_id=self.installation_id,
            repository_id=self.repository_id,
            pull_number=self.pr_number,
            head_sha=self.head_sha,
            delivery_id=self.delivery_id,
        )


@dataclass(frozen=True)
class AcceptedRun:
    run_id: int
    status: str
    created: bool


def parse_pull_request_event(payload: Any, *, delivery_id: str) -> PullRequestEvent:
    """Read a pull_request payload, refusing one that lacks a required field."""

    try:
        repository = payload["repository"]
        pull_request = payload["pull_request"]
        return PullRequestEvent(
            action=str(payload["action"]),
            delivery_id=delivery_id,
            installation_id=int(payload["installation"]["id"]),
            repository_id=int(repository["id"]),
            repo_full_name=str(repository["full_name"]),
            # Missing visibility is treated as private, as in the GitHub client.
            repo_private=bool(repository.get("private", True)),
            pr_number=int(pull_request["number"]),
            head_sha=str(pull_request["head"]["sha"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise WebhookPayloadError(
            "pull_request webhook payload is missing installation, repository, "
            "PR number, or head SHA"
        ) from exc


def accept_event(settings: Settings, event: PullRequestEvent) -> AcceptedRun:
    """Create the pending run for this delivery, or find the one already there.

    A redelivery carries the same delivery id, so it resolves to the same key
    and the same run. It is reported rather than executed again.
    """

    if not settings.database_url:
        raise run_store.StoreError("Webhook intake needs DATABASE_URL to record review runs")
    with run_store.connect(settings.database_url) as connection:
        run_store.migrate(connection)
        row, created = run_store.create_or_get_run(
            connection,
            idempotency_key=event.idempotency_key,
            repo_full_name=event.repo_full_name,
            pr_number=event.pr_number,
            head_sha=event.head_sha,
            mode=WEBHOOK_MODE,
        )
    return AcceptedRun(run_id=int(row["id"]), status=str(row["status"]), created=created)


def execute_run(
    settings: Settings,
    event: PullRequestEvent,
    run_id: int,
    *,
    client_factory: ClientFactory = client_for_installation,
    provider: ModelProvider | None = None,
) -> str:
    """Carry one accepted run to a terminal state and return that state.

    Every failure is recorded on the run rather than raised: this runs after
    the response has gone back to GitHub, so an exception here would reach
    nobody who could act on it.
    """

    dsn = str(settings.database_url)
    try:
        with run_store.connect(dsn) as connection:
            row = run_store.get_run(connection, run_id)
            if row is None or row["status"] != run_store.RunStatus.PENDING.value:
                # Another worker, or an earlier delivery of this event, owns it.
                return str(row["status"]) if row else "missing"
            return _execute(connection, settings, event, run_id, client_factory, provider)
    except Exception as exc:  # recorded on the run, see docstring
        return _fail(dsn, run_id, exc)


def _execute(
    connection: Any,
    settings: Settings,
    event: PullRequestEvent,
    run_id: int,
    client_factory: ClientFactory,
    provider: ModelProvider | None,
) -> str:
    with client_factory(settings, event.installation_id) as client:
        metadata = client.get_pull_request(event.repo_full_name, event.pr_number)
        if metadata.repo_private and not settings.allow_private_repos:
            return _skip(
                connection,
                run_id,
                "The repository is private; this deployment reviews public repositories only.",
            )
        if metadata.head_sha != event.head_sha:
            return _skip(
                connection,
                run_id,
                f"The pull request moved to {metadata.head_sha[:12]} before this run started; "
                "the delivery for that commit reviews it.",
            )

        run_store.update_run_status(connection, run_id, run_store.RunStatus.RUNNING)
        changed_files = client.list_changed_files(event.repo_full_name, event.pr_number)
        file_diffs = [file_diff_from_changed_file(changed) for changed in changed_files]
        outcomes = apply_guardrails(file_diffs, settings)
        run_store.record_files(connection, run_id, file_diffs, outcomes)
        reviewable = sum(1 for outcome in outcomes if outcome.reviewable)

        if not settings.webhook_review:
            summary = (
                f"Dry-run intake: {reviewable} of {len(file_diffs)} changed files are "
                "reviewable. Model review is off for webhook runs "
                "(PRCRITIQ_WEBHOOK_REVIEW=false); nothing was posted."
            )
            final = run_store.update_run_status(
                connection, run_id, run_store.RunStatus.SUMMARIZED, summary=summary
            )
            return str(final["status"])

        in_scope = reviewable_files(file_diffs, outcomes)
        _index, retrieval, tool_runs = gather_evidence(
            client=client,
            repo=event.repo_full_name,
            ref=metadata.head_sha,
            file_diffs=in_scope,
            settings=settings,
            want_context=True,
            want_tools=True,
        )

    with trace_run(settings, name="prcritiq.webhook_review") as trace:
        state = run_review_graph(
            settings=settings,
            metadata=metadata,
            changed_files=changed_files,
            retrieval=retrieval,
            tool_runs=tool_runs,
            provider=provider,
        )
    choice = state.get("model_choice")
    run_store.record_routing(
        connection,
        run_id,
        provider=choice.provider if choice else None,
        model=choice.model if choice else None,
        reason=choice.reason if choice else None,
        trace_provider=trace.stored_provider,
        trace_id=trace.trace_id,
    )
    if tool_runs:
        run_store.record_tool_runs(connection, run_id, tool_runs)
    reviewed = state.get("reviewed") or ()
    if reviewed:
        run_store.record_findings(connection, run_id, reviewed)
    summary = (state.get("summary") or "") + " Dry-run: nothing was posted."
    final = run_store.update_run_status(
        connection, run_id, run_store.RunStatus.SUMMARIZED, summary=summary.strip()
    )
    return str(final["status"])


def _skip(connection: Any, run_id: int, reason: str) -> str:
    final = run_store.update_run_status(
        connection, run_id, run_store.RunStatus.SKIPPED, summary=reason
    )
    return str(final["status"])


def _fail(dsn: str, run_id: int, exc: Exception) -> str:
    message = f"{type(exc).__name__}: {exc}"[:_MAX_ERROR_CHARS]
    try:
        with run_store.connect(dsn) as connection:
            final = run_store.update_run_status(
                connection, run_id, run_store.RunStatus.FAILED, error=message
            )
        return str(final["status"])
    except Exception:  # the store itself is unreachable
        return run_store.RunStatus.FAILED.value


def run_status(settings: Settings, run_id: int) -> dict[str, Any] | None:
    """Read one run with its file and finding counts, or None when absent."""

    if not settings.database_url:
        raise run_store.StoreError("Run status needs DATABASE_URL")
    with run_store.connect(settings.database_url) as connection:
        row = run_store.get_run(connection, run_id)
        if row is None:
            return None
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) AS total,"
                " count(*) FILTER (WHERE guardrail_decision = 'reviewable') AS reviewable"
                " FROM review_files WHERE run_id = %s",
                (run_id,),
            )
            files = cursor.fetchone() or {}
            cursor.execute(
                "SELECT count(*) AS total,"
                " count(*) FILTER (WHERE publish_decision = 'publish') AS publishable"
                " FROM review_findings WHERE run_id = %s",
                (run_id,),
            )
            findings = cursor.fetchone() or {}
    return {
        **row,
        "files": int(files.get("total", 0)),
        "reviewable_files": int(files.get("reviewable", 0)),
        "findings": int(findings.get("total", 0)),
        "publishable_findings": int(findings.get("publishable", 0)),
    }
