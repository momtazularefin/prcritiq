from __future__ import annotations

import json

from prcritiq.cli import main


def test_cli_health_outputs_json(capsys) -> None:
    exit_code = main(["health"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["service"] == "prcritiq"
    assert payload["implementation_status"] == "m0_scaffold"


def test_cli_review_outputs_dry_run_scaffold_report(capsys) -> None:
    exit_code = main(
        ["review", "--repo", "https://github.com/example/repo", "--pr", "7", "--mode", "dry-run"]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["repo"] == "https://github.com/example/repo"
    assert payload["pr_number"] == 7
    assert payload["posted_comments"] == 0
