"""Tests for the wired dry-run review path."""

from __future__ import annotations

import pytest

from prcritiq.config import ConfigError, Settings, SimilarityMode
from prcritiq.github import (
    ChangedFile,
    GitHubClientError,
    PullRequestMetadata,
    RepoReferenceError,
    parse_repo_reference,
)
from prcritiq.reporting import build_review_report
from prcritiq.review import run_dry_run

from conftest import StubGitHubClient


class TestParseRepoReference:
    @pytest.mark.parametrize(
        "reference",
        [
            "https://github.com/example/repo",
            "https://github.com/example/repo.git",
            "http://github.com/example/repo/",
            "git@github.com:example/repo.git",
            "ssh://git@github.com/example/repo",
            "example/repo",
            "  https://github.com/example/repo  ",
        ],
    )
    def test_supported_forms_resolve_to_owner_and_name(self, reference: str) -> None:
        assert parse_repo_reference(reference) == "example/repo"

    def test_pasted_pull_request_url_resolves_to_the_repository(self) -> None:
        assert parse_repo_reference("https://github.com/example/repo/pull/7") == "example/repo"

    @pytest.mark.parametrize(
        "reference",
        ["", "   ", "https://github.com/example", "example", "https://github.com//repo"],
    )
    def test_unusable_references_are_rejected(self, reference: str) -> None:
        with pytest.raises(RepoReferenceError):
            parse_repo_reference(reference)


class TestRunDryRun:
    def test_report_describes_every_changed_file(self, stub_client: StubGitHubClient) -> None:
        report = run_dry_run(
            repo="https://github.com/example/repo",
            pr_number=7,
            settings=Settings(),
            client=stub_client,
        )

        assert report.repo == "example/repo"
        assert report.pr_number == 7
        assert report.title == "Add configuration values"
        assert report.head_sha == "head222"
        assert report.total_files == 3
        assert [item.path for item in report.files] == ["src/app.py", "uv.lock", "README.md"]

    def test_guardrails_are_reflected_in_the_report(self, stub_client: StubGitHubClient) -> None:
        report = run_dry_run(
            repo="example/repo", pr_number=7, settings=Settings(), client=stub_client
        )

        decisions = {item.path: item.decision for item in report.files}
        assert decisions == {
            "src/app.py": "reviewable",
            "uv.lock": "skipped_lockfile",
            "README.md": "skipped_unsupported_language",
        }
        assert report.reviewable_files == 1
        assert report.skipped_files == 2
        assert report.skipped_by_decision == {
            "skipped_lockfile": 1,
            "skipped_unsupported_language": 1,
        }

    def test_commentable_lines_count_only_reviewable_files(
        self, stub_client: StubGitHubClient
    ) -> None:
        """The lockfile and the doc each add a line, but neither may be commented on."""

        report = run_dry_run(
            repo="example/repo", pr_number=7, settings=Settings(), client=stub_client
        )

        assert report.commentable_lines == 2

    def test_empty_findings_are_explained_rather_than_implied(
        self, stub_client: StubGitHubClient
    ) -> None:
        report = run_dry_run(
            repo="example/repo", pr_number=7, settings=Settings(), client=stub_client
        )

        assert report.findings == []
        assert report.posted_comments == 0
        assert "no model was called" in report.message.lower()

    def test_idempotency_key_changes_with_the_head_sha(
        self, pr_metadata: PullRequestMetadata, changed_files: list[ChangedFile]
    ) -> None:
        """A re-run after new commits must be a distinct run."""

        first = run_dry_run(
            repo="example/repo",
            pr_number=7,
            settings=Settings(),
            client=StubGitHubClient(pr_metadata, changed_files),
        )
        moved = PullRequestMetadata(**{**pr_metadata.__dict__, "head_sha": "head999"})
        second = run_dry_run(
            repo="example/repo",
            pr_number=7,
            settings=Settings(),
            client=StubGitHubClient(moved, changed_files),
        )

        assert first.idempotency_key != second.idempotency_key
        assert first.idempotency_key.startswith("dry_run:")

    def test_repository_reference_is_normalized_before_the_api_call(
        self, stub_client: StubGitHubClient
    ) -> None:
        run_dry_run(
            repo="git@github.com:example/repo.git",
            pr_number=7,
            settings=Settings(),
            client=stub_client,
        )

        assert stub_client.calls[0] == ("get_pull_request", "example/repo", 7)

    def test_injected_client_is_not_closed_by_the_review(
        self, stub_client: StubGitHubClient
    ) -> None:
        """The caller owns a client it supplied."""

        run_dry_run(repo="example/repo", pr_number=7, settings=Settings(), client=stub_client)

        assert stub_client.closed is False

    def test_unusable_repository_reference_raises_before_any_call(
        self, stub_client: StubGitHubClient
    ) -> None:
        with pytest.raises(RepoReferenceError):
            run_dry_run(repo="not-a-repo", pr_number=7, settings=Settings(), client=stub_client)

        assert stub_client.calls == []

    def test_github_failure_propagates(
        self, pr_metadata: PullRequestMetadata, changed_files: list[ChangedFile]
    ) -> None:
        class Failing(StubGitHubClient):
            def get_pull_request(self, repo: str, number: int) -> PullRequestMetadata:
                raise GitHubClientError("GitHub request failed: 404")

        with pytest.raises(GitHubClientError):
            run_dry_run(
                repo="example/repo",
                pr_number=7,
                settings=Settings(),
                client=Failing(pr_metadata, changed_files),
            )

    def test_file_budget_is_visible_in_the_report(self, pr_metadata: PullRequestMetadata) -> None:
        patch = "@@ -1,1 +1,2 @@\n alpha\n+beta\n"
        files = [
            ChangedFile(
                filename=f"src/mod_{index}.py",
                status="modified",
                additions=1,
                deletions=0,
                changes=1,
                patch=patch,
            )
            for index in range(3)
        ]

        report = run_dry_run(
            repo="example/repo",
            pr_number=7,
            settings=Settings(max_files=2),
            client=StubGitHubClient(pr_metadata, files),
        )

        assert report.reviewable_files == 2
        assert report.skipped_by_decision == {"skipped_oversized_diff": 1}


class TestReportConstruction:
    def test_mismatched_lengths_are_rejected(self, pr_metadata: PullRequestMetadata) -> None:
        with pytest.raises(ValueError, match="same length"):
            build_review_report(metadata=pr_metadata, file_diffs=[], outcomes=[object()])  # type: ignore[list-item]


class TestContextRetrieval:
    def test_context_is_absent_unless_requested(self, stub_client: StubGitHubClient) -> None:
        """The default dry run stays cheap: no archive download."""

        report = run_dry_run(
            repo="example/repo", pr_number=7, settings=Settings(), client=stub_client
        )

        assert report.context is None
        assert all(call[0] != "download_source_archive" for call in stub_client.calls)

    def test_context_is_retrieved_when_requested(self, stub_client: StubGitHubClient) -> None:
        report = run_dry_run(
            repo="example/repo",
            pr_number=7,
            settings=Settings(),
            client=stub_client,
            include_context=True,
        )

        assert report.context is not None
        assert report.context.indexed_files > 0
        assert report.context.indexed_chunks > 0
        assert ("download_source_archive", "example/repo", "head222") in stub_client.calls

    def test_context_report_names_reasons_and_chunk_ids(
        self, stub_client: StubGitHubClient
    ) -> None:
        report = run_dry_run(
            repo="example/repo",
            pr_number=7,
            settings=Settings(),
            client=stub_client,
            include_context=True,
        )

        assert report.context is not None
        for item in report.context.related:
            assert item.reason
            assert item.chunk_id.startswith(item.path)

    def test_message_stops_claiming_no_context_was_retrieved(
        self, stub_client: StubGitHubClient
    ) -> None:
        without = run_dry_run(
            repo="example/repo", pr_number=7, settings=Settings(), client=stub_client
        )
        with_context = run_dry_run(
            repo="example/repo",
            pr_number=7,
            settings=Settings(),
            client=stub_client,
            include_context=True,
        )

        assert "no context was retrieved" in without.message
        assert "no context was retrieved" not in with_context.message
        assert "context retrieved" in with_context.message

    def test_embedding_request_fails_the_run(self, stub_client: StubGitHubClient) -> None:
        with pytest.raises(ConfigError, match="not implemented"):
            run_dry_run(
                repo="example/repo",
                pr_number=7,
                settings=Settings(similarity=SimilarityMode.EMBEDDING),
                client=stub_client,
                include_context=True,
            )
