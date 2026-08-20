"""Command line interface for PRCritiq."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

from . import __version__
from .config import load_settings
from .reporting import build_scaffold_report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="prcritiq", description="PRCritiq command line")
    parser.add_argument("--version", action="version", version=f"prcritiq {__version__}")

    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("health", help="Print local scaffold health as JSON")

    review = subparsers.add_parser("review", help="Run a dry-run PR review scaffold report")
    review.add_argument("--repo", required=True, help="Repository URL")
    review.add_argument("--pr", required=True, type=int, help="Pull request number")
    review.add_argument("--mode", default="dry-run", choices=["dry-run"], help="Review mode")

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
            "implementation_status": "m1_intake_dry_run",
        }
    elif args.command == "review":
        payload = build_scaffold_report(repo=args.repo, pr_number=args.pr).model_dump()
    else:  # pragma: no cover - argparse prevents this branch.
        parser.error(f"unknown command: {args.command}")

    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return run(argv)
