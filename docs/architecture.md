# Architecture

## Status

The intake, diff and guardrail path, repository snapshotting, retrieval, allowlisted static analysis, LangGraph review loop, Postgres store, opt-in posting, Markdown reporting, and benchmark harness are implemented. The benchmark corpus is being rebuilt because its original mechanically harvested labels are not certified ground truth.

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
- `prcritiq.providers` routes deterministically between Claude and OpenAI, uses typed structured output, records usage, and refuses to substitute one for the other.
- `prcritiq.critique` re-validates every drafted finding against the run's own evidence.
- `prcritiq.graph` wires the seven design nodes into a LangGraph state graph.
- `prcritiq.store` owns the Postgres schema, the run state machine, and idempotent run creation.
- `prcritiq.tracing` opens a LangSmith root run when tracing is configured, and records nothing when it is not.
- `prcritiq.posting` publishes validated findings, re-checking the target line and refusing a duplicate body.
- `prcritiq.markdown` renders a report for a human reader.
- `prcritiq.review` orchestrates fetch, parse, gate, retrieve, analyze, review, persist, post, and report. Context retrieval and static analysis share one repository download and one workspace. It is the only module in the dry-run path that performs I/O.
- `prcritiq.config` validates the configuration surface, including strict acceleration modes.
- `prcritiq.webhooks` verifies GitHub webhook signatures and builds review-run idempotency keys.
- `prcritiq.github` wraps PR metadata and changed-file reads through the GitHub REST API.
- `prcritiq.reporting` builds explicit dry-run and review reports that name which stages ran.

## Next Components

- Human-adjudicated, revision-correct benchmark fixtures.
- Per-hunk high-recall candidate generation.
- Candidate-targeted context retrieval instead of one global context pack.
- Independent semantic verification before publication.
- Durable webhook-triggered review execution.
- Crash- and concurrency-safe posting claims across the GitHub/database boundary.
