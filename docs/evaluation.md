# Evaluation

## Status

The benchmark harness and corpus are built. The corpus is 20 real merged pull requests drawn from 8 code-heavy Python repositories, carrying 46 labels taken from inline review comments humans left on those pull requests, frozen as fixtures under `eval/`.

No live benchmark result is published. Every case has so far been scored against a mocked model, which proves the measurement rather than the reviewer. The live run is blocked on `ANTHROPIC_WORKSPACE_ID`, because the configured key is identity-linked and the API rejects requests that do not name a workspace.

Two honest limitations apply to any number this harness eventually produces. Labels are filtered by documented heuristics rather than hand-adjudicated, and finding-to-label matching is mechanical: same file, a line within five, and at least 18 percent shared distinctive vocabulary. The evaluation plan asks for manual adjudication of ambiguous matches, which this harness does not perform. Both are stated in every report it writes.

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
- Posting tests use a recording client, so no test writes to a real pull request. They cover the suppressed-finding gate, the last-gate line check, duplicate refusal on a second run, stored comment ids, and the quiet path when nothing clears the bar.
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
