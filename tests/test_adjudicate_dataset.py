"""Safety tests for the human-adjudication command."""

import subprocess
import sys
from pathlib import Path


def test_incomplete_draft_cannot_be_filtered_into_a_certified_subset() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve().parents[1] / "eval" / "adjudicate_dataset.py"),
            "--apply",
            "unused-decisions.jsonl",
            "--allow-incomplete",
            "--confirmed-only",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "cannot be combined" in result.stderr
    assert "hide provisional decisions" in result.stderr
