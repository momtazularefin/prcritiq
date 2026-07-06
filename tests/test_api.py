from __future__ import annotations

from fastapi.testclient import TestClient

from prcritiq.api import create_app


def test_health_returns_m0_scaffold_status() -> None:
    client = TestClient(create_app())

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["service"] == "prcritiq"
    assert response.json()["implementation_status"] == "m0_scaffold"


def test_demo_review_returns_non_posting_scaffold_report() -> None:
    client = TestClient(create_app())

    response = client.post(
        "/demo/review",
        json={"repo": "https://github.com/example/repo", "pr": 1, "mode": "dry-run"},
    )

    body = response.json()
    assert response.status_code == 200
    assert body["implementation_status"] == "m0_scaffold"
    assert body["posted_comments"] == 0
    assert body["findings"] == []
    assert "no PR comment was posted" in body["message"]
