# Architecture

## Status

M1 contains intake boundaries and executable dry-run scaffold behavior. The full review architecture is planned but not implemented yet.

## Target Flow

```text
fetch_diff -> guardrail_gate -> static_analysis -> retrieve_context -> reason_and_draft -> self_critique -> post_or_summarize
```

## Current Components

- `prcritiq.api` exposes health and demo review scaffold endpoints.
- `prcritiq.cli` exposes health and dry-run review scaffold commands.
- `prcritiq.config` validates the configuration surface, including strict acceleration modes.
- `prcritiq.webhooks` verifies GitHub webhook signatures and builds review-run idempotency keys.
- `prcritiq.github` wraps PR metadata and changed-file reads through the GitHub REST API.
- `prcritiq.reporting` returns an explicit M1 dry-run report that states no review, model, or posting work occurred.

## Planned Components

- PR diff parser and changed-line model.
- Guardrail gate for noisy or unsafe files.
- Repository context retrieval.
- Safe static analysis evidence.
- LangGraph review loop.
- Postgres run store and trace metadata.
- GitHub posting and dry-run reports.
- Benchmark harness.
