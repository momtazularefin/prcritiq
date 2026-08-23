from __future__ import annotations

from prcritiq.webhooks import (
    build_dry_run_key,
    build_review_run_key,
    build_review_run_key_from_payload,
    compute_webhook_signature,
    verify_webhook_signature,
)


def test_webhook_signature_verification() -> None:
    body = b'{"action":"opened"}'
    signature = compute_webhook_signature(secret="secret", body=body)

    assert verify_webhook_signature(secret="secret", body=body, signature_header=signature)
    assert not verify_webhook_signature(secret="wrong", body=body, signature_header=signature)
    assert not verify_webhook_signature(secret="secret", body=body, signature_header=None)


def test_review_run_key_is_stable_and_head_sha_sensitive() -> None:
    first = build_review_run_key(
        installation_id=1,
        repository_id=2,
        pull_number=3,
        head_sha="abc",
        delivery_id="delivery",
    )
    second = build_review_run_key(
        installation_id=1,
        repository_id=2,
        pull_number=3,
        head_sha="abc",
        delivery_id="delivery",
    )
    changed = build_review_run_key(
        installation_id=1,
        repository_id=2,
        pull_number=3,
        head_sha="def",
        delivery_id="delivery",
    )

    assert first == second
    assert first != changed


def test_review_run_key_from_payload() -> None:
    key = build_review_run_key_from_payload(
        {
            "installation": {"id": 1},
            "repository": {"id": 2},
            "pull_request": {"number": 3, "head": {"sha": "abc"}},
        },
        delivery_id="delivery",
    )

    assert key.startswith("review_run:")


def test_dry_run_key_is_stable() -> None:
    first = build_dry_run_key(repo="example/repo", pr_number=1, head_sha="abc")
    second = build_dry_run_key(repo="example/repo", pr_number=1, head_sha="abc")

    assert first == second
