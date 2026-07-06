# PRCritiq

PRCritiq is an evidence-backed pull request review agent. It is being built to review GitHub PRs with diff-aware context, safe tool evidence, a LangGraph review loop, and measured benchmark results.

## Current Status

M0 scaffold is implemented locally:

- Python package layout.
- FastAPI app with `GET /health` and scaffolded `POST /demo/review`.
- CLI scaffold for `health` and dry-run `review`.
- Configuration surface with strict `ACCELERATION=none|gpu|npu` validation.
- Ruff, pytest, and GitHub Actions CI skeleton.

Not implemented yet:

- Live GitHub App webhook behavior.
- PR diff parsing.
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

The dry-run command currently returns a scaffold report. It does not fetch GitHub data or call an LLM yet.

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

- `docs/getting-started.md` - local setup and M0 commands.
- `docs/architecture.md` - planned architecture and current scaffold.
- `docs/configuration.md` - environment variables.
- `docs/security.md` - security posture and current limits.
- `docs/evaluation.md` - benchmark plan and current evidence status.

## License

MIT.
