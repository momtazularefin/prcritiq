# Architecture

## Status

The intake, dry-run webhook execution, diff and guardrail path, repository snapshotting, retrieval, allowlisted static analysis, LangGraph review loop, Postgres store, opt-in posting, Markdown reporting, and benchmark harness are implemented. A separate experimental verifier can replay saved candidates against bounded base/head evidence. The certified smoke set has 3 PRs and 5 approved defects; the larger legacy comment corpus is not certified ground truth.

## Review Flow

```text
fetch_diff -> guardrail_gate -> static_analysis -> retrieve_context -> reason_and_draft -> self_critique -> post_or_summarize
```

## Current Components

- `prcritiq.api` exposes health, the rate-limited non-posting demo review, the signed webhook, and public run status.
- `prcritiq.intake` turns an accepted `pull_request` event into a pending run, then carries it to a terminal state in the background: re-check visibility and head, apply the guardrails, optionally run the review graph, and record the outcome. It never posts.
- `prcritiq.github_app` signs the app JWT and exchanges it for an installation token, with no fallback to another credential.
- `prcritiq.ratelimit` is the in-process sliding-window limit on the demo.
- `prcritiq.cli` exposes health, review, fixture/live evaluation, and opt-in verification replay commands. Model drafting and GitHub posting require explicit flags.
- `prcritiq.diff` parses patches into hunks and changed lines, and validates inline-comment targets.
- `prcritiq.guardrails` decides which changed files are in scope, with an explicit reason for every skip.
- `prcritiq.workspace` downloads a repository archive at an exact SHA and extracts it into a temporary workspace under strict safety and size limits.
- `prcritiq.chunking` splits sources into retrievable chunks, using the standard library `ast` for Python and shallow heuristics for JS/TS.
- `prcritiq.retrieval` indexes chunks and retrieves related context by imports, nearby tests, directory siblings, and BM25 ranking, under an explicit budget.
- `prcritiq.tools` runs allowlisted static checks against the snapshot under a timeout, an output cap, a scrubbed environment, and no shell.
- `prcritiq.findings` defines the finding schema and the suppression vocabulary.
- `prcritiq.prompts` builds prompts that fence untrusted repository content as data.
- `prcritiq.providers` routes deterministically between Claude and OpenAI, uses typed structured output, records usage, and refuses to substitute one for the other.
- `prcritiq.critique` deterministically re-validates line placement, evidence references, generic wording, duplicates, and confidence. It does not establish semantic correctness.
- `prcritiq.graph` wires the seven design nodes into a LangGraph state graph.
- `prcritiq.store` owns the Postgres schema, the run state machine, and idempotent run creation.
- `prcritiq.tracing` opens a LangSmith root run when tracing is configured, and records nothing when it is not.
- `prcritiq.posting` publishes validated findings, re-checking the target line and refusing a duplicate body.
- `prcritiq.markdown` renders a report for a human reader.
- `prcritiq.review` orchestrates fetch, parse, gate, retrieve, analyze, review, persist, post, and report through the corresponding I/O components. Context retrieval and static analysis share one repository download and one workspace.
- `prcritiq.config` validates the configuration surface, including strict acceleration modes.
- `prcritiq.webhooks` verifies GitHub webhook signatures and builds review-run idempotency keys.
- `prcritiq.github` wraps PR metadata and changed-file reads through the GitHub REST API.
- `prcritiq.reporting` builds explicit dry-run and review reports that name which stages ran.
- `prcritiq.benchmark` and `prcritiq.dataset` load frozen fixtures, check certified-label provenance, score retained findings, and emit JSON/Markdown evaluation reports. Fixture mode uses a mock provider and does not measure model quality.

## Separate Experimental Verification

- `prcritiq.verification_sources` validates pinned revisions and paths, then optionally fetches bounded source files; a retained local bundle avoids network access.
- `prcritiq.verification_context` selects candidate-specific base/head functions or bounded windows with source IDs and explicit coverage gaps.
- `prcritiq.verification` defines the structured verifier protocol and validates evidence references. Protocol v2 distinguishes `partial` from `confirmed`, requires a supported introduced-failure or contract-violation basis, and requires concrete base/head behavior summaries and citations for either supported verdict. A contract-based claim also requires cited contract evidence. These structural checks do not prove the model's assertions.
- `prcritiq.verification_replay` preflights the selected dataset, original candidates and sources, preserves publication/suppression provenance, and writes separate reports. Preparation is the default; only `--live` permits paid verification. It never rewrites candidates, posts comments, or changes review routing or thresholds.

The [frozen verifier comparison](../eval/runs/2026-09-20-verifier-comparison/comparison.md) predates protocol v2. It favored Sol-low as an experimental baseline, while exposing false confirmations and partial allegations. The stricter v2 contract has no measured live quality gain yet; historical verdicts are not reclassified as new results. See [verification](verification.md) for safety boundaries and reproduction.

## Deferred Or Incomplete Work

- A statistically sufficient, human-adjudicated benchmark with positive and clean PRs and passing quality gates.
- Per-hunk high-recall candidate generation.
- Candidate-targeted retrieval in the production review path and deeper dependency-contract evidence in replay.
- Integration of independent semantic verification before publication.
- Reclaiming webhook runs interrupted by a restart. A run left `pending` or `running` is reported, not retried.
- Crash- and concurrency-safe posting claims across the GitHub/database boundary.

The service deploys to one Hetzner node behind Caddy (ADR-019); see [deployment](deployment.md). The [portfolio release checklist](portfolio-release.md) records the approved release scope and which original acceptance criteria remain unmet.
