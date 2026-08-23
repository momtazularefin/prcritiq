from __future__ import annotations

import json

from fastapi.testclient import TestClient

from prcritiq.api import create_app
from prcritiq.config import Settings
from prcritiq.github import GitHubClientError
from prcritiq.schemas import IMPLEMENTATION_STATUS
from prcritiq.webhooks import compute_webhook_signature


def test_health_returns_m1_status() -> None:
    client = TestClient(create_app())

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["service"] == "prcritiq"
    assert response.json()["implementation_status"] == IMPLEMENTATION_STATUS


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


def test_github_webhook_accepts_signed_pull_request_event() -> None:
    settings = Settings(github_webhook_secret="top-secret")
    client = TestClient(create_app(settings))
    payload = {
        "action": "synchronize",
        "installation": {"id": 101},
        "repository": {"id": 202},
        "pull_request": {"number": 7, "head": {"sha": "abc123"}},
    }
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    signature = compute_webhook_signature(secret="top-secret", body=body)

    response = client.post(
        "/webhooks/github",
        content=body,
        headers={
            "content-type": "application/json",
            "x-hub-signature-256": signature,
            "x-github-event": "pull_request",
            "x-github-delivery": "delivery-1",
        },
    )

    data = response.json()
    assert response.status_code == 200
    assert data["accepted"] is True
    assert data["action"] == "synchronize"
    assert data["idempotency_key"].startswith("review_run:")


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
