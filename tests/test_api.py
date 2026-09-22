from __future__ import annotations

import dataclasses
import json
import os

import pytest
from fastapi.testclient import TestClient

from prcritiq.api import create_app
from prcritiq.config import Settings
from prcritiq.github import GitHubClientError
from prcritiq.schemas import IMPLEMENTATION_STATUS
from prcritiq.store import connect, migrate
from prcritiq.webhooks import compute_webhook_signature

DATABASE_URL = os.environ.get("DATABASE_URL")
needs_postgres = pytest.mark.skipif(
    not DATABASE_URL, reason="DATABASE_URL is not set; run `docker compose up -d` first"
)

PULL_REQUEST_EVENT = {
    "action": "synchronize",
    "installation": {"id": 101},
    "repository": {"id": 202, "full_name": "example/repo", "private": False},
    "pull_request": {"number": 7, "head": {"sha": "head222"}},
}


def deliver(
    client: TestClient, payload: dict, *, delivery: str = "delivery-1", secret="top-secret"
):
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return client.post(
        "/webhooks/github",
        content=body,
        headers={
            "content-type": "application/json",
            "x-hub-signature-256": compute_webhook_signature(secret=secret, body=body),
            "x-github-event": "pull_request",
            "x-github-delivery": delivery,
        },
    )


DEMO_REQUEST = {"repo": "https://github.com/example/repo", "pr": 7, "mode": "dry-run"}


def test_health_returns_m1_status() -> None:
    client = TestClient(create_app())

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["service"] == "prcritiq"
    assert response.json()["implementation_status"] == IMPLEMENTATION_STATUS


def test_health_answers_a_head_probe() -> None:
    client = TestClient(create_app(Settings()))

    assert client.head("/health").status_code == 200


def test_demo_review_returns_a_non_posting_report(patched_github) -> None:
    client = TestClient(create_app())

    response = client.post(
        "/demo/review",
        json={"repo": "https://github.com/example/repo", "pr": 7, "mode": "dry-run"},
    )

    body = response.json()
    assert response.status_code == 200
    assert body["implementation_status"] == IMPLEMENTATION_STATUS
    assert body["idempotency_key"].startswith("dry_run:")
    assert body["reviewable_files"] == 1
    assert body["skipped_by_decision"] == {
        "skipped_lockfile": 1,
        "skipped_unsupported_language": 1,
    }
    assert body["posted_comments"] == 0
    assert body["findings"] == []


def test_demo_review_maps_a_github_failure_to_bad_gateway(monkeypatch) -> None:
    def explode(**_kwargs):
        raise GitHubClientError("GitHub request failed: 404 Not Found")

    monkeypatch.setattr("prcritiq.api.run_dry_run", explode)
    client = TestClient(create_app())

    response = client.post(
        "/demo/review",
        json={"repo": "https://github.com/example/repo", "pr": 7, "mode": "dry-run"},
    )

    assert response.status_code == 502
    assert "404" in response.json()["detail"]


def test_github_webhook_without_a_database_asks_for_redelivery() -> None:
    client = TestClient(create_app(Settings(github_webhook_secret="top-secret")))

    response = deliver(client, PULL_REQUEST_EVENT)

    assert response.status_code == 503
    assert "DATABASE_URL" in response.json()["detail"]


def test_github_webhook_ignores_a_private_repository() -> None:
    client = TestClient(create_app(Settings(github_webhook_secret="top-secret")))
    payload = {**PULL_REQUEST_EVENT, "repository": {"id": 202, "full_name": "a/b", "private": True}}

    response = deliver(client, payload)

    assert response.status_code == 200
    assert response.json()["accepted"] is False
    assert "private" in response.json()["message"]


def test_github_webhook_refuses_an_incomplete_payload() -> None:
    client = TestClient(create_app(Settings(github_webhook_secret="top-secret")))
    payload = {key: value for key, value in PULL_REQUEST_EVENT.items() if key != "installation"}

    assert deliver(client, payload).status_code == 400


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
def webhook_client(stub_client):
    settings = Settings(github_webhook_secret="top-secret", database_url=DATABASE_URL)
    app = create_app(settings, github_client_factory=lambda _settings, _id: stub_client)
    return TestClient(app)


@needs_postgres
@pytest.mark.usefixtures("clean_database")
class TestWebhookRuns:
    def test_an_event_becomes_a_recorded_dry_run(self, webhook_client, stub_client) -> None:
        response = deliver(webhook_client, PULL_REQUEST_EVENT)

        data = response.json()
        assert response.status_code == 200
        assert data["accepted"] is True
        assert data["idempotency_key"].startswith("review_run:")
        run = webhook_client.get(f"/runs/{data['run_id']}").json()
        assert run["status"] == "summarized"
        assert run["mode"] == "webhook-dry-run"
        assert run["files"] == 3
        assert run["reviewable_files"] == 1
        assert not any(call[0] == "create_review_comment" for call in stub_client.calls)

    def test_a_redelivery_returns_the_same_run_without_running_it(
        self, webhook_client, stub_client
    ) -> None:
        first = deliver(webhook_client, PULL_REQUEST_EVENT).json()
        stub_client.calls.clear()

        second = deliver(webhook_client, PULL_REQUEST_EVENT).json()

        assert second["run_id"] == first["run_id"]
        assert second["run_status"] == "summarized"
        assert "not run again" in second["message"]
        assert stub_client.calls == []

    def test_a_new_delivery_is_a_new_run(self, webhook_client) -> None:
        first = deliver(webhook_client, PULL_REQUEST_EVENT, delivery="delivery-1").json()
        second = deliver(webhook_client, PULL_REQUEST_EVENT, delivery="delivery-2").json()

        assert second["run_id"] != first["run_id"]

    def test_an_unknown_run_is_not_found(self, webhook_client) -> None:
        assert webhook_client.get("/runs/999999").status_code == 404


def test_run_status_without_a_database_is_unavailable() -> None:
    client = TestClient(create_app(Settings()))

    assert client.get("/runs/1").status_code == 503


def test_github_webhook_rejects_invalid_signature() -> None:
    settings = Settings(github_webhook_secret="top-secret")
    client = TestClient(create_app(settings))

    response = client.post(
        "/webhooks/github",
        json={"action": "opened"},
        headers={
            "x-hub-signature-256": "sha256=bad",
            "x-github-event": "pull_request",
            "x-github-delivery": "delivery-1",
        },
    )

    assert response.status_code == 401


def test_github_webhook_ignores_unsupported_event() -> None:
    settings = Settings(github_webhook_secret="top-secret")
    client = TestClient(create_app(settings))
    body = b'{"zen":"Keep it logically awesome."}'
    signature = compute_webhook_signature(secret="top-secret", body=body)

    response = client.post(
        "/webhooks/github",
        content=body,
        headers={
            "content-type": "application/json",
            "x-hub-signature-256": signature,
            "x-github-event": "ping",
            "x-github-delivery": "delivery-2",
        },
    )

    assert response.status_code == 200
    assert response.json()["accepted"] is False


def test_the_demo_can_be_switched_off() -> None:
    client = TestClient(create_app(Settings(demo_enabled=False)))

    assert client.post("/demo/review", json=DEMO_REQUEST).status_code == 404


def test_the_demo_is_rate_limited_per_client(patched_github) -> None:
    client = TestClient(create_app(Settings(demo_requests_per_minute=2)))

    statuses = [client.post("/demo/review", json=DEMO_REQUEST).status_code for _ in range(3)]

    assert statuses == [200, 200, 429]
    limited = client.post("/demo/review", json=DEMO_REQUEST)
    assert int(limited.headers["retry-after"]) >= 1


def test_the_demo_refuses_a_private_repository(patched_github) -> None:
    patched_github.metadata = dataclasses.replace(patched_github.metadata, repo_private=True)
    client = TestClient(create_app(Settings()))

    response = client.post("/demo/review", json=DEMO_REQUEST)

    assert response.status_code == 403
    assert ("list_changed_files", "example/repo", 7) not in patched_github.calls


def test_the_demo_reviews_a_private_repository_only_when_allowed(patched_github) -> None:
    patched_github.metadata = dataclasses.replace(patched_github.metadata, repo_private=True)
    client = TestClient(create_app(Settings(allow_private_repos=True)))

    assert client.post("/demo/review", json=DEMO_REQUEST).status_code == 200
