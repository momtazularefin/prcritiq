# PRCritiq

PRCritiq is an evidence-backed pull request review agent. It is being built to review GitHub PRs with diff-aware context, safe tool evidence, a LangGraph review loop, and measured benchmark results.

## Current Status

M4 static-analysis evidence is implemented, on top of the M1 intake surfaces, M2 diff parsing, and M3 retrieval:

- Unified-diff parser mapping every patch to hunks, changed new-line numbers, and removed old-line numbers. It is strict on purpose: a patch whose hunk header disagrees with its body is rejected rather than parsed into line numbers that would be silently wrong.
- Changed-line validation, so an inline comment can only ever target a line this pull request actually added. Rejected targets are returned with a reason rather than dropped, so a run can report its invalid-line rate.
- Guardrail gate with structured decisions for generated, lockfile, vendored, binary, oversized-file, oversized-diff, unsupported-language, and unsafe-path files, plus a per-pull-request reviewable-file budget. Every skip carries an explicit reason.
- Context retrieval: `prcritiq review --context` downloads the repository at the head commit, chunks it by real symbol boundaries, and retrieves related code by imports, nearby tests, directory siblings, and identifier-aware BM25 ranking. Every retrieved chunk carries a chunk id and the reason it was chosen, so a later finding can cite it.
- Bounded and safe: extraction rejects traversal and link members, is capped by file count and total bytes, and writes only inside a temporary workspace that is deleted afterwards. Context has an explicit budget, with a reserved share for the changed code so a large diff cannot starve out related context.
- Static analysis: `prcritiq review --tools` runs allowlisted checks over the changed files and reports structured diagnostics, flagging which land on lines the pull request actually changed. Commands come from a fixed allowlist, never from the repository; no shell is used; the executable must resolve outside the workspace so a repository cannot supply its own binary; the child process gets a scrubbed environment so no token reaches it; and runs are bounded by a timeout and an output cap.
- Every tool is reported even when it does not run, with the reason. `eslint` is refused on repositories that configure it, because eslint loads the repository's own config as JavaScript and would execute the pull request's code. `tsc` is refused because the snapshot excludes `node_modules`, so it would report a missing module for every dependency rather than real findings.
- Working dry-run review: `prcritiq review` fetches a real pull request, parses every patch, applies the guardrail gate, and prints a JSON report of what it would and would not review. No model is called and nothing is posted.
- FastAPI app with `GET /health`, `POST /demo/review` running that same dry run, and signed `POST /webhooks/github`.
- Repository references accept the HTTPS and SSH clone forms, a pasted pull-request URL, and the `owner/name` shorthand.
- GitHub webhook signature verification, pull-request idempotency keys, and a thin GitHub REST client boundary for PR metadata and changed files.
- Configuration surface with strict `ACCELERATION=none|gpu|npu` validation.
- Ruff, pytest, and GitHub Actions CI.

Not implemented yet:

- LangGraph review loop.
- GitHub comment posting.
- Benchmark metrics.

Retrieval ranking is identifier-aware BM25, not vector embeddings. This is a settled design choice rather than a gap: what connects two regions of a codebase is usually a shared identifier, which lexical matching captures directly, and keeping it lexical means retrieval is deterministic, reproducible offline, and free of a model download. A vector provider can be added behind the existing `SimilarityProvider` protocol without touching any caller. `PRCRITIQ_SIMILARITY=embedding` is a valid configuration value that fails with a clear error rather than quietly running the lexical path instead.

## Quick Start

```powershell
uv sync --dev
uv run prcritiq health
uv run prcritiq review --repo pydantic/pydantic --pr 13680 --mode dry-run
uv run prcritiq review --repo pydantic/pydantic --pr 13680 --context --tools
uv run ruff check .
uv run pytest
```

The dry-run command reads the pull request from the GitHub REST API and reports parsed diffs and guardrail decisions. It does not call a model or post comments yet, so `findings` is always empty and the report says so explicitly rather than letting an empty list read as a clean bill of health.

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
