# Architecture

## Status

M1 through M6 are built: intake boundaries, diff parsing, changed-line validation, the guardrail gate, repository snapshotting, source chunking, context retrieval, allowlisted static analysis, the LangGraph review loop with self-critique, the Postgres run store, and a dry-run review that reports all of it. GitHub posting and the benchmark are planned but not implemented yet.

## Target Flow

```text
fetch_diff -> guardrail_gate -> static_analysis -> retrieve_context -> reason_and_draft -> self_critique -> post_or_summarize
```

## Current Components

- `prcritiq.api` exposes health, demo review, and webhook endpoints.
- `prcritiq.cli` exposes health and dry-run review commands.
- `prcritiq.diff` parses patches into hunks and changed lines, and validates inline-comment targets.
- `prcritiq.guardrails` decides which changed files are in scope, with an explicit reason for every skip.
- `prcritiq.workspace` downloads a repository archive at an exact SHA and extracts it into a temporary workspace under strict safety and size limits.
- `prcritiq.chunking` splits sources into retrievable chunks, using the standard library `ast` for Python and shallow heuristics for JS/TS.
- `prcritiq.retrieval` indexes chunks and retrieves related context by imports, nearby tests, directory siblings, and BM25 ranking, under an explicit budget.
- `prcritiq.tools` runs allowlisted static checks against the snapshot under a timeout, an output cap, a scrubbed environment, and no shell.
- `prcritiq.findings` defines the finding schema and the suppression vocabulary.
- `prcritiq.prompts` builds prompts that fence untrusted repository content as data.
- `prcritiq.providers` routes deterministically between Claude and OpenAI, and refuses to substitute one for the other.
- `prcritiq.critique` re-validates every drafted finding against the run's own evidence.
- `prcritiq.graph` wires the seven design nodes into a LangGraph state graph.
- `prcritiq.store` owns the Postgres schema, the run state machine, and idempotent run creation.
- `prcritiq.tracing` opens a LangSmith root run when tracing is configured, and records nothing when it is not.
- `prcritiq.review` orchestrates fetch, parse, gate, retrieve, analyze, review, persist, and report. Context retrieval and static analysis share one repository download and one workspace. It is the only module in the dry-run path that performs I/O.
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
