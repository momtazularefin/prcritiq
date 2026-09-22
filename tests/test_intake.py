"""Tests for webhook intake: acceptance, execution, and the dry-run guarantee.

Parsing is pure and always runs. Acceptance and execution need Postgres and
are skipped without it:

    docker compose up -d
    DATABASE_URL=postgresql://prcritiq:prcritiq@localhost:5433/prcritiq uv run pytest
"""

from __future__ import annotations

import dataclasses
import os

import pytest

from prcritiq.config import Settings
from prcritiq.findings import CandidateFinding, DraftedFindings
from prcritiq.github import GitHubClientError
from prcritiq.intake import (
    WEBHOOK_MODE,
    accept_event,
    execute_run,
    parse_pull_request_event,
    run_status,
)
from prcritiq.providers import MockProvider
from prcritiq.store import StoreError, connect, migrate
from prcritiq.webhooks import WebhookPayloadError, build_review_run_key_from_payload

from conftest import StubGitHubClient

DATABASE_URL = os.environ.get("DATABASE_URL")
needs_postgres = pytest.mark.skipif(
    not DATABASE_URL, reason="DATABASE_URL is not set; run `docker compose up -d` first"
)


def event_payload(**overrides) -> dict:
    payload = {
        "action": "synchronize",
        "installation": {"id": 101},
        "repository": {"id": 202, "full_name": "example/repo", "private": False},
        "pull_request": {"number": 7, "head": {"sha": "head222"}},
    }
    payload.update(overrides)
    return payload


class TestParsing:
    def test_a_complete_payload_parses(self) -> None:
        event = parse_pull_request_event(event_payload(), delivery_id="d-1")

        assert event.repo_full_name == "example/repo"
        assert event.installation_id == 101
        assert event.pr_number == 7
        assert event.head_sha == "head222"
        assert event.repo_private is False

    def test_the_key_matches_the_payload_key_builder(self) -> None:
        payload = event_payload()

        event = parse_pull_request_event(payload, delivery_id="d-1")

        assert event.idempotency_key == build_review_run_key_from_payload(
            payload, delivery_id="d-1"
        )

    def test_a_new_delivery_is_a_new_key(self) -> None:
        first = parse_pull_request_event(event_payload(), delivery_id="d-1")
        second = parse_pull_request_event(event_payload(), delivery_id="d-2")

        assert first.idempotency_key != second.idempotency_key

    def test_missing_visibility_is_treated_as_private(self) -> None:
        payload = event_payload(repository={"id": 202, "full_name": "example/repo"})

        assert parse_pull_request_event(payload, delivery_id="d-1").repo_private is True

    @pytest.mark.parametrize("missing", ["installation", "repository", "pull_request"])
    def test_a_missing_section_is_refused(self, missing: str) -> None:
        payload = event_payload()
        del payload[missing]

        with pytest.raises(WebhookPayloadError):
            parse_pull_request_event(payload, delivery_id="d-1")

    def test_acceptance_without_a_database_is_refused(self) -> None:
        event = parse_pull_request_event(event_payload(), delivery_id="d-1")

        with pytest.raises(StoreError, match="DATABASE_URL"):
            accept_event(Settings(), event)


@pytest.fixture
def clean_database():
    with connect(DATABASE_URL) as conn:
        migrate(conn)
        with conn.cursor() as cursor:
            cursor.execute(
                "TRUNCATE review_runs, benchmark_runs, installations, repositories"
                " RESTART IDENTITY CASCADE"
            )
        conn.commit()
        yield conn


@pytest.fixture
def settings() -> Settings:
    return Settings(database_url=DATABASE_URL)


def factory_for(client):
    """A client factory that records the installation it was asked for."""

    requested: list[int | None] = []

    def build(_settings: Settings, installation_id: int | None):
        requested.append(installation_id)
        return client

    build.requested = requested
    return build


@needs_postgres
@pytest.mark.usefixtures("clean_database")
class TestExecution:
    def test_acceptance_creates_one_pending_webhook_run(self, settings) -> None:
        event = parse_pull_request_event(event_payload(), delivery_id="d-1")

        accepted = accept_event(settings, event)

        assert accepted.created is True
        assert accepted.status == "pending"
        row = run_status(settings, accepted.run_id)
        assert row["mode"] == WEBHOOK_MODE
        assert row["idempotency_key"] == event.idempotency_key

    def test_a_redelivery_resolves_to_the_same_run(self, settings) -> None:
        event = parse_pull_request_event(event_payload(), delivery_id="d-1")

        first = accept_event(settings, event)
        second = accept_event(settings, event)

        assert second.created is False
        assert second.run_id == first.run_id

    def test_a_run_is_summarized_with_its_guardrail_verdicts(
        self, settings, stub_client: StubGitHubClient
    ) -> None:
        event = parse_pull_request_event(event_payload(), delivery_id="d-1")
        accepted = accept_event(settings, event)
        factory = factory_for(stub_client)

        final = execute_run(settings, event, accepted.run_id, client_factory=factory)

        assert final == "summarized"
        assert factory.requested == [101]
        row = run_status(settings, accepted.run_id)
        assert row["files"] == 3
        assert row["reviewable_files"] == 1
        assert row["findings"] == 0
        assert "nothing was posted" in row["summary"]

    def test_model_review_runs_only_when_enabled_and_never_posts(
        self, settings, stub_client: StubGitHubClient
    ) -> None:
        enabled = dataclasses.replace(settings, webhook_review=True)
        event = parse_pull_request_event(event_payload(), delivery_id="d-1")
        accepted = accept_event(enabled, event)
        provider = MockProvider(
            DraftedFindings(
                findings=[
                    CandidateFinding(
                        file_path="src/app.py",
                        line=3,
                        severity="medium",
                        category="bug",
                        confidence=90,
                        finding="VALUE is a module constant with no use in this change.",
                        evidence="Line 3 adds VALUE = 1 and nothing in the diff reads it.",
                        suggested_fix="Remove VALUE or use it.",
                    )
                ]
            )
        )

        final = execute_run(
            enabled,
            event,
            accepted.run_id,
            client_factory=factory_for(stub_client),
            provider=provider,
        )

        assert final == "summarized"
        assert len(provider.calls) == 1
        row = run_status(enabled, accepted.run_id)
        assert row["findings"] == 1
        assert row["status"] != "posted"
        assert "nothing was posted" in row["summary"]

    def test_a_run_is_not_executed_twice(self, settings, stub_client) -> None:
        event = parse_pull_request_event(event_payload(), delivery_id="d-1")
        accepted = accept_event(settings, event)
        execute_run(settings, event, accepted.run_id, client_factory=factory_for(stub_client))
        stub_client.calls.clear()

        again = execute_run(
            settings, event, accepted.run_id, client_factory=factory_for(stub_client)
        )

        assert again == "summarized"
        assert stub_client.calls == []

    def test_a_moved_head_skips_the_run(self, settings, stub_client) -> None:
        event = parse_pull_request_event(
            event_payload(pull_request={"number": 7, "head": {"sha": "older111"}}),
            delivery_id="d-1",
        )
        accepted = accept_event(settings, event)

        final = execute_run(
            settings, event, accepted.run_id, client_factory=factory_for(stub_client)
        )

        assert final == "skipped"
        assert "moved" in run_status(settings, accepted.run_id)["summary"]
        assert ("list_changed_files", "example/repo", 7) not in stub_client.calls

    def test_a_private_repository_is_skipped_before_its_files_are_read(
        self, settings, stub_client
    ) -> None:
        stub_client.metadata = dataclasses.replace(stub_client.metadata, repo_private=True)
        event = parse_pull_request_event(event_payload(), delivery_id="d-1")
        accepted = accept_event(settings, event)

        final = execute_run(
            settings, event, accepted.run_id, client_factory=factory_for(stub_client)
        )

        assert final == "skipped"
        assert ("list_changed_files", "example/repo", 7) not in stub_client.calls

    def test_a_github_failure_is_recorded_on_the_run(self, settings) -> None:
        event = parse_pull_request_event(event_payload(), delivery_id="d-1")
        accepted = accept_event(settings, event)

        def refuse(_settings, _installation_id):
            raise GitHubClientError("GitHub refused an installation token: 401")

        final = execute_run(settings, event, accepted.run_id, client_factory=refuse)

        assert final == "failed"
        assert "401" in run_status(settings, accepted.run_id)["error"]
