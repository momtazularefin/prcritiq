# PRCritiq

PRCritiq is an evidence-backed pull request review agent. It is being built to review GitHub PRs with diff-aware context, safe tool evidence, a LangGraph review loop, and measured benchmark results.

## Current Status

M6 adds durable run history. The agent reviews pull requests and records what it did, on top of the M1 intake surfaces, M2 diff parsing, M3 retrieval, M4 tool evidence, and the M5 review loop:

- Unified-diff parser mapping every patch to hunks, changed new-line numbers, and removed old-line numbers. It is strict on purpose: a patch whose hunk header disagrees with its body is rejected rather than parsed into line numbers that would be silently wrong.
- Changed-line validation, so an inline comment can only ever target a line this pull request actually added. Rejected targets are returned with a reason rather than dropped, so a run can report its invalid-line rate.
- Guardrail gate with structured decisions for generated, lockfile, vendored, binary, oversized-file, oversized-diff, unsupported-language, and unsafe-path files, plus a per-pull-request reviewable-file budget. Every skip carries an explicit reason.
- Context retrieval: `prcritiq review --context` downloads the repository at the head commit, chunks it by real symbol boundaries, and retrieves related code by imports, nearby tests, directory siblings, and identifier-aware BM25 ranking. Every retrieved chunk carries a chunk id and the reason it was chosen, so a later finding can cite it.
- Bounded and safe: extraction rejects traversal and link members, is capped by file count and total bytes, and writes only inside a temporary workspace that is deleted afterwards. Context has an explicit budget, with a reserved share for the changed code so a large diff cannot starve out related context.
- Static analysis: `prcritiq review --tools` runs allowlisted checks over the changed files and reports structured diagnostics, flagging which land on lines the pull request actually changed. Commands come from a fixed allowlist, never from the repository; no shell is used; the executable must resolve outside the workspace so a repository cannot supply its own binary; the child process gets a scrubbed environment so no token reaches it; and runs are bounded by a timeout and an output cap.
- Every tool is reported even when it does not run, with the reason. `eslint` is refused on repositories that configure it, because eslint loads the repository's own config as JavaScript and would execute the pull request's code. `tsc` is refused because the snapshot excludes `node_modules`, so it would report a missing module for every dependency rather than real findings.
- LangGraph review loop: `prcritiq review --review` runs the seven-node graph from the design, `fetch_diff -> guardrail_gate -> static_analysis -> retrieve_context -> reason_and_draft -> self_critique -> post_or_summarize`. Each node is an inspectable step, not a line in a prompt.
- Evidence-backed findings: every finding carries severity, confidence as a percentage, category, the evidence behind it, a suggested fix, and the chunk ids or tool codes it drew on.
- Self-critique that assumes the model misbehaves: candidates are re-checked against the diff, the retrieved context, and the tool output rather than trusted. A finding is suppressed when it targets a line the pull request never added, carries no evidence, cites a source this run never produced, reads as generic filler, duplicates another, or falls under the confidence threshold. Suppressed candidates are kept and reported with their reason, so a run publishes its own invalid-line rate instead of hiding it.
- Deterministic model routing with no silent fallback: Claude for compact judgment-heavy synthesis, OpenAI for extensive context or batch evaluation. The choice and its reason are recorded on every report. A missing key or an unknown policy fails loudly rather than reviewing with something else and reporting the model it was asked for.
- Prompt-injection defense: diffs, retrieved code, and tool output are fenced as labelled untrusted data, the system prompt states they cannot change the instructions, and every finding is validated against the diff afterwards regardless of what the model was told.
- Postgres run store: `prcritiq review --persist` records the run, its per-file guardrail verdicts, tool outcomes, findings, and routing decision. Suppressed findings are stored alongside published ones, because the invalid-line and no-evidence rates a benchmark reports are computed from them.
- Idempotency enforced by the database, not by an application check: the run key is `UNIQUE`, so a redelivered webhook or a repeated command resolves to the existing run instead of writing a second one. Two workers racing cannot both win.
- A run state machine with no way back: `pending -> running -> summarized -> posted`, with `failed` and `skipped` as terminal outcomes. A finished run cannot be reopened and rewritten.
- Optional LangSmith trace linkage. Tracing is off unless it is both selected and credentialed, and a run never records a fabricated trace id, because an identifier nobody can resolve is worse than none.
- Working dry-run review: `prcritiq review` fetches a real pull request, parses every patch, applies the guardrail gate, and prints a JSON report of what it would and would not review. No model is called and nothing is posted.
- FastAPI app with `GET /health`, `POST /demo/review` running that same dry run, and signed `POST /webhooks/github`.
- Repository references accept the HTTPS and SSH clone forms, a pasted pull-request URL, and the `owner/name` shorthand.
- GitHub webhook signature verification, pull-request idempotency keys, and a thin GitHub REST client boundary for PR metadata and changed files.
- Configuration surface with strict `ACCELERATION=none|gpu|npu` validation.
- Ruff, pytest, and GitHub Actions CI.

Not implemented yet:

- GitHub comment posting.
- Benchmark metrics.

Retrieval ranking is identifier-aware BM25, not vector embeddings. This is a settled design choice rather than a gap: what connects two regions of a codebase is usually a shared identifier, which lexical matching captures directly, and keeping it lexical means retrieval is deterministic, reproducible offline, and free of a model download. A vector provider can be added behind the existing `SimilarityProvider` protocol without touching any caller. `PRCRITIQ_SIMILARITY=embedding` is a valid configuration value that fails with a clear error rather than quietly running the lexical path instead.

## Quick Start

```powershell
uv sync --dev
uv run prcritiq health
uv run prcritiq review --repo pydantic/pydantic --pr 13680 --mode dry-run
uv run prcritiq review --repo pydantic/pydantic --pr 13680 --context --tools
uv run prcritiq review --repo pydantic/pydantic --pr 13680 --context --tools --review
docker compose up -d   # local Postgres for --persist
uv run ruff check .
uv run pytest
```

Without `--review` the command reads the pull request and reports parsed diffs and guardrail decisions without calling a model. With `--review` it drafts and critiques findings using the routed provider, which requires an API key and spends credits. Nothing is ever posted to GitHub yet; posting arrives in a later milestone, and the report says which stages actually ran rather than letting an empty findings list read as a clean bill of health.

Public repositories work without a token. Set `GITHUB_TOKEN` to raise the API rate limit or to reach a private repository.

## Project Shape

```text
src/prcritiq/       Python package
tests/              unit and smoke tests
docs/               public project docs
eval/               future benchmark fixtures and reports
.github/workflows/  CI workflow
```

## Design Direction

The target reviewer flow is:

```text
fetch_diff -> guardrail_gate -> static_analysis -> retrieve_context -> reason_and_draft -> self_critique -> post_or_summarize
```

PRCritiq will prefer silence over weak comments. Every reportable finding must include severity, confidence, evidence, and a suggested fix, and inline comments must target valid changed PR lines.

## Documentation

- `docs/getting-started.md` - local setup and current commands.
- `docs/architecture.md` - planned architecture and what is built today.
- `docs/configuration.md` - environment variables.
- `docs/security.md` - security posture and current limits.
- `docs/evaluation.md` - benchmark plan and current evidence status.

## License

MIT.
