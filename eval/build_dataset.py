"""Build a benchmark-candidate corpus from real pull request review history.

Run once to produce a dataset and frozen fixtures. The output paths are explicit
so a v2 candidate harvest does not overwrite the published legacy diagnostic
corpus. Generated labels remain ``unreviewed`` until a human adjudicates them;
they cannot make a benchmark report certified.

Uses the `gh` CLI so the harvest runs under the operator's own authenticated
credentials rather than the unauthenticated 60-requests-per-hour ceiling.

    uv run python eval/build_dataset.py --target 24 \
        --comment-pages 5 \
        --output eval/ground-truth-candidates/dataset.jsonl \
        --fixtures eval/ground-truth-candidates/fixtures

The default path searches recent substantive human review comments, then
freezes one fixture per PR at the exact revision a reviewer saw.  A conservative
failure-language filter reduces documentation and style noise but never
certifies a label; every candidate still begins ``unreviewed``.
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
    CandidateSignal,
    case_is_usable,
    is_likely_defect_comment,
    is_reviewable_source_path,
    labels_from_review_revision,
    read_dataset,
    write_dataset,
)
from prcritiq.diff import build_diff_index, build_file_diff

# Code-heavy Python repositories with a culture of substantive inline review.
# Deliberately excludes prose and book repositories, per the eval plan.
CANDIDATE_REPOS = [
    "pandas-dev/pandas",
    "numpy/numpy",
    "scikit-learn/scikit-learn",
    "apache/airflow",
    "django/django",
    "ansible/ansible",
    "celery/celery",
    "pallets/flask",
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

GH_TIMEOUT_SECONDS = 60


def gh(path: str) -> Any:
    """Call the GitHub API through the authenticated gh CLI."""

    try:
        result = subprocess.run(
            ["gh", "api", path],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=GH_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return None
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def gh_search_pull_requests(repo: str, limit: int) -> list[int]:
    """Find merged PRs with a formal changes-requested review."""

    try:
        result = subprocess.run(
            [
                "gh",
                "search",
                "prs",
                "--repo",
                repo,
                "--merged",
                "--review",
                "changes_requested",
                "--limit",
                str(limit),
                "--sort",
                "updated",
                "--order",
                "desc",
                "--json",
                "number",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=GH_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return []
    if result.returncode != 0:
        return []
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []
    return [int(item["number"]) for item in payload if item.get("number") is not None]


def gh_recent_defect_pull_requests(repo: str, limit: int, *, pages: int) -> list[int]:
    """Find recent PRs with substantive human inline defect reports."""

    numbers: list[int] = []
    seen: set[int] = set()
    for page in range(1, pages + 1):
        comments = gh(
            f"repos/{repo}/pulls/comments?sort=created&direction=desc&per_page=100&page={page}"
        )
        if not isinstance(comments, list):
            break
        for comment in comments:
            user = comment.get("user") or {}
            login = str(user.get("login") or "")
            account_type = str(user.get("type") or "")
            path = str(comment.get("path") or "")
            if comment.get("in_reply_to_id") is not None:
                continue
            if account_type.casefold() == "bot" or login.casefold().endswith("[bot]"):
                continue
            if not is_reviewable_source_path(path):
                continue
            if not is_likely_defect_comment(str(comment.get("body") or "")):
                continue
            try:
                number = int(
                    str(comment.get("pull_request_url") or "").rstrip("/").rsplit("/", 1)[-1]
                )
            except ValueError:
                continue
            if number in seen:
                continue
            numbers.append(number)
            seen.add(number)
            if len(numbers) >= limit:
                return numbers
    return numbers


def harvest_pull_request(
    repo: str,
    entry: dict[str, Any],
    *,
    fixtures: Path,
    fixture_reference: str,
    signal: CandidateSignal,
) -> BenchmarkCase | None:
    """Freeze the strongest exact review revision from one merged PR."""

    if not entry.get("merged_at"):
        return None
    number = int(entry["number"])
    comments = gh(f"repos/{repo}/pulls/{number}/comments?per_page=100&sort=created&direction=desc")
    if not isinstance(comments, list) or not comments:
        return None
    reviews = gh(f"repos/{repo}/pulls/{number}/reviews?per_page=100")
    if not isinstance(reviews, list):
        return None
    author_login = (entry.get("user") or {}).get("login", "unknown")
    review_states = {
        int(review["id"]): str(review.get("state") or "")
        for review in reviews
        if review.get("id") is not None
    }
    revisions = sorted(
        {
            str(comment.get("original_commit_id") or comment.get("commit_id") or "")
            for comment in comments
            if comment.get("in_reply_to_id") is None
        }
        - {""}
    )
    candidates: list[tuple[BenchmarkCase, dict[str, Any], int]] = []
    base_sha = str(entry["base"]["sha"])
    final_head_sha = str(entry["head"]["sha"])

    for revision_sha in revisions:
        labels = labels_from_review_revision(
            comments,
            reviews=review_states,
            pr_author_login=author_login,
            revision_sha=revision_sha,
            signal=signal,
        )
        if not labels:
            continue
        comparison = gh(f"repos/{repo}/compare/{base_sha}...{revision_sha}?per_page=100")
        if not isinstance(comparison, dict) or comparison.get("status") not in {
            "ahead",
            "identical",
        }:
            continue
        files = comparison.get("files")
        if not isinstance(files, list):
            continue
        python_files = [item for item in files if str(item.get("filename", "")).endswith(".py")]
        if not python_files:
            continue
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
            (file_diff.path, line)
            for file_diff in index.files
            for line in file_diff.commentable_lines
        }
        labels = [label for label in labels if (label.file_path, label.line) in reviewable_targets]
        if not labels:
            continue

        case_id = f"{repo.replace('/', '__')}__{number}"
        fixture = {
            "metadata": {
                "repo": repo,
                "number": number,
                "title": entry.get("title", ""),
                "state": entry.get("state", "closed"),
                "base_sha": base_sha,
                "head_sha": revision_sha,
                "final_head_sha": final_head_sha,
                "author_login": author_login,
                "candidate_signal": signal,
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
            "reviews": [
                {
                    "id": review.get("id"),
                    "state": review.get("state"),
                    "body": review.get("body"),
                    "commit_id": review.get("commit_id"),
                    "submitted_at": review.get("submitted_at"),
                    "user": (review.get("user") or {}).get("login"),
                    "user_type": (review.get("user") or {}).get("type"),
                }
                for review in reviews
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
                    "pull_request_review_id": item.get("pull_request_review_id"),
                    "commit_id": item.get("commit_id"),
                    "original_commit_id": item.get("original_commit_id"),
                    "in_reply_to_id": item.get("in_reply_to_id"),
                    "side": item.get("side"),
                    "created_at": item.get("created_at"),
                }
                for item in comments
            ],
        }
        case = BenchmarkCase(
            id=case_id,
            repo=repo,
            pr_number=number,
            base_sha=base_sha,
            head_sha=revision_sha,
            languages=["python"],
            diff_path=f"{fixture_reference}/{case_id}.json",
            human_comments_path=f"{fixture_reference}/{case_id}.json",
            labels=labels,
            notes=(
                f"exact review revision {revision_sha}; {len(python_files)} python files; "
                f"{signal} candidates require human adjudication"
            ),
        )
        if case_is_usable(case):
            candidates.append((case, fixture, sum(int(item.get("changes", 0)) for item in files)))

    if not candidates:
        return None
    case, fixture, _changes = max(
        candidates,
        key=lambda item: (len(item[0].labels), -item[2], item[0].head_sha),
    )
    fixtures.mkdir(parents=True, exist_ok=True)
    (fixtures / f"{case.id}.json").write_text(
        json.dumps(fixture, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    python_files = [item for item in fixture["files"] if item["filename"].endswith(".py")]
    print(
        f"  + {repo}#{number}@{case.head_sha[:12]}: "
        f"{len(case.labels)} labels, {len(python_files)} py files"
    )
    return case


def harvest_repo(
    repo: str,
    scan: int,
    *,
    limit: int,
    comment_pages: int,
    fixtures: Path,
    fixture_reference: str,
    signal: CandidateSignal,
) -> list[BenchmarkCase]:
    """Collect usable cases from merged PRs with changes-requested reviews."""

    cases: list[BenchmarkCase] = []
    pull_numbers = (
        gh_recent_defect_pull_requests(repo, scan, pages=comment_pages)
        if signal == "defect"
        else gh_search_pull_requests(repo, scan)
    )
    for number in pull_numbers:
        if len(cases) >= limit:
            break
        entry = gh(f"repos/{repo}/pulls/{number}")
        if not isinstance(entry, dict):
            continue
        case = harvest_pull_request(
            repo,
            entry,
            fixtures=fixtures,
            fixture_reference=fixture_reference,
            signal=signal,
        )
        if case is not None:
            cases.append(case)
    return cases


def harvest_seed_dataset(
    path: Path,
    *,
    limit: int,
    fixtures: Path,
    fixture_reference: str,
    signal: CandidateSignal,
) -> list[BenchmarkCase]:
    """Re-fetch named legacy cases so current v2 filters can assess them."""

    cases: list[BenchmarkCase] = []
    for seed in read_dataset(path):
        if len(cases) >= limit:
            break
        entry = gh(f"repos/{seed.repo}/pulls/{seed.pr_number}")
        if not isinstance(entry, dict):
            continue
        case = harvest_pull_request(
            seed.repo,
            entry,
            fixtures=fixtures,
            fixture_reference=fixture_reference,
            signal=signal,
        )
        if case is not None:
            cases.append(case)
    return cases


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the PRCritiq benchmark corpus")
    parser.add_argument("--target", type=int, default=24, help="How many cases to collect")
    parser.add_argument("--scan", type=int, default=30, help="PRs to scan per repository")
    parser.add_argument(
        "--comment-pages",
        type=int,
        default=3,
        help="Pages of recent repository review comments to inspect in defect mode",
    )
    parser.add_argument(
        "--signal",
        choices=("defect", "accepted", "requested_changes", "all"),
        default="defect",
        help=(
            "Candidate signal: concrete failure language in recent human review (default), "
            "explicit completed-action author reply, formal changes-requested review, "
            "or every mechanically eligible comment"
        ),
    )
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
    signal: CandidateSignal = args.signal

    collected: list[BenchmarkCase] = []
    seen: set[str] = set()
    if args.seed_dataset:
        print(f"re-fetching seed cases from {args.seed_dataset} ...")
        for case in harvest_seed_dataset(
            Path(args.seed_dataset),
            limit=args.target,
            fixtures=fixtures,
            fixture_reference=fixture_reference,
            signal=signal,
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
            limit=args.target - len(collected),
            comment_pages=args.comment_pages,
            fixtures=fixtures,
            fixture_reference=fixture_reference,
            signal=signal,
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
