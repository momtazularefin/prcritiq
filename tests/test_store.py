"""Tests for the Postgres run store.

Pure tests always run. The integration tests need a real database and are
skipped without one, because a store test that never opens a connection proves
the schema compiles in someone's head rather than in Postgres:

    docker compose up -d
    DATABASE_URL=postgresql://prcritiq:prcritiq@localhost:5433/prcritiq uv run pytest
"""

from __future__ import annotations

import os

import pytest

from prcritiq.config import Settings
from prcritiq.findings import CandidateFinding, ReviewedFinding
from prcritiq.store import (
    RunStatus,
    StoreError,
    assert_transition,
    can_transition,
    connect,
    create_or_get_run,
    find_run_by_key,
    get_run,
    load_migrations,
    migrate,
    record_files,
    record_findings,
    record_tool_runs,
    update_run_status,
)
from prcritiq.tracing import trace_run, tracing_is_available

DATABASE_URL = os.environ.get("DATABASE_URL")
needs_postgres = pytest.mark.skipif(
    not DATABASE_URL, reason="DATABASE_URL is not set; run `docker compose up -d` first"
)


@pytest.fixture
def clean_database():
    """Start every integration test from an empty store.

    Without this the suite passes once and then fails, because a run created by
    an earlier execution already holds the idempotency key the test expects to
    claim.
    """

    with connect(DATABASE_URL) as conn:
        migrate(conn)
        with conn.cursor() as cursor:
            cursor.execute(
                "TRUNCATE review_runs, benchmark_runs, installations, repositories"
                " RESTART IDENTITY CASCADE"
            )
        conn.commit()
        yield conn


def reviewed(publish: bool = True, **overrides) -> ReviewedFinding:
    defaults = {
        "file_path": "src/app.py",
        "line": 4,
        "severity": "high",
        "confidence": 90,
        "category": "bug",
        "finding": "retry divides by n without guarding against a zero argument.",
        "evidence": "Line 4 computes 1 / n with n straight from the caller.",
        "suggested_fix": "Raise ValueError when n is zero.",
        "source_refs": ["src/app.py:1-2:abcd1234"],
    }
    defaults.update(overrides)
    return ReviewedFinding(
        candidate=CandidateFinding(**defaults),
        publish_decision="publish" if publish else "suppress",
        suppression_reason=None if publish else "invalid_line",
    )


class TestMigrationLoading:
    def test_migrations_are_discovered_and_ordered_numerically(self) -> None:
        """Ordering by the numeric prefix, so 10 follows 9 rather than 1."""

        migrations = load_migrations()

        assert migrations
        assert [item.order for item in migrations] == sorted(item.order for item in migrations)
        assert all(item.sql.strip() for item in migrations)

    def test_every_designed_table_is_created_by_the_schema(self) -> None:
        sql = "\n".join(item.sql for item in load_migrations()).lower()

        for table in (
            "installations",
            "repositories",
            "pull_requests",
            "review_runs",
            "review_files",
            "tool_results",
            "review_findings",
            "posted_comments",
            "benchmark_runs",
            "benchmark_cases",
        ):
            assert f"create table if not exists {table}" in sql, table


class TestRunStateMachine:
    @pytest.mark.parametrize(
        ("current", "target"),
        [
            (RunStatus.PENDING, RunStatus.RUNNING),
            (RunStatus.PENDING, RunStatus.SKIPPED),
            (RunStatus.RUNNING, RunStatus.SUMMARIZED),
            (RunStatus.RUNNING, RunStatus.FAILED),
            (RunStatus.SUMMARIZED, RunStatus.POSTED),
        ],
    )
    def test_legal_transitions(self, current: RunStatus, target: RunStatus) -> None:
        assert can_transition(current, target)

    @pytest.mark.parametrize(
        ("current", "target"),
        [
            (RunStatus.PENDING, RunStatus.POSTED),
            (RunStatus.PENDING, RunStatus.SUMMARIZED),
            (RunStatus.SUMMARIZED, RunStatus.RUNNING),
            (RunStatus.POSTED, RunStatus.RUNNING),
            (RunStatus.FAILED, RunStatus.RUNNING),
            (RunStatus.SKIPPED, RunStatus.RUNNING),
        ],
    )
    def test_illegal_transitions(self, current: RunStatus, target: RunStatus) -> None:
        assert not can_transition(current, target)

    def test_terminal_states_cannot_be_reopened(self) -> None:
        """A finished run must not be quietly rewritten."""

        for terminal in (RunStatus.POSTED, RunStatus.FAILED, RunStatus.SKIPPED):
            with pytest.raises(StoreError, match="Allowed: nothing"):
                assert_transition(terminal, RunStatus.RUNNING)

    def test_the_error_names_what_was_allowed(self) -> None:
        with pytest.raises(StoreError, match="Allowed: failed, running, skipped"):
            assert_transition(RunStatus.PENDING, RunStatus.POSTED)


class TestTracing:
    def test_tracing_is_off_without_credentials(self) -> None:
        assert not tracing_is_available(Settings(trace_provider="langsmith"))

    def test_tracing_is_off_when_not_selected(self) -> None:
        assert not tracing_is_available(Settings(trace_provider="none", langsmith_api_key="secret"))

    def test_a_disabled_trace_records_no_identifier(self) -> None:
        """Better to record no trace than one nobody can resolve."""

        with trace_run(Settings()) as handle:
            assert handle.enabled is False
            assert handle.trace_id is None
            assert handle.stored_provider is None


@needs_postgres
class TestStoreAgainstPostgres:
    @pytest.fixture
    def connection(self, clean_database):
        return clean_database

    @pytest.fixture
    def key(self, request) -> str:
        return f"dry_run:{request.node.name}"

    def test_migrations_are_idempotent(self, connection) -> None:
        assert migrate(connection) == ()

    def test_a_run_is_created_and_readable(self, connection, key) -> None:
        row, created = create_or_get_run(
            connection,
            idempotency_key=key,
            repo_full_name="example/repo",
            pr_number=7,
            head_sha="head222",
        )

        assert created is True
        assert row["status"] == RunStatus.PENDING.value
        assert get_run(connection, int(row["id"]))["idempotency_key"] == key

    def test_a_repeated_key_resolves_to_the_same_run(self, connection, key) -> None:
        """A redelivered webhook must not create a second run (AC1, AC10)."""

        first, created_first = create_or_get_run(
            connection,
            idempotency_key=key,
            repo_full_name="example/repo",
            pr_number=7,
            head_sha="head222",
        )
        second, created_second = create_or_get_run(
            connection,
            idempotency_key=key,
            repo_full_name="example/repo",
            pr_number=7,
            head_sha="head222",
        )

        assert created_first is True
        assert created_second is False
        assert first["id"] == second["id"]

    def test_status_moves_through_legal_states(self, connection, key) -> None:
        row, _ = create_or_get_run(
            connection,
            idempotency_key=key,
            repo_full_name="example/repo",
            pr_number=7,
            head_sha="head",
        )
        run_id = int(row["id"])

        update_run_status(connection, run_id, RunStatus.RUNNING)
        final = update_run_status(
            connection, run_id, RunStatus.SUMMARIZED, summary="1 of 1 met the bar."
        )

        assert final["status"] == RunStatus.SUMMARIZED.value
        assert final["summary"] == "1 of 1 met the bar."

    def test_an_illegal_transition_is_refused_by_the_store(self, connection, key) -> None:
        row, _ = create_or_get_run(
            connection,
            idempotency_key=key,
            repo_full_name="example/repo",
            pr_number=7,
            head_sha="head",
        )

        with pytest.raises(StoreError, match="cannot become posted"):
            update_run_status(connection, int(row["id"]), RunStatus.POSTED)

    def test_findings_are_stored_with_their_verdicts(self, connection, key) -> None:
        row, _ = create_or_get_run(
            connection,
            idempotency_key=key,
            repo_full_name="example/repo",
            pr_number=7,
            head_sha="head",
        )
        run_id = int(row["id"])

        stored = record_findings(connection, run_id, [reviewed(), reviewed(publish=False)])

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT publish_decision, suppression_reason FROM review_findings"
                " WHERE run_id = %s ORDER BY id",
                (run_id,),
            )
            rows = cursor.fetchall()

        assert stored == 2
        assert rows[0]["publish_decision"] == "publish"
        assert rows[0]["suppression_reason"] is None
        assert rows[1]["suppression_reason"] == "invalid_line"

    def test_the_schema_refuses_a_suppressed_finding_with_no_reason(self, connection, key) -> None:
        """A rate computed from rows like this would be silently wrong."""

        row, _ = create_or_get_run(
            connection,
            idempotency_key=key,
            repo_full_name="example/repo",
            pr_number=7,
            head_sha="head",
        )

        with pytest.raises(Exception, match="review_findings_check"), connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO review_findings (run_id, file_path, line, severity,"
                " confidence, category, finding, publish_decision, suppression_reason)"
                " VALUES (%s, 'a.py', 1, 'low', 10, 'bug', 'x', 'suppress', NULL)",
                (int(row["id"]),),
            )
        connection.rollback()

    def test_files_and_tools_are_recorded_once_per_run(self, connection, key) -> None:
        from prcritiq.diff import build_file_diff
        from prcritiq.guardrails import apply_guardrails
        from prcritiq.tools import ToolRun, ToolStatus

        row, _ = create_or_get_run(
            connection,
            idempotency_key=key,
            repo_full_name="example/repo",
            pr_number=7,
            head_sha="head",
        )
        run_id = int(row["id"])
        diffs = [
            build_file_diff(
                path="src/app.py",
                status="modified",
                additions=1,
                deletions=0,
                changes=1,
                patch="@@ -1,1 +1,2 @@\n a\n+b\n",
            )
        ]
        outcomes = apply_guardrails(diffs, Settings())
        runs = [ToolRun("ruff", ToolStatus.OK, "clean")]

        record_files(connection, run_id, diffs, outcomes)
        record_files(connection, run_id, diffs, outcomes)
        record_tool_runs(connection, run_id, runs)
        record_tool_runs(connection, run_id, runs)

        with connection.cursor() as cursor:
            cursor.execute("SELECT count(*) AS n FROM review_files WHERE run_id = %s", (run_id,))
            files = cursor.fetchone()["n"]
            cursor.execute("SELECT count(*) AS n FROM tool_results WHERE run_id = %s", (run_id,))
            tools = cursor.fetchone()["n"]

        assert files == 1
        assert tools == 1

    def test_lookup_by_key_finds_nothing_for_an_unknown_key(self, connection) -> None:
        assert find_run_by_key(connection, "dry_run:never-created") is None


@needs_postgres
class TestPersistedReviewRun:
    """The dry-run path writing to a real database, end to end."""

    @pytest.fixture(autouse=True)
    def _isolate(self, clean_database):
        return clean_database

    def _report(self, stub_client, **kwargs):
        from prcritiq.review import run_dry_run

        return run_dry_run(
            repo="example/repo",
            pr_number=7,
            settings=Settings(database_url=DATABASE_URL),
            client=stub_client,
            persist=True,
            **kwargs,
        )

    def test_a_run_is_persisted_and_reported(self, stub_client) -> None:
        report = self._report(stub_client)

        assert report.run is not None
        assert report.run.created is True
        assert report.run.status == RunStatus.SUMMARIZED.value

        with connect(DATABASE_URL) as conn:
            stored = get_run(conn, report.run.run_id)
        assert stored["repo_full_name"] == "example/repo"
        assert stored["pr_number"] == 7

    def test_rerunning_the_same_head_does_not_duplicate_the_run(self, stub_client) -> None:
        """The whole point of the idempotency key (AC1, AC10)."""

        first = self._report(stub_client)
        second = self._report(stub_client)

        assert first.run.run_id == second.run.run_id
        assert second.run.created is False
        assert "already existed" in second.run.note

    def test_findings_and_routing_reach_the_database(self, stub_client) -> None:
        from prcritiq.findings import DraftedFindings
        from prcritiq.providers import MockProvider

        drafted = DraftedFindings(findings=[reviewed().candidate])
        report = self._report(stub_client, include_review=True, provider=MockProvider(drafted))

        with connect(DATABASE_URL) as conn:
            stored = get_run(conn, report.run.run_id)
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT count(*) AS n FROM review_findings WHERE run_id = %s",
                    (report.run.run_id,),
                )
                findings = cursor.fetchone()["n"]

        assert stored["model_provider"] == "anthropic"
        assert stored["model_name"] == "claude-opus-5"
        assert stored["routing_reason"]
        assert findings == 1

    def test_persistence_without_a_dsn_fails_clearly(self, stub_client) -> None:
        from prcritiq.review import run_dry_run

        with pytest.raises(StoreError, match="DATABASE_URL is not set"):
            run_dry_run(
                repo="example/repo",
                pr_number=7,
                settings=Settings(database_url=None),
                client=stub_client,
                persist=True,
            )
