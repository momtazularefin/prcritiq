from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from prcritiq.cli import main
from prcritiq.dataset import BenchmarkCase, Label, write_dataset
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


def test_live_eval_refuses_uncertified_data_before_calling_a_provider(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    fixture = {
        "metadata": {
            "repo": "example/repo",
            "number": 1,
            "title": "Fix division",
            "state": "closed",
            "base_sha": "base",
            "head_sha": "head",
            "author_login": "author",
            "html_url": "https://github.com/example/repo/pull/1",
        },
        "files": [
            {
                "filename": "src/app.py",
                "status": "modified",
                "additions": 2,
                "deletions": 0,
                "changes": 2,
                "patch": "@@ -1,2 +1,4 @@\n import os\n \n+def retry(n):\n+    return 1 / n\n",
                "previous_filename": None,
            }
        ],
        "review_comments": [
            {
                "id": 7,
                "path": "src/app.py",
                "line": 4,
                "original_line": 4,
                "body": "This divides by n without checking whether n is zero.",
                "user": "reviewer",
                "user_type": "User",
                "commit_id": "head",
                "in_reply_to_id": None,
            }
        ],
    }
    fixture_path = tmp_path / "fixture.json"
    fixture_path.write_text(json.dumps(fixture), encoding="utf-8")
    approved = BenchmarkCase(
        id="approved",
        repo="example/repo",
        pr_number=1,
        base_sha="base",
        head_sha="head",
        languages=["python"],
        diff_path="fixture.json",
        human_comments_path="fixture.json",
        labels=[
            Label(
                file_path="src/app.py",
                line=4,
                category="bug",
                severity="high",
                human_comment="This divides by n without checking whether n is zero.",
                expected_issue="zero division",
                label_id="github-review-comment:7",
                reviewer_login="reviewer",
                source_comment_id=7,
                review_commit_sha="head",
                adjudication="confirmed_defect",
                adjudication_notes="Human verified against the changed behavior.",
                human_approved=True,
                adjudicator="project-owner",
            )
        ],
    )
    provisional = replace(
        approved,
        id="provisional",
        labels=[
            replace(
                approved.labels[0],
                adjudication_notes="Agent evidence review; human sign-off pending.",
                human_approved=False,
                adjudicator="agent-evidence-review",
            )
        ],
    )
    dataset = tmp_path / "dataset.jsonl"
    write_dataset([approved, provisional], dataset)
    monkeypatch.chdir(tmp_path)

    def unexpected_call(*args, **kwargs):
        raise AssertionError("provider execution must not start")

    monkeypatch.setattr("prcritiq.benchmark.execute_case", unexpected_call)
    exit_code = main(["eval", "--dataset", str(dataset), "--provider", "openai", "--limit", "1"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert "no provider was called" in payload["error"]
    assert payload["certified_cases"] == 1
    assert payload["issues"] == [
        "provisional/github-review-comment:7: decision is not human-approved"
    ]
