"""Export and apply human adjudication decisions for benchmark labels.

Export an editable JSONL queue:

    uv run python eval/adjudicate_dataset.py --export eval/adjudications.jsonl

After setting each row's ``adjudication`` to ``confirmed_defect`` or
``excluded`` and explaining the decision, build a new dataset:

    uv run python eval/adjudicate_dataset.py \
        --apply eval/adjudications.jsonl \
        --output eval/dataset-certified.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from prcritiq.dataset import (
    adjudication_queue,
    apply_adjudications,
    read_dataset,
    select_confirmed_cases,
    write_dataset,
)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_number}: invalid JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"{path}:{line_number}: each row must be a JSON object")
        rows.append(payload)
    return rows


def _write_jsonl(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Export or apply benchmark adjudications")
    parser.add_argument("--dataset", default="eval/dataset.jsonl", help="Input dataset JSONL")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--export", metavar="PATH", help="Write an editable decision queue")
    mode.add_argument("--apply", metavar="PATH", help="Apply decisions from this JSONL file")
    parser.add_argument(
        "--output",
        default="eval/dataset-certified.jsonl",
        help="Dataset path written by --apply",
    )
    parser.add_argument(
        "--allow-incomplete",
        action="store_true",
        help="Allow missing or still-unreviewed decisions in a draft output.",
    )
    parser.add_argument(
        "--confirmed-only",
        action="store_true",
        help=("With --apply, retain only cases containing at least one confirmed-defect label."),
    )
    args = parser.parse_args()

    cases = read_dataset(Path(args.dataset))
    if args.export:
        queue = adjudication_queue(cases, root=Path.cwd())
        _write_jsonl(queue, Path(args.export))
        print(f"wrote {len(queue)} label decisions to {args.export}")
        return 0

    decisions = _read_jsonl(Path(args.apply))
    try:
        updated = apply_adjudications(
            cases,
            decisions,
            require_complete=not args.allow_incomplete,
        )
    except ValueError as exc:
        print(f"adjudication failed: {exc}", file=sys.stderr)
        return 1

    unreviewed = sum(
        label.adjudication == "unreviewed" for case in updated for label in case.labels
    )
    if unreviewed and not args.allow_incomplete:
        print(
            f"adjudication failed: {unreviewed} labels are still unreviewed",
            file=sys.stderr,
        )
        return 1
    input_cases = len(updated)
    if args.confirmed_only:
        updated = select_confirmed_cases(updated)
    write_dataset(updated, Path(args.output))
    confirmed = sum(
        label.adjudication == "confirmed_defect" for case in updated for label in case.labels
    )
    excluded = sum(label.adjudication == "excluded" for case in updated for label in case.labels)
    print(
        f"wrote {len(updated)} cases to {args.output}: "
        f"{confirmed} confirmed, {excluded} excluded, {unreviewed} unreviewed"
        + (f"; selected from {input_cases} adjudicated cases" if args.confirmed_only else "")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
