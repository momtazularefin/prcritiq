# Getting Started

## Status

This document describes the M1 intake surfaces and the M2 dry-run review. The reasoning engine is not implemented yet.

## Requirements

- Python 3.12.
- uv.

## Local Setup

```powershell
uv sync --dev
uv run prcritiq health
uv run prcritiq review --repo pydantic/pydantic --pr 13680 --mode dry-run
```

## Validation

```powershell
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

## Current Behavior

- `GET /health` returns service status.
- `POST /demo/review` runs a dry review of a real pull request and returns the report.
- `POST /webhooks/github` verifies GitHub webhook signatures and accepts supported pull request events.
- The CLI returns the same health payload and dry-run report.
- No GitHub data is fetched.
- No LLM provider is called.
- No PR comments are posted.
