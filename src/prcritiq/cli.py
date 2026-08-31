"""Command line interface for PRCritiq."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from . import __version__
from .config import ConfigError, load_settings
from .github import GitHubClientError, RepoReferenceError
from .markdown import render_report
from .providers import ProviderError
from .review import run_dry_run
from .schemas import IMPLEMENTATION_STATUS
from .store import StoreError
from .workspace import WorkspaceError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="prcritiq", description="PRCritiq command line")
    parser.add_argument("--version", action="version", version=f"prcritiq {__version__}")

    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("health", help="Print local scaffold health as JSON")

    review = subparsers.add_parser("review", help="Review a pull request without posting")
    review.add_argument(
        "--repo",
        required=True,
        help="Repository URL, SSH clone address, or owner/name shorthand",
    )
    review.add_argument("--pr", required=True, type=int, help="Pull request number")
    review.add_argument("--mode", default="dry-run", choices=["dry-run"], help="Review mode")
    review.add_argument(
        "--post",
        action="store_true",
        help=(
            "Post validated findings to the pull request. Requires --review, "
            "GITHUB_TOKEN, and DATABASE_URL. This writes to GitHub."
        ),
    )
    review.add_argument(
        "--markdown",
        metavar="PATH",
        help="Also write a Markdown report to PATH.",
    )
    review.add_argument(
        "--persist",
        action="store_true",
        help="Record the run in Postgres. Requires DATABASE_URL.",
    )
    review.add_argument(
        "--review",
        action="store_true",
        help=(
            "Draft and critique findings with the routed model. Requires a provider "
            "API key and spends credits."
        ),
    )
    review.add_argument(
        "--tools",
        action="store_true",
        help=(
            "Run allowlisted static analysis over the changed files. "
            "Off by default because it fetches a source archive."
        ),
    )
    review.add_argument(
        "--context",
        action="store_true",
        help=(
            "Download the repository at the head commit and retrieve review context. "
            "Off by default because it fetches a source archive."
        ),
    )

    evaluate = subparsers.add_parser("eval", help="Run the benchmark over a dataset")
    evaluate.add_argument("--dataset", default="eval/dataset.jsonl", help="Dataset JSONL path")
    evaluate.add_argument("--out", default="eval/reports", help="Directory for reports")
    evaluate.add_argument(
        "--limit", type=int, default=0, help="Review only the first N cases (0 means all)"
    )
    evaluate.add_argument(
        "--fixture-mode",
        action="store_true",
        help="Use a deterministic mock model instead of a live provider.",
    )

    return parser


def run(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "health":
        settings = load_settings()
        payload = {
            "service": "prcritiq",
            "status": "ok",
            "version": __version__,
            "environment": settings.env,
            "acceleration": settings.acceleration.value,
            "implementation_status": IMPLEMENTATION_STATUS,
        }
    elif args.command == "review":
        try:
            report = run_dry_run(
                repo=args.repo,
                pr_number=args.pr,
                settings=load_settings(),
                include_context=args.context,
                include_tools=args.tools,
                include_review=args.review,
                persist=args.persist,
                post=args.post,
            )
        except (
            RepoReferenceError,
            GitHubClientError,
            ConfigError,
            WorkspaceError,
            ProviderError,
            StoreError,
        ) as exc:
            print(json.dumps({"service": "prcritiq", "error": str(exc)}, indent=2))
            return 1
        if args.markdown:
            Path(args.markdown).write_text(render_report(report), encoding="utf-8")
        payload = report.model_dump()
    elif args.command == "eval":
        return _run_eval(args)
    else:  # pragma: no cover - argparse prevents this branch.
        parser.error(f"unknown command: {args.command}")

    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return run(argv)


def _run_eval(args) -> int:
    """Run the benchmark and write both reports."""

    from .benchmark import (
        aggregate,
        build_report,
        execute_case,
        render_markdown_report,
        score_raw,
        threshold_sweep,
        write_json_report,
    )
    from .dataset import read_dataset
    from .providers import MockProvider, route

    root = Path.cwd()
    dataset_path = Path(args.dataset)
    if not dataset_path.exists():
        print(json.dumps({"service": "prcritiq", "error": f"No dataset at {dataset_path}"}))
        return 1

    cases = read_dataset(dataset_path)
    if args.limit:
        cases = cases[: args.limit]

    settings = load_settings()
    provider = MockProvider() if args.fixture_mode else None
    choice = route(settings=settings, prompt_characters=1000)

    from .providers import ProviderBillingError

    raws = []
    aborted: str | None = None
    for index, case in enumerate(cases, start=1):
        print(f"[{index}/{len(cases)}] {case.repo}#{case.pr_number}", flush=True)
        try:
            raws.append(execute_case(case, root=root, settings=settings, provider=provider))
        except ProviderBillingError as exc:
            aborted = str(exc)
            print(f"aborting: {aborted}", flush=True)
            break

    outcomes = [
        score_raw(raw, settings=settings, threshold=settings.min_publish_confidence) for raw in raws
    ]
    metrics = aggregate(outcomes)
    report = build_report(
        outcomes,
        metrics,
        model_policy=settings.model_policy,
        model="mock-reviewer" if args.fixture_mode else choice.model,
        dataset_version=dataset_path.name,
        mode="fixture" if args.fixture_mode else "live",
    )

    report["aborted"] = aborted
    report["min_publish_confidence"] = settings.min_publish_confidence
    report["threshold_sweep"] = threshold_sweep(
        raws, settings=settings, thresholds=[50, 60, 70, settings.min_publish_confidence, 85]
    )

    out = Path(args.out)
    write_json_report(report, out / "benchmark.json")
    (out / "benchmark.md").write_text(render_markdown_report(report), encoding="utf-8")

    print(json.dumps({"metrics": report["metrics"], "passed": report["passed"]}, indent=2))
    return 0
