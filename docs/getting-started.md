# Getting Started

## Status

The local review pipeline is implemented through diff intake, optional repository context and static analysis, model drafting, deterministic critique, persistence, and opt-in posting. The benchmark is diagnostic while its labels are being rebuilt and adjudicated.

## Requirements

- Python 3.12.
- uv.

## Local Setup

```powershell
uv sync --dev
uv run prcritiq health
uv run prcritiq review --repo pydantic/pydantic --pr 13680 --mode dry-run
uv run prcritiq review --repo pydantic/pydantic --pr 13680 --context --tools --review
uv run prcritiq eval --fixture-mode --limit 2
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
- `POST /webhooks/github` verifies signatures and acknowledges supported events. It does not yet enqueue a review run.
- The CLI returns the same health payload and dry-run report.
- `review --review` calls the configured model; `--context` and `--tools` fetch a bounded source archive; `--post` writes validated findings only when persistence and GitHub credentials are configured.
- `eval --provider openai --model gpt-5.6-terra --effort low` selects one bake-off configuration without editing `.env`.

## Local Postgres

`--persist` needs a database. The bundled compose file provides one on port 5433, chosen so it never collides with a Postgres already running on the host:

```powershell
docker compose up -d
$env:DATABASE_URL = "postgresql://prcritiq:prcritiq@localhost:5433/prcritiq"
uv run prcritiq review --repo pydantic/pydantic --pr 13680 --persist
```

Migrations run automatically on connect and are idempotent. The command above fetches GitHub data but does not call a model or post comments unless the corresponding flags are supplied.
