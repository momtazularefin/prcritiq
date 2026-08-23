# PRCritiq

PRCritiq is an evidence-backed pull request review agent. It is being built to review GitHub PRs with diff-aware context, safe tool evidence, a LangGraph review loop, and measured benchmark results.

## Current Status

M2 diff parsing and guardrails are implemented locally, on top of the M1 intake surfaces:

- Unified-diff parser mapping every patch to hunks, changed new-line numbers, and removed old-line numbers. It is strict on purpose: a patch whose hunk header disagrees with its body is rejected rather than parsed into line numbers that would be silently wrong.
- Changed-line validation, so an inline comment can only ever target a line this pull request actually added. Rejected targets are returned with a reason rather than dropped, so a run can report its invalid-line rate.
- Guardrail gate with structured decisions for generated, lockfile, vendored, binary, oversized-file, oversized-diff, unsupported-language, and unsafe-path files, plus a per-pull-request reviewable-file budget. Every skip carries an explicit reason.
- FastAPI app with `GET /health`, scaffolded `POST /demo/review`, and signed `POST /webhooks/github`.
- CLI for `health` and a scaffold dry-run `review`.
- GitHub webhook signature verification, pull-request idempotency keys, and a thin GitHub REST client boundary for PR metadata and changed files.
- Configuration surface with strict `ACCELERATION=none|gpu|npu` validation.
- Ruff, pytest, and GitHub Actions CI.

Not implemented yet:

- Wiring the diff and guardrail layers into the dry-run command, which still returns a scaffold report.
- Context retrieval.
- Static analysis evidence.
- LangGraph review loop.
- GitHub comment posting.
- Benchmark metrics.

## Quick Start

```powershell
uv sync --dev
uv run prcritiq health
uv run prcritiq review --repo https://github.com/example/repo --pr 1 --mode dry-run
uv run ruff check .
uv run pytest
```

The dry-run command currently returns a scaffold report. It does not fetch GitHub data, call an LLM, or post comments yet.

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
- `docs/architecture.md` - planned architecture and current scaffold.
- `docs/configuration.md` - environment variables.
- `docs/security.md` - security posture and current limits.
- `docs/evaluation.md` - benchmark plan and current evidence status.

## License

MIT.
