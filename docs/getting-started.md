# Getting Started

## Status

This document describes the M1 intake and dry-run scaffold. The reviewer engine is not implemented yet.

## Requirements

- Python 3.12.
- uv.

## Local Setup

```powershell
uv sync --dev
uv run prcritiq health
uv run prcritiq review --repo https://github.com/example/repo --pr 1 --mode dry-run
```

## Validation

```powershell
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

## Current Behavior

- `GET /health` returns scaffold status.
- `POST /demo/review` returns a scaffold dry-run report.
- `POST /webhooks/github` verifies GitHub webhook signatures and accepts supported pull request events.
- The CLI returns the same scaffold health and dry-run report.
- No GitHub data is fetched.
- No LLM provider is called.
- No PR comments are posted.
