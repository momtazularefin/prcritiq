"""Build a benchmark-candidate corpus from real pull request review history.

Run once to produce a dataset and frozen fixtures. The output paths are explicit
so a v2 candidate harvest does not overwrite the published legacy diagnostic
corpus. Generated labels remain ``unreviewed`` until a human adjudicates them;
they cannot make a benchmark report certified.

Uses the `gh` CLI so the harvest runs under the operator's own authenticated
credentials rather than the unauthenticated 60-requests-per-hour ceiling.

    uv run python eval/build_dataset.py --target 24 \
        --output eval/candidates/dataset.jsonl \
        --fixtures eval/candidates/fixtures \
        --seed-dataset eval/dataset.jsonl
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from prcritiq.dataset import (
    BenchmarkCase,
    case_is_usable,
    labels_from_comments,
    read_dataset,
    write_dataset,
)
from prcritiq.diff import build_diff_index, build_file_diff

# Code-heavy Python repositories with a culture of substantive inline review.
# Deliberately excludes prose and book repositories, per the eval plan.
CANDIDATE_REPOS = [
    "pydantic/pydantic",
    "encode/httpx",
    "encode/starlette",
    "fastapi/fastapi",
    "psf/black",
    "python-attrs/attrs",
    "pytest-dev/pytest",
    "HypothesisWorks/hypothesis",
    "Textualize/rich",
    "sqlalchemy/sqlalchemy",
    "scrapy/scrapy",
    "aio-libs/aiohttp",
]


def gh(path: str) -> Any:
    """Call the GitHub API through the authenticated gh CLI."""

    result = subprocess.run(
        ["gh", "api", path],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def harvest_pull_request(
    repo: str,
    entry: dict[str, Any],
    *,
    fixtures: Path,
    fixture_reference: str,
) -> BenchmarkCase | None:
    """Freeze one exact-revision pull request when it has eligible labels."""

    if not entry.get("merged_at"):
        return None
    number = int(entry["number"])
    comments = gh(f"repos/{repo}/pulls/{number}/comments?per_page=100")
    if not isinstance(comments, list) or not comments:
        return None
    author_login = (entry.get("user") or {}).get("login", "unknown")
    head_sha = entry["head"]["sha"]
    labels = labels_from_comments(
        comments,
        pr_author_login=author_login,
        head_sha=head_sha,
    )
    if not labels:
        return None

    files = gh(f"repos/{repo}/pulls/{number}/files?per_page=100")
    if not isinstance(files, list):
        return None
    python_files = [item for item in files if str(item.get("filename", "")).endswith(".py")]
    if not python_files:
        return None
    index = build_diff_index(
        [
            build_file_diff(
                path=item["filename"],
                status=item.get("status", "modified"),
                additions=int(item.get("additions", 0)),
                deletions=int(item.get("deletions", 0)),
                changes=int(item.get("changes", 0)),
                patch=item.get("patch"),
                previous_path=item.get("previous_filename"),
            )
            for item in python_files
        ]
    )
    reviewable_targets = {
        (file_diff.path, line) for file_diff in index.files for line in file_diff.commentable_lines
    }
    labels = [label for label in labels if (label.file_path, label.line) in reviewable_targets]
    if not labels:
        return None

    case_id = f"{repo.replace('/', '__')}__{number}"
    fixture = {
        "metadata": {
            "repo": repo,
            "number": number,
            "title": entry.get("title", ""),
            "state": entry.get("state", "closed"),
            "base_sha": entry["base"]["sha"],
            "head_sha": entry["head"]["sha"],
            "author_login": author_login,
            "html_url": entry.get("html_url", ""),
        },
        "files": [
            {
                "filename": item["filename"],
                "status": item.get("status", "modified"),
                "additions": int(item.get("additions", 0)),
                "deletions": int(item.get("deletions", 0)),
                "changes": int(item.get("changes", 0)),
                "patch": item.get("patch"),
                "previous_filename": item.get("previous_filename"),
            }
            for item in files
        ],
        "review_comments": [
            {
                "path": item.get("path"),
                "line": item.get("line"),
                "original_line": item.get("original_line"),
                "body": item.get("body"),
                "user": (item.get("user") or {}).get("login"),
                "user_type": (item.get("user") or {}).get("type"),
                "id": item.get("id"),
                "commit_id": item.get("commit_id"),
                "original_commit_id": item.get("original_commit_id"),
                "in_reply_to_id": item.get("in_reply_to_id"),
                "side": item.get("side"),
            }
            for item in comments
        ],
    }
    fixtures.mkdir(parents=True, exist_ok=True)
    (fixtures / f"{case_id}.json").write_text(
        json.dumps(fixture, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    case = BenchmarkCase(
        id=case_id,
        repo=repo,
        pr_number=number,
        base_sha=entry["base"]["sha"],
        head_sha=entry["head"]["sha"],
        languages=["python"],
        diff_path=f"{fixture_reference}/{case_id}.json",
        human_comments_path=f"{fixture_reference}/{case_id}.json",
        labels=labels,
        notes=f"{len(python_files)} python files changed; labels require human adjudication",
    )
    if not case_is_usable(case):
        return None
    print(f"  + {repo}#{number}: {len(labels)} labels, {len(python_files)} py files")
    return case


def harvest_repo(
    repo: str,
    scan: int,
    *,
    fixtures: Path,
    fixture_reference: str,
) -> list[BenchmarkCase]:
    """Collect usable cases from the most recent merged pull requests."""

    listing = gh(f"repos/{repo}/pulls?state=closed&per_page={scan}&sort=updated&direction=desc")
    if not isinstance(listing, list):
        return []
    return [
        case
        for entry in listing
        if (
            case := harvest_pull_request(
                repo,
                entry,
                fixtures=fixtures,
                fixture_reference=fixture_reference,
            )
        )
        is not None
    ]


def harvest_seed_dataset(
    path: Path,
    *,
    fixtures: Path,
    fixture_reference: str,
) -> list[BenchmarkCase]:
    """Re-fetch named legacy cases so current v2 filters can assess them."""

    cases: list[BenchmarkCase] = []
    for seed in read_dataset(path):
        entry = gh(f"repos/{seed.repo}/pulls/{seed.pr_number}")
        if not isinstance(entry, dict):
            continue
        case = harvest_pull_request(
            seed.repo,
            entry,
            fixtures=fixtures,
            fixture_reference=fixture_reference,
        )
        if case is not None:
            cases.append(case)
    return cases


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the PRCritiq benchmark corpus")
    parser.add_argument("--target", type=int, default=24, help="How many cases to collect")
    parser.add_argument("--scan", type=int, default=30, help="PRs to scan per repository")
    parser.add_argument(
        "--minimum",
        type=int,
        default=20,
        help="Exit successfully only when at least this many cases were collected",
    )
    parser.add_argument(
        "--output",
        default="eval/dataset.jsonl",
        help="Candidate dataset path, relative to the repository root",
    )
    parser.add_argument(
        "--fixtures",
        default="eval/fixtures",
        help="Frozen fixture directory, relative to the repository root",
    )
    parser.add_argument(
        "--seed-dataset",
        help="Re-fetch the PRs named by an existing dataset before scanning recent PRs",
    )
    args = parser.parse_args()

    output = Path(args.output)
    fixtures = Path(args.fixtures)
    if output.is_absolute() or fixtures.is_absolute():
        parser.error("--output and --fixtures must be repository-relative paths")
    fixture_reference = fixtures.as_posix().rstrip("/")

    collected: list[BenchmarkCase] = []
    seen: set[str] = set()
    if args.seed_dataset:
        print(f"re-fetching seed cases from {args.seed_dataset} ...")
        for case in harvest_seed_dataset(
            Path(args.seed_dataset),
            fixtures=fixtures,
            fixture_reference=fixture_reference,
        ):
            if case.id not in seen:
                collected.append(case)
                seen.add(case.id)

    for repo in CANDIDATE_REPOS:
        if len(collected) >= args.target:
            break
        print(f"scanning {repo} ...")
        for case in harvest_repo(
            repo,
            args.scan,
            fixtures=fixtures,
            fixture_reference=fixture_reference,
        ):
            if case.id not in seen:
                collected.append(case)
                seen.add(case.id)

    collected = collected[: args.target]
    written = write_dataset(collected, output)
    total_labels = sum(len(case.labels) for case in collected)
    print(f"\nwrote {written} cases with {total_labels} labels to {output}")
    return 0 if written >= args.minimum else 1


if __name__ == "__main__":
    raise SystemExit(main())
