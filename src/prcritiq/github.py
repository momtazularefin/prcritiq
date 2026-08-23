"""Thin GitHub REST client boundary for PR metadata and changed files."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import httpx


class GitHubClientError(RuntimeError):
    """Raised when GitHub returns an unexpected response."""


class RepoReferenceError(ValueError):
    """Raised when a repository reference cannot be resolved to owner/name."""


_REPO_SEGMENT = re.compile(r"^[A-Za-z0-9._-]+$")


def parse_repo_reference(reference: str) -> str:
    """Resolve a repository URL or shorthand to its `owner/name` form.

    Accepts the HTTPS and SSH clone forms, a pasted pull-request URL, and the
    bare `owner/name` shorthand. Trailing path segments are ignored, so a pasted
    PR link works without the caller having to strip it; the pull request under
    review always comes from the explicit PR number.
    """

    candidate = reference.strip()
    if not candidate:
        raise RepoReferenceError("Repository reference is empty")

    without_scheme = re.sub(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", "", candidate)
    had_scheme = without_scheme != candidate
    candidate = without_scheme

    without_user = re.sub(r"^[^/@]+@", "", candidate)  # git@ or user:token@
    had_user = without_user != candidate
    candidate = without_user

    # The SSH form separates host from owner with a colon rather than a slash.
    host, separator, remainder = candidate.partition(":")
    had_ssh_colon = bool(separator) and "/" not in host
    if had_ssh_colon:
        candidate = f"{host}/{remainder}"

    parts = [part for part in candidate.split("/") if part]
    # Anything that carried a scheme, a user, or an SSH colon began with a host
    # segment. Inferring that from a dot alone would misread github.com/example
    # as a complete owner and name.
    leads_with_host = had_scheme or had_user or had_ssh_colon
    if parts and (leads_with_host or (len(parts) > 2 and "." in parts[0])):
        parts = parts[1:]

    unresolved = RepoReferenceError(
        f"Cannot resolve a repository owner and name from {reference!r}"
    )
    if len(parts) < 2:
        raise unresolved
    owner, name = parts[0], parts[1].removesuffix(".git")
    if not _REPO_SEGMENT.match(owner) or not _REPO_SEGMENT.match(name):
        raise unresolved
    return f"{owner}/{name}"


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
