"""Posting validated findings to GitHub.

ADR-012 orders this deliberately: dry-run and summary first, posting only behind
gates. Three of them apply to every comment.

The line is re-validated here even though self-critique already checked it.
GitHub accepts a comment on any line it considers part of the diff, which is a
wider set than the lines this pull request added, so the last check before a
write is the one that matters (AC8).

A body hash is recorded per run, so re-running a review cannot post the same
comment twice (AC1, AC10).

And nothing is posted when nothing cleared the bar. A no-findings run is a
correct outcome recorded in run history, not a reason to leave a comment saying
so (AC9).
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass

import psycopg

from .diff import DiffIndex
from .findings import ReviewedFinding
from .github import GitHubClient, PostedComment


def body_hash(body: str) -> str:
    """Stable hash of a comment body, used to refuse a duplicate post."""

    return hashlib.sha256(body.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SkippedComment:
    """A finding that was not posted, and why."""

    file_path: str
    line: int
    reason: str


@dataclass(frozen=True)
class PostOutcome:
    """What posting did, including everything it declined to do."""

    posted: tuple[PostedComment, ...] = ()
    skipped: tuple[SkippedComment, ...] = ()
    attempted: int = 0
    summary: str = ""

    @property
    def posted_count(self) -> int:
        return len(self.posted)


def _already_posted(connection: psycopg.Connection, run_id: int, digest: str) -> bool:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT 1 FROM posted_comments WHERE run_id = %s AND body_hash = %s",
            (run_id, digest),
        )
        return cursor.fetchone() is not None


def _record_posted(
    connection: psycopg.Connection,
    *,
    run_id: int,
    finding_id: int | None,
    comment: PostedComment,
    digest: str,
) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO posted_comments (finding_id, run_id, github_comment_id, body_hash)"
            " VALUES (%s, %s, %s, %s) ON CONFLICT (run_id, body_hash) DO NOTHING",
            (finding_id, run_id, comment.comment_id, digest),
        )
    connection.commit()


def _finding_ids(connection: psycopg.Connection, run_id: int) -> dict[tuple[str, int], int]:
    """Map a published finding's file and line to its stored row id."""

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT id, file_path, line FROM review_findings"
            " WHERE run_id = %s AND publish_decision = 'publish'",
            (run_id,),
        )
        return {(row["file_path"], int(row["line"])): int(row["id"]) for row in cursor.fetchall()}


def post_findings(
    *,
    client: GitHubClient,
    connection: psycopg.Connection,
    run_id: int,
    repo: str,
    pr_number: int,
    head_sha: str,
    reviewed: Sequence[ReviewedFinding],
    diff_index: DiffIndex,
) -> PostOutcome:
    """Post every published finding that still validates, exactly once."""

    publishable = [item for item in reviewed if item.published]
    if not publishable:
        return PostOutcome(
            summary=(
                f"Nothing was posted. {len(reviewed)} candidate findings were drafted and "
                "none cleared the bar, which is a recorded outcome rather than a reason "
                "to comment."
            )
        )

    stored_ids = _finding_ids(connection, run_id)
    posted: list[PostedComment] = []
    skipped: list[SkippedComment] = []

    for item in publishable:
        candidate = item.candidate
        verdict = diff_index.validate_line(candidate.file_path, candidate.line)
        if not verdict.valid:
            skipped.append(SkippedComment(candidate.file_path, candidate.line, verdict.reason))
            continue

        body = item.as_comment()
        digest = body_hash(body)
        if _already_posted(connection, run_id, digest):
            skipped.append(SkippedComment(candidate.file_path, candidate.line, "already_posted"))
            continue

        comment = client.create_review_comment(
            repo,
            pr_number,
            commit_id=head_sha,
            path=candidate.file_path,
            line=candidate.line,
            body=body,
        )
        posted.append(comment)
        _record_posted(
            connection,
            run_id=run_id,
            finding_id=stored_ids.get((candidate.file_path, candidate.line)),
            comment=comment,
            digest=digest,
        )

    return PostOutcome(
        posted=tuple(posted),
        skipped=tuple(skipped),
        attempted=len(publishable),
        summary=(
            f"Posted {len(posted)} of {len(publishable)} published findings; "
            f"{len(skipped)} were skipped."
        ),
    )
