"""GitHub webhook verification and idempotency helpers."""

from __future__ import annotations

import hashlib
import hmac
from typing import Any

SUPPORTED_PULL_REQUEST_ACTIONS = frozenset(
    {
        "opened",
        "reopened",
        "ready_for_review",
        "synchronize",
    }
)


class WebhookPayloadError(ValueError):
    """Raised when a webhook payload lacks required review-run fields."""


def compute_webhook_signature(secret: str, body: bytes) -> str:
    """Compute the GitHub sha256 signature for a webhook request body."""

    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def verify_webhook_signature(
    *,
    secret: str,
    body: bytes,
    signature_header: str | None,
) -> bool:
    """Return True only when the GitHub signature matches."""

    if not signature_header:
        return False
    expected = compute_webhook_signature(secret=secret, body=body)
    return hmac.compare_digest(expected, signature_header)


def build_review_run_key(
    *,
    installation_id: int | str,
    repository_id: int | str,
    pull_number: int | str,
    head_sha: str,
    delivery_id: str,
) -> str:
    """Build a stable idempotency key for a GitHub webhook review run."""

    raw = "|".join(
        str(part) for part in (installation_id, repository_id, pull_number, head_sha, delivery_id)
    )
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
    return f"review_run:{digest}"


def build_dry_run_key(*, repo: str, pr_number: int, head_sha: str) -> str:
    """Build a deterministic idempotency key for local dry-run reports.

    The head SHA is part of the key so that re-running a pull request after new
    commits is a distinct run, matching how the webhook key already behaves.
    """

    raw = f"dry-run|{repo}|{pr_number}|{head_sha}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
    return f"dry_run:{digest}"


def build_review_run_key_from_payload(payload: dict[str, Any], *, delivery_id: str) -> str:
    """Extract required fields from a pull_request payload and build its key."""

    try:
        installation_id = payload["installation"]["id"]
        repository_id = payload["repository"]["id"]
        pull_number = payload["pull_request"]["number"]
        head_sha = payload["pull_request"]["head"]["sha"]
    except (KeyError, TypeError) as exc:
        raise WebhookPayloadError(
            "pull_request webhook payload is missing installation, repository, "
            "PR number, or head SHA"
        ) from exc
    return build_review_run_key(
        installation_id=installation_id,
        repository_id=repository_id,
        pull_number=pull_number,
        head_sha=head_sha,
        delivery_id=delivery_id,
    )
