# PRCritiq

PRCritiq is an evidence-backed pull request review agent. It is being built to review GitHub PRs with diff-aware context, safe tool evidence, a LangGraph review loop, and measured benchmark results.

## Current Status

M8 adds the benchmark that measures whether any of this actually works. On top of the M1 intake surfaces, M2 diff parsing, M3 retrieval, M4 tool evidence, the M5 review loop, the M6 run store, and M7 posting:

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
- GitHub posting behind three gates: `prcritiq review --post` publishes only findings that survived self-critique, re-validates every target line immediately before the write because GitHub accepts comments on a wider set of lines than a pull request actually added, and refuses to post a body whose hash is already recorded for that run. Posting requires `--review`, `GITHUB_TOKEN`, and a database, and fails loudly without them.
- A quiet run stays quiet. When nothing clears the bar, nothing is posted and the outcome is recorded in run history instead, because a comment announcing that there is nothing to say is still a comment.
- Markdown reports: `--markdown PATH` renders the run for a human reader, listing findings in full, suppression counts with the invalid-line rate, files not reviewed with reasons, and tool outcomes. Untrusted pull request text is escaped so it cannot break out of a table cell.
- Benchmark corpus: 20 real merged pull requests from 8 code-heavy Python repositories, with 46 labels taken from inline review comments humans actually left. Frozen as fixtures under `eval/`, so a published number is reproducible from this repository without re-fetching from GitHub.
- Metrics from the evaluation plan: issue recall, comment precision, invalid-line rate, no-evidence rate, spam rate, quiet runs, median and P95 latency, and cost per pull request with token counts. `prcritiq eval` writes a JSON report and a Markdown report, and judges both against the plan's pass gates.
- The invalid-line rate is verified against each pull request's real changed lines rather than assumed from the fact that self-critique ran.
- Working dry-run review: `prcritiq review` fetches a real pull request, parses every patch, applies the guardrail gate, and prints a JSON report of what it would and would not review. No model is called and nothing is posted.
- FastAPI app with `GET /health`, `POST /demo/review` running that same dry run, and signed `POST /webhooks/github`.
- Repository references accept the HTTPS and SSH clone forms, a pasted pull-request URL, and the `owner/name` shorthand.
- GitHub webhook signature verification, pull-request idempotency keys, and a thin GitHub REST client boundary for PR metadata and changed files.
- Configuration surface with strict `ACCELERATION=none|gpu|npu` validation.
- Ruff, pytest, and GitHub Actions CI.

Not implemented yet:

- Deployment of the public demo and its managed database.
- A benchmark result that passes its own gates. See below.

### Benchmark result: does not pass

The live benchmark has run, and PRCritiq does not pass it. This section states what was measured rather than what was hoped for.

The most recent run reviewed **13 of 20 cases** before the API credit balance was exhausted, so the result is incomplete as well as failing.

| Gate | Target | Actual | Result |
| --- | --- | --- | --- |
| Corpus size | >= 20 reviewed | 13 of 20 | FAIL |
| Run completeness | 0 failed cases | 7 failed | FAIL |
| Issue recall | > 0.50 | 0.00 | FAIL |
| Comment precision | >= 0.70 | 0.00 | FAIL |
| Invalid-line rate | = 0.00 | 0.00 | PASS |
| No-evidence rate | = 0.00 | 0.00 | PASS |
| Spam rate | <= 0.10 | 0.00 | PASS |

The safety gates hold: nothing was reported on an invalid line, nothing without evidence, nothing generic or duplicated. The quality gates do not. At the default publish threshold of 78 the reviewer published nothing at all; every candidate it drafted was suppressed as low confidence, which is the reviewer declining to stand behind its own output rather than the gate misfiring.

The publish-threshold sweep, scored from the same model calls:

| Min confidence | Recall | Precision | Findings | Quiet runs |
| --- | --- | --- | --- | --- |
| 50 | 0.02 | 0.11 | 9 | 15 |
| 60 | 0.00 | 0.00 | 4 | 17 |
| 70 | 0.00 | 0.00 | 0 | 20 |
| 78 (default) | 0.00 | 0.00 | 0 | 20 |
| 85 | 0.00 | 0.00 | 0 | 20 |

Lowering the bar surfaces findings but does not find the issues humans found: at confidence 50 it matched 1 of 46 labels. The bottleneck is what the reviewer drafts, not where the threshold sits.

Cost was $5.02 for the run, $0.25 per pull request, median latency 62s. Full reports are in `eval/reports/`.

Read these numbers with the labelling method in mind. Labels are inline review comments filtered by documented heuristics, not hand-adjudicated, and matching is mechanical: same file, a line within 5, and shared distinctive vocabulary. Human review comments are often design discussion rather than defects, so this measures agreement with reviewers rather than defect detection. That cuts both ways, and it makes this weaker evidence than a curated corpus would be.

Retrieval ranking is identifier-aware BM25, not vector embeddings. This is a settled design choice rather than a gap: what connects two regions of a codebase is usually a shared identifier, which lexical matching captures directly, and keeping it lexical means retrieval is deterministic, reproducible offline, and free of a model download. A vector provider can be added behind the existing `SimilarityProvider` protocol without touching any caller. `PRCRITIQ_SIMILARITY=embedding` is a valid configuration value that fails with a clear error rather than quietly running the lexical path instead.

## Quick Start

```powershell
uv sync --dev
uv run prcritiq health
uv run prcritiq review --repo pydantic/pydantic --pr 13680 --mode dry-run
uv run prcritiq review --repo pydantic/pydantic --pr 13680 --context --tools
uv run prcritiq review --repo pydantic/pydantic --pr 13680 --context --tools --review --markdown report.md
uv run prcritiq eval --fixture-mode          # measurement harness, mocked model
docker compose up -d   # local Postgres for --persist
uv run ruff check .
uv run pytest
```

Without `--review` the command reads the pull request and reports parsed diffs and guardrail decisions without calling a model. With `--review` it drafts and critiques findings using the routed provider, which requires an API key and spends credits. Nothing reaches GitHub unless you add `--post`, which is the only flag that writes. The report always names which stages actually ran, so an empty findings list is never mistaken for a clean bill of health.

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
