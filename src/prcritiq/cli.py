"""Command line interface for PRCritiq."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

from . import __version__
from .config import ConfigError, load_settings
from .github import GitHubClientError, RepoReferenceError
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
        payload = report.model_dump()
    else:  # pragma: no cover - argparse prevents this branch.
        parser.error(f"unknown command: {args.command}")

    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return run(argv)
