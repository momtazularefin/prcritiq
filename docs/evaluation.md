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
- Review-graph tests run the whole loop against a mock provider, so CI never needs a live model or an API key.
- Store tests run against a real Postgres, provided as a service container in CI and by `docker compose up -d` locally. They are skipped rather than faked when no `DATABASE_URL` is set, so a green run without a database never reads as evidence that the schema works.
- Idempotency is tested by creating the same run twice and asserting one row, and the run state machine is tested for both legal and refused transitions.
- Self-critique tests cover every suppression reason, the precedence between them, and that suppressed candidates are retained for rate reporting.
- Routing tests cover the policy matrix and prove that a missing provider key raises instead of falling back.
- Tool-runner tests cover the security boundary directly: secrets are absent from a real child process, a binary planted inside the workspace is refused, shell metacharacters stay inert, timeouts and output caps hold, and malformed tool output raises instead of reading as a clean result.
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
