"""Bounded, read-only source retrieval for candidate-local verification.

This deliberately uses the existing GitHub client's internal HTTP boundary so
authentication and its configured API origin stay centralized. Only relative
Contents API requests are made, with redirects disabled. Returned source is
untrusted text: it is never imported, executed, or written to disk here.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from urllib.parse import quote

import httpx

from .github import GitHubClient, GitHubClientError, RepoReferenceError, parse_repo_reference
from .guardrails import is_indexable_path

_COMMIT_SHA = re.compile(r"[0-9a-fA-F]{40}\Z")
_MAX_PATHS = 30
_RAW_MEDIA_TYPE = "application/vnd.github.raw+json"


class VerificationSourceError(GitHubClientError):
    """Source validation or retrieval failed; messages contain no remote body."""


def validate_source_request(
    repo: str,
    ref: str,
    paths: Sequence[str],
    max_file_bytes: int,
    max_total_bytes: int,
) -> tuple[str, ...]:
    """Validate a complete request without I/O and return unique paths in order.

    Replay callers can preflight every case before fetching the first source;
    ``fetch_sources`` repeats this same validation at its HTTP boundary.
    """
    if not isinstance(ref, str) or _COMMIT_SHA.fullmatch(ref) is None:
        raise VerificationSourceError("Source retrieval requires a full 40-hex commit SHA")
    if not isinstance(repo, str):
        raise VerificationSourceError("Source retrieval requires a canonical owner/name repository")
    try:
        canonical_repo = parse_repo_reference(repo)
    except RepoReferenceError:
        raise VerificationSourceError(
            "Source retrieval requires a canonical owner/name repository"
        ) from None
    if repo != canonical_repo or any(part in {".", ".."} for part in repo.split("/")):
        raise VerificationSourceError("Source retrieval requires a canonical owner/name repository")
    if any(type(limit) is not int or limit < 1 for limit in (max_file_bytes, max_total_bytes)):
        raise VerificationSourceError("Source byte limits must be positive integers")
    if isinstance(paths, (str, bytes)) or not isinstance(paths, Sequence):
        raise VerificationSourceError("Source paths must be a sequence of repository paths")
    if len(paths) > _MAX_PATHS:
        raise VerificationSourceError("Source retrieval supports at most 30 paths")
    for path in paths:
        if (
            not isinstance(path, str)
            or not path
            or "\\" in path
            or any(unicodedata.category(char).startswith("C") for char in path)
            or any(part in {"", ".", ".."} for part in path.split("/"))
            or not is_indexable_path(path)
        ):
            raise VerificationSourceError("Source paths must be normalized, safe, indexable paths")
    return tuple(dict.fromkeys(paths))


def fetch_sources(
    *,
    client: GitHubClient,
    repo: str,
    ref: str,
    paths: Sequence[str],
    max_file_bytes: int = 250_000,
    max_total_bytes: int = 2_000_000,
) -> dict[str, str]:
    """Fetch UTF-8 source at an immutable revision, within explicit byte budgets.

    All requested paths and options are validated before any request. Missing
    paths are omitted; every other failure aborts without returning a partial
    bundle. Duplicate paths are fetched once. Budgets apply to decoded HTTP
    bytes before UTF-8 decoding, including when a whole chunk exceeds a limit.

    JSON directory/symlink metadata is rejected instead of treated as source.
    GitHub may resolve an in-repository symlink server-side for the raw Contents
    representation; this helper does not attest regular-file Git tree mode and
    never follows a target or download URL supplied in a response.
    """
    validated_paths = validate_source_request(repo, ref, paths, max_file_bytes, max_total_bytes)
    sources: dict[str, str] = {}
    total_bytes = 0
    try:
        for path in validated_paths:
            with client._client.stream(
                "GET",
                f"/repos/{repo}/contents/{quote(path, safe='/')}",
                params={"ref": ref},
                headers={"Accept": _RAW_MEDIA_TYPE},
                follow_redirects=False,
            ) as response:
                if response.status_code == 404:
                    continue
                if response.status_code != 200:
                    raise VerificationSourceError(
                        f"GitHub source request failed with status {response.status_code}"
                    )
                media_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
                if media_type and media_type not in {
                    "text/plain",
                    "application/octet-stream",
                    _RAW_MEDIA_TYPE,
                }:
                    raise VerificationSourceError("GitHub source response was not raw file content")
                payload = bytearray()
                for block in response.iter_bytes():
                    block_size = len(block)
                    if len(payload) + block_size > max_file_bytes:
                        raise VerificationSourceError(
                            "GitHub source exceeds the per-file byte limit"
                        )
                    if total_bytes + block_size > max_total_bytes:
                        raise VerificationSourceError("GitHub sources exceed the total byte limit")
                    payload.extend(block)
                    total_bytes += block_size
                try:
                    sources[path] = payload.decode("utf-8", errors="strict")
                except UnicodeDecodeError:
                    raise VerificationSourceError("GitHub source is not valid UTF-8") from None
    except httpx.HTTPError:
        raise VerificationSourceError("GitHub source request failed at the HTTP boundary") from None
    return sources
