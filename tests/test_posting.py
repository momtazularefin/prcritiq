"""Tests for GitHub posting and the Markdown report.

Every GitHub write here is mocked. No test posts to a real pull request.
"""

from __future__ import annotations

import os

import pytest

from prcritiq.diff import build_diff_index, build_file_diff
from prcritiq.findings import CandidateFinding, ReviewedFinding
from prcritiq.github import PostedComment
from prcritiq.markdown import render_report
from prcritiq.posting import body_hash, post_findings
from prcritiq.store import connect, create_or_get_run, migrate, record_findings

DATABASE_URL = os.environ.get("DATABASE_URL")
needs_postgres = pytest.mark.skipif(
    not DATABASE_URL, reason="DATABASE_URL is not set; run `docker compose up -d` first"
)

PATCH = "@@ -1,2 +1,4 @@\n import os\n \n+def retry(n):\n+    return 1 / n\n"


def reviewed(publish: bool = True, **overrides) -> ReviewedFinding:
    defaults = {
        "file_path": "src/app.py",
        "line": 4,
        "severity": "high",
        "confidence": 90,
        "category": "bug",
        "finding": "retry divides by n without guarding against a zero argument.",
        "evidence": "Line 4 computes 1 / n with n straight from the caller.",
        "suggested_fix": "Raise ValueError when n is zero.",
        "source_refs": [],
    }
    defaults.update(overrides)
    return ReviewedFinding(
        candidate=CandidateFinding(**defaults),
        publish_decision="publish" if publish else "suppress",
        suppression_reason=None if publish else "low_confidence",
    )


@pytest.fixture
def diff_index():
    return build_diff_index(
        [
            build_file_diff(
                path="src/app.py",
                status="modified",
                additions=2,
                deletions=0,
                changes=2,
                patch=PATCH,
            )
        ]
    )


class RecordingGitHub:
    """Captures the writes that would have reached GitHub."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def create_review_comment(self, repo, number, *, commit_id, path, line, body):
        self.calls.append(
            {
                "repo": repo,
                "number": number,
                "commit_id": commit_id,
                "path": path,
                "line": line,
                "body": body,
            }
        )
        return PostedComment(
            comment_id=1000 + len(self.calls),
            path=path,
            line=line,
            html_url=f"https://github.com/{repo}/pull/{number}#discussion_r{len(self.calls)}",
        )


class TestBodyHash:
    def test_identical_bodies_hash_the_same(self) -> None:
        assert body_hash("a body") == body_hash("a body")

    def test_different_bodies_differ(self) -> None:
        assert body_hash("a body") != body_hash("another body")


class TestCommentShape:
    def test_the_comment_matches_the_design_shape(self) -> None:
        body = reviewed().as_comment()

        assert body.splitlines()[0] == "Severity: high"
        assert body.splitlines()[1] == "Confidence: 90%"
        assert "Finding:" in body
        assert "Evidence:" in body
        assert "Suggested fix:" in body


@needs_postgres
class TestPosting:
    @pytest.fixture
    def connection(self):
        with connect(DATABASE_URL) as conn:
            migrate(conn)
            with conn.cursor() as cursor:
                cursor.execute("TRUNCATE review_runs RESTART IDENTITY CASCADE")
            conn.commit()
            yield conn

    @pytest.fixture
    def run_id(self, connection) -> int:
        row, _ = create_or_get_run(
            connection,
            idempotency_key="dry_run:posting",
            repo_full_name="example/repo",
            pr_number=7,
            head_sha="head222",
        )
        return int(row["id"])

    def _post(self, connection, run_id, client, reviewed_items, diff_index):
        return post_findings(
            client=client,
            connection=connection,
            run_id=run_id,
            repo="example/repo",
            pr_number=7,
            head_sha="head222",
            reviewed=reviewed_items,
            diff_index=diff_index,
        )

    def test_a_validated_finding_is_posted(self, connection, run_id, diff_index) -> None:
        client = RecordingGitHub()
        record_findings(connection, run_id, [reviewed()])

        outcome = self._post(connection, run_id, client, [reviewed()], diff_index)

        assert outcome.posted_count == 1
        assert client.calls[0]["path"] == "src/app.py"
        assert client.calls[0]["line"] == 4
        assert client.calls[0]["commit_id"] == "head222"

    def test_a_suppressed_finding_is_never_posted(self, connection, run_id, diff_index) -> None:
        client = RecordingGitHub()

        outcome = self._post(connection, run_id, client, [reviewed(publish=False)], diff_index)

        assert client.calls == []
        assert outcome.posted_count == 0

    def test_an_invalid_line_is_refused_at_the_last_gate(
        self, connection, run_id, diff_index
    ) -> None:
        """Self-critique already checked, but the write is the check that counts."""

        client = RecordingGitHub()
        sneaky = ReviewedFinding(candidate=reviewed(line=1).candidate, publish_decision="publish")

        outcome = self._post(connection, run_id, client, [sneaky], diff_index)

        assert client.calls == []
        assert outcome.skipped[0].reason == "line_not_changed"

    def test_the_same_finding_is_not_posted_twice(self, connection, run_id, diff_index) -> None:
        """A re-run must not repeat a comment on someone's pull request."""

        client = RecordingGitHub()
        record_findings(connection, run_id, [reviewed()])

        first = self._post(connection, run_id, client, [reviewed()], diff_index)
        second = self._post(connection, run_id, client, [reviewed()], diff_index)

        assert first.posted_count == 1
        assert second.posted_count == 0
        assert second.skipped[0].reason == "already_posted"
        assert len(client.calls) == 1

    def test_posted_comment_ids_are_stored(self, connection, run_id, diff_index) -> None:
        client = RecordingGitHub()
        record_findings(connection, run_id, [reviewed()])

        self._post(connection, run_id, client, [reviewed()], diff_index)

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT github_comment_id, body_hash FROM posted_comments WHERE run_id = %s",
                (run_id,),
            )
            rows = cursor.fetchall()

        assert len(rows) == 1
        assert rows[0]["github_comment_id"] == 1001
        assert rows[0]["body_hash"] == body_hash(reviewed().as_comment())

    def test_no_findings_posts_nothing_and_says_why(self, connection, run_id, diff_index) -> None:
        """AC9: a quiet run is an outcome, not a reason to comment."""

        client = RecordingGitHub()

        outcome = self._post(connection, run_id, client, [reviewed(publish=False)], diff_index)

        assert client.calls == []
        assert "Nothing was posted" in outcome.summary
        assert "recorded outcome" in outcome.summary


class TestMarkdownReport:
    def _report(self, **overrides):
        from prcritiq.schemas import ReviewReport

        defaults = {
            "repo": "example/repo",
            "pr_number": 7,
            "idempotency_key": "dry_run:abc",
            "title": "Add retry helper",
            "state": "open",
            "author_login": "octocat",
            "base_sha": "base111222333",
            "head_sha": "head222333444",
            "html_url": "https://github.com/example/repo/pull/7",
            "message": "Dry run.",
        }
        defaults.update(overrides)
        return ReviewReport(**defaults)

    def test_a_report_without_a_review_says_it_is_not_a_clean_bill(self) -> None:
        rendered = render_report(self._report())

        assert "# PRCritiq review of example/repo#7" in rendered
        assert "not a clean bill of health" in rendered

    def test_published_findings_are_rendered_in_full(self) -> None:
        from prcritiq.reporting import _describe_finding
        from prcritiq.schemas import ReviewOutcome

        rendered = render_report(
            self._report(
                findings=[_describe_finding(reviewed())],
                review=ReviewOutcome(summary="1 of 1", candidates=1, published=1),
            )
        )

        assert "### src/app.py:4 — high (90%)" in rendered
        assert "**Evidence:**" in rendered
        assert "**Suggested fix:**" in rendered

    def test_a_suppressed_only_run_reports_the_counts(self) -> None:
        from prcritiq.schemas import ReviewOutcome

        rendered = render_report(
            self._report(
                review=ReviewOutcome(
                    summary="none met the bar",
                    candidates=3,
                    published=0,
                    suppressed=3,
                    suppressed_by_reason={"invalid_line": 1, "low_confidence": 2},
                    invalid_line_rate=0.3333,
                )
            )
        )

        assert "No findings met the bar. 3 of 3" in rendered
        assert "| invalid_line | 1 |" in rendered
        assert "Invalid-line rate: 33.3%" in rendered

    def test_table_cells_cannot_be_broken_by_pull_request_text(self) -> None:
        """A path or reason is attacker-controlled text, not table syntax."""

        from prcritiq.schemas import FileReport

        rendered = render_report(
            self._report(
                files=[
                    FileReport(
                        path="src/a.py",
                        status="modified",
                        language="python",
                        decision="skipped_vendor",
                        reason="pipe | here\nand a newline",
                        changed_lines=0,
                    )
                ]
            )
        )

        assert "pipe \\| here and a newline" in rendered
        assert rendered.count("\n|") >= 1
