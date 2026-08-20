from __future__ import annotations

import json

import httpx

from prcritiq.github import GitHubClient


def test_github_client_fetches_pull_request_metadata() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/repos/example/repo/pulls/7"
        return httpx.Response(
            200,
            json={
                "number": 7,
                "title": "Fix validation",
                "state": "open",
                "base": {"sha": "base-sha"},
                "head": {"sha": "head-sha"},
                "user": {"login": "octocat"},
                "html_url": "https://github.com/example/repo/pull/7",
            },
        )

    http_client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://api.github.test",
    )
    client = GitHubClient(http_client=http_client)

    metadata = client.get_pull_request("example/repo", 7)

    assert metadata.repo == "example/repo"
    assert metadata.number == 7
    assert metadata.base_sha == "base-sha"
    assert metadata.head_sha == "head-sha"


def test_github_client_lists_changed_files() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/repos/example/repo/pulls/7/files"
        assert request.url.params["per_page"] == "100"
        return httpx.Response(
            200,
            json=[
                {
                    "filename": "src/app.py",
                    "status": "modified",
                    "additions": 3,
                    "deletions": 1,
                    "changes": 4,
                    "patch": "@@ -1 +1 @@\n-print('old')\n+print('new')",
                }
            ],
        )

    http_client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://api.github.test",
    )
    client = GitHubClient(http_client=http_client)

    files = client.list_changed_files("example/repo", 7)

    assert len(files) == 1
    assert files[0].filename == "src/app.py"
    assert files[0].patch is not None


def test_github_client_uses_json_response_shape() -> None:
    payload = [
        {
            "filename": "README.md",
            "status": "added",
            "additions": 1,
            "deletions": 0,
            "changes": 1,
        }
    ]

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=json.dumps(payload).encode("utf-8"))

    http_client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://api.github.test",
    )
    client = GitHubClient(http_client=http_client)

    assert client.list_changed_files("example/repo", 1)[0].filename == "README.md"
