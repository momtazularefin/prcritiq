"""Postgres run store: history, idempotency, and trace linkage.

Postgres is the source of truth for review runs, and there is no Redis in v1
(ADR-009). Idempotency is enforced by a UNIQUE constraint rather than by an
application check, so two workers racing on the same redelivered webhook cannot
both win.

Every function here takes an open connection. Nothing in this module opens one
for itself, which keeps transaction boundaries with the caller that knows what
a unit of work is.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from importlib import resources
from typing import Any, Final

import psycopg
from psycopg.rows import dict_row

from .findings import ReviewedFinding
from .guardrails import GuardrailOutcome
from .tools import ToolRun

_MIGRATION_NAME = re.compile(r"^(?P<order>\d+)_[a-z0-9_]+\.sql$")


class StoreError(RuntimeError):
    """Raised when the store cannot do what was asked of it."""


class RunStatus(StrEnum):
    """The run states named in the design."""

    PENDING = "pending"
    RUNNING = "running"
    SUMMARIZED = "summarized"
    POSTED = "posted"
    FAILED = "failed"
    SKIPPED = "skipped"


#: Legal transitions. Terminal states have no outgoing edges, so a finished run
#: cannot be quietly reopened and rewritten.
_TRANSITIONS: Final[dict[RunStatus, frozenset[RunStatus]]] = {
    RunStatus.PENDING: frozenset({RunStatus.RUNNING, RunStatus.SKIPPED, RunStatus.FAILED}),
    RunStatus.RUNNING: frozenset({RunStatus.SUMMARIZED, RunStatus.FAILED}),
    RunStatus.SUMMARIZED: frozenset({RunStatus.POSTED, RunStatus.FAILED}),
    RunStatus.POSTED: frozenset(),
    RunStatus.FAILED: frozenset(),
    RunStatus.SKIPPED: frozenset(),
}


def can_transition(current: RunStatus, target: RunStatus) -> bool:
    """True when a run may move from one state to the other."""

    return target in _TRANSITIONS[current]


def assert_transition(current: RunStatus, target: RunStatus) -> None:
    """Raise unless the transition is legal."""

    if not can_transition(current, target):
        allowed = ", ".join(sorted(item.value for item in _TRANSITIONS[current])) or "nothing"
        raise StoreError(f"A {current.value} run cannot become {target.value}. Allowed: {allowed}.")


@dataclass(frozen=True)
class Migration:
    """One ordered schema change."""

    order: int
    name: str
    sql: str


def load_migrations() -> tuple[Migration, ...]:
    """Read the bundled migrations in numeric order.

    Ordered by the numeric prefix rather than by filename string, so migration
    10 sorts after migration 9 instead of after migration 1.
    """

    package = resources.files("prcritiq") / "migrations"
    found: list[Migration] = []
    for entry in package.iterdir():
        match = _MIGRATION_NAME.match(entry.name)
        if match is None:
            continue
        found.append(
            Migration(
                order=int(match.group("order")),
                name=entry.name,
                sql=entry.read_text(encoding="utf-8"),
            )
        )
    if not found:
        raise StoreError("No migrations were found in the prcritiq package")
    return tuple(sorted(found, key=lambda item: item.order))


@contextmanager
def connect(dsn: str) -> Iterator[psycopg.Connection]:
    """Open a connection with dict rows, closing it afterwards."""

    connection = psycopg.connect(dsn, row_factory=dict_row)
    try:
        yield connection
    finally:
        connection.close()


def migrate(connection: psycopg.Connection) -> tuple[str, ...]:
    """Apply every migration that has not run yet, and report which ran."""

    with connection.cursor() as cursor:
        cursor.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " name TEXT PRIMARY KEY,"
            " applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
        cursor.execute("SELECT name FROM schema_migrations")
        applied = {row["name"] for row in cursor.fetchall()}

        ran: list[str] = []
        for migration in load_migrations():
            if migration.name in applied:
                continue
            cursor.execute(migration.sql)
            cursor.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (migration.name,))
            ran.append(migration.name)
    connection.commit()
    return tuple(ran)


def create_or_get_run(
    connection: psycopg.Connection,
    *,
    idempotency_key: str,
    repo_full_name: str,
    pr_number: int,
    head_sha: str,
    mode: str = "dry-run",
) -> tuple[dict[str, Any], bool]:
    """Create a run, or return the existing one for this idempotency key.

    Returns the row and whether it was created. The UNIQUE constraint does the
    deciding, so a redelivered webhook resolves to the same run even when two
    callers arrive at once.
    """

    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO review_runs"
            " (idempotency_key, repo_full_name, pr_number, head_sha, mode, status)"
            " VALUES (%s, %s, %s, %s, %s, %s)"
            " ON CONFLICT (idempotency_key) DO NOTHING"
            " RETURNING *",
            (idempotency_key, repo_full_name, pr_number, head_sha, mode, RunStatus.PENDING.value),
        )
        row = cursor.fetchone()
        if row is not None:
            connection.commit()
            return row, True

        cursor.execute("SELECT * FROM review_runs WHERE idempotency_key = %s", (idempotency_key,))
        existing = cursor.fetchone()
    connection.commit()
    if existing is None:  # pragma: no cover - only on a concurrent delete
        raise StoreError(f"Run for key {idempotency_key!r} vanished during creation")
    return existing, False


def update_run_status(
    connection: psycopg.Connection,
    run_id: int,
    target: RunStatus,
    *,
    summary: str | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    """Move a run to a new state, refusing an illegal transition."""

    with connection.cursor() as cursor:
        cursor.execute("SELECT status FROM review_runs WHERE id = %s FOR UPDATE", (run_id,))
        row = cursor.fetchone()
        if row is None:
            raise StoreError(f"No review run with id {run_id}")
        assert_transition(RunStatus(row["status"]), target)

        cursor.execute(
            "UPDATE review_runs SET status = %s, summary = COALESCE(%s, summary),"
            " error = COALESCE(%s, error), updated_at = now()"
            " WHERE id = %s RETURNING *",
            (target.value, summary, error, run_id),
        )
        updated = cursor.fetchone()
    connection.commit()
    if updated is None:  # pragma: no cover - the row was locked above
        raise StoreError(f"No review run with id {run_id}")
    return updated


def record_routing(
    connection: psycopg.Connection,
    run_id: int,
    *,
    provider: str | None,
    model: str | None,
    reason: str | None,
    trace_provider: str | None = None,
    trace_id: str | None = None,
) -> None:
    """Attach the routing decision and trace linkage to a run."""

    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE review_runs SET model_provider = %s, model_name = %s, routing_reason = %s,"
            " trace_provider = %s, trace_id = %s, updated_at = now() WHERE id = %s",
            (provider, model, reason, trace_provider, trace_id, run_id),
        )
    connection.commit()


def record_files(
    connection: psycopg.Connection,
    run_id: int,
    file_diffs: Sequence[Any],
    outcomes: Sequence[GuardrailOutcome],
) -> int:
    """Store the per-file guardrail verdicts for a run."""

    if len(file_diffs) != len(outcomes):
        raise StoreError("file diffs and guardrail outcomes must be the same length")
    with connection.cursor() as cursor:
        for file_diff, outcome in zip(file_diffs, outcomes, strict=True):
            cursor.execute(
                "INSERT INTO review_files"
                " (run_id, path, language, status, guardrail_decision, guardrail_reason,"
                "  changed_lines)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s)"
                " ON CONFLICT (run_id, path) DO NOTHING",
                (
                    run_id,
                    file_diff.path,
                    file_diff.language,
                    file_diff.status,
                    outcome.decision.value,
                    outcome.reason,
                    len(file_diff.changed_new_lines),
                ),
            )
    connection.commit()
    return len(file_diffs)


def record_tool_runs(
    connection: psycopg.Connection,
    run_id: int,
    tool_runs: Sequence[ToolRun],
) -> int:
    """Store tool outcomes, including the tools that declined to run."""

    with connection.cursor() as cursor:
        for run in tool_runs:
            diagnostics = [
                {
                    "path": item.path,
                    "line": item.line,
                    "column": item.column,
                    "code": item.code,
                    "message": item.message,
                    "on_changed_line": item.on_changed_line,
                }
                for item in run.diagnostics
            ]
            cursor.execute(
                "INSERT INTO tool_results"
                " (run_id, tool, status, reason, exit_code, duration_seconds, diagnostics)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s)"
                " ON CONFLICT (run_id, tool) DO NOTHING",
                (
                    run_id,
                    run.tool,
                    run.status.value,
                    run.reason,
                    run.exit_code,
                    run.duration_seconds,
                    json.dumps(diagnostics),
                ),
            )
    connection.commit()
    return len(tool_runs)


def record_findings(
    connection: psycopg.Connection,
    run_id: int,
    reviewed: Sequence[ReviewedFinding],
) -> int:
    """Store published and suppressed findings alike.

    Suppressed rows are kept because the invalid-line and no-evidence rates a
    benchmark reports are computed from them (AC11).
    """

    with connection.cursor() as cursor:
        for item in reviewed:
            candidate = item.candidate
            cursor.execute(
                "INSERT INTO review_findings"
                " (run_id, file_path, line, severity, confidence, category, finding,"
                "  evidence, suggested_fix, source_refs, publish_decision, suppression_reason)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    run_id,
                    candidate.file_path,
                    candidate.line,
                    candidate.severity.value,
                    candidate.confidence,
                    candidate.category.value,
                    candidate.finding,
                    candidate.evidence,
                    candidate.suggested_fix,
                    json.dumps(list(candidate.source_refs)),
                    item.publish_decision,
                    item.suppression_reason.value if item.suppression_reason else None,
                ),
            )
    connection.commit()
    return len(reviewed)


def get_run(connection: psycopg.Connection, run_id: int) -> dict[str, Any] | None:
    with connection.cursor() as cursor:
        cursor.execute("SELECT * FROM review_runs WHERE id = %s", (run_id,))
        return cursor.fetchone()


def find_run_by_key(connection: psycopg.Connection, idempotency_key: str) -> dict[str, Any] | None:
    with connection.cursor() as cursor:
        cursor.execute("SELECT * FROM review_runs WHERE idempotency_key = %s", (idempotency_key,))
        return cursor.fetchone()
