"""Thin GitHub REST client boundary for PR metadata and changed files."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx


class GitHubClientError(RuntimeError):
    """Raised when GitHub returns an unexpected response."""


@dataclass(frozen=True)
class PullRequestMetadata:
    repo: str
    number: int
    title: str
    state: str
    base_sha: str
    head_sha: str
    author_login: str
    html_url: str


@dataclass(frozen=True)
class ChangedFile:
    filename: str
    status: str
    additions: int
    deletions: int
    changes: int
    patch: str | None
    previous_filename: str | None = None


class GitHubClient:
    """Small wrapper around the GitHub REST API used by the intake milestone."""

    def __init__(
        self,
        *,
        token: str | None = None,
        api_base_url: str = "https://api.github.com",
        timeout_seconds: int = 15,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._owns_client = http_client is None
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "PRCritiq",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._client = http_client or httpx.Client(
            base_url=api_base_url,
            timeout=timeout_seconds,
            headers=headers,
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> GitHubClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def get_pull_request(self, repo: str, number: int) -> PullRequestMetadata:
        payload = self._get_json(f"/repos/{repo}/pulls/{number}")
        return PullRequestMetadata(
            repo=repo,
            number=int(payload["number"]),
            title=payload["title"],
            state=payload["state"],
            base_sha=payload["base"]["sha"],
            head_sha=payload["head"]["sha"],
            author_login=payload["user"]["login"],
            html_url=payload["html_url"],
        )

    def list_changed_files(self, repo: str, number: int) -> list[ChangedFile]:
        files: list[ChangedFile] = []
        page = 1
        while True:
            payload = self._get_json(
                f"/repos/{repo}/pulls/{number}/files",
                params={"per_page": 100, "page": page},
            )
            if not isinstance(payload, list):
                raise GitHubClientError("GitHub changed-files response was not a list")
            files.extend(
                ChangedFile(
                    filename=item["filename"],
                    status=item["status"],
                    additions=int(item["additions"]),
                    deletions=int(item["deletions"]),
                    changes=int(item["changes"]),
                    patch=item.get("patch"),
                    previous_filename=item.get("previous_filename"),
                )
                for item in payload
            )
            if len(payload) < 100:
                return files
            page += 1

    def _get_json(self, path: str, params: dict[str, object] | None = None) -> Any:
        response = self._client.get(path, params=params)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise GitHubClientError(
                f"GitHub request failed: {response.status_code} {response.text}"
            ) from exc
        return response.json()
