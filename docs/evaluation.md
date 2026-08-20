# Evaluation

## Status

M1 has intake and scaffold tests. The PR-review benchmark is planned for a later milestone.

## Current Evidence

- Unit tests cover configuration validation.
- API tests cover scaffold health and demo review responses.
- API tests cover valid and invalid GitHub webhook signatures.
- Unit tests cover webhook idempotency key construction.
- Unit tests cover the mocked GitHub client for PR metadata and changed files.
- CLI tests cover scaffold health and dry-run report output.
- CI is configured to run Ruff and pytest in a stable job named `test`.

## Target Benchmark

The full benchmark will use at least 20 real code-heavy PRs with substantive human review comments.

Metrics will include:

- issue recall,
- comment precision,
- invalid-line rate,
- no-evidence rate,
- spam rate,
- latency,
- cost per PR.

The benchmark must not use prose-heavy repositories such as `chiphuyen/dmls-book`.
