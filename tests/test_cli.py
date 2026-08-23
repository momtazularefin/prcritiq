from __future__ import annotations

import json

from prcritiq.cli import main
from prcritiq.schemas import IMPLEMENTATION_STATUS


def test_cli_health_outputs_json(capsys) -> None:
    exit_code = main(["health"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["service"] == "prcritiq"
    assert payload["implementation_status"] == IMPLEMENTATION_STATUS


def test_cli_review_reports_parsed_diff_and_guardrails(capsys, patched_github) -> None:
    exit_code = main(
        ["review", "--repo", "https://github.com/example/repo", "--pr", "7", "--mode", "dry-run"]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["repo"] == "example/repo"
    assert payload["pr_number"] == 7
    assert payload["reviewable_files"] == 1
    assert payload["commentable_lines"] == 2
    assert payload["idempotency_key"].startswith("dry_run:")
    assert payload["posted_comments"] == 0


def test_cli_review_reports_an_unusable_repository_without_a_traceback(capsys) -> None:
    exit_code = main(["review", "--repo", "not-a-repo", "--pr", "7"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert "not-a-repo" in payload["error"]
