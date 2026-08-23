# Evaluation

## Status

M1 and M2 are covered by unit and fixture tests, including a stubbed end-to-end dry run. The PR-review benchmark is planned for a later milestone.

## Current Evidence

- Unit tests cover configuration validation.
- API tests cover health, demo review, and webhook responses, including the bad-gateway mapping for GitHub failures.
- API tests cover valid and invalid GitHub webhook signatures.
- Unit tests cover webhook idempotency key construction.
- Unit tests cover the mocked GitHub client for PR metadata and changed files.
- CLI tests cover health, dry-run report output, and the non-zero exit path for an unusable repository reference.
- Diff tests parse fixture patches for renames, new and deleted files, blank context lines, missing trailing newlines, and malformed hunk headers.
- Guardrail tests cover every skip decision, the documented precedence between them, and the per-pull-request file budget.
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
