from __future__ import annotations

import json
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
    case = BenchmarkCase(
        id="case",
        repo="example/repo",
        pr_number=1,
        base_sha="base",
        head_sha="head",
        languages=["python"],
        diff_path="missing-fixture.json",
        human_comments_path="missing-fixture.json",
        labels=[
            Label(
                file_path="src/app.py",
                line=4,
                category="bug",
                severity="high",
                human_comment="This divides by n without checking whether n is zero.",
                expected_issue="zero division",
            )
        ],
    )
    dataset = tmp_path / "dataset.jsonl"
    write_dataset([case], dataset)
    monkeypatch.chdir(tmp_path)

    def unexpected_call(*args, **kwargs):
        raise AssertionError("provider execution must not start")

    monkeypatch.setattr("prcritiq.benchmark.execute_case", unexpected_call)
    exit_code = main(["eval", "--dataset", str(dataset), "--provider", "openai"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert "no provider was called" in payload["error"]
    assert payload["certified_cases"] == 0
