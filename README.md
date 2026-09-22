# PRCritiq

[![CI](https://github.com/momtazularefin/prcritiq/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/momtazularefin/prcritiq/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

PRCritiq is an evidence-backed pull request review agent. It reviews GitHub PRs with diff-aware context, safe tool evidence, a LangGraph review loop, and measured benchmark results, and it prefers silence over weak comments.

## Current Status

v0.1.0 is a bounded portfolio release. The review system, a GitHub App webhook that records dry-run review runs, and a container deployment for one small Hetzner node are implemented. The benchmark evidence is a certified smoke set, not the full corpus the original plan called for, so the quality targets are stated as unmet rather than claimed. The [acceptance criteria table](#acceptance-criteria) shows where each criterion stands.

M9 made the service deployable:

- GitHub App webhook execution: a signed `pull_request` event becomes one persisted review run under a database-enforced idempotency key, executed in the background and never posted. A redelivery returns the same run without executing it again. `GET /runs/{id}` reports its state.
- GitHub App authentication through a short-lived installation token minted from a signed app JWT, with no fallback to another credential when minting fails.
- Public-surface safety: private repositories are refused by default on the demo and the webhook, the demo is rate limited per client address and can be switched off, and model review on webhook runs is off unless the owner turns it on.
- A two-stage container image running as non-root with the locked `ruff`, and a Docker Compose stack with Caddy TLS for `prcritiq.arefin.app`. The stack was rehearsed locally end to end: signed delivery through Caddy, background execution, redelivery, bad signature, and rate limit. See [deployment](docs/deployment.md).

Earlier milestones, M1 intake through M8 benchmark:

- Unified-diff parser mapping every patch to hunks, changed new-line numbers, and removed old-line numbers. It is strict on purpose: a patch whose hunk header disagrees with its body is rejected rather than parsed into line numbers that would be silently wrong.
- Changed-line validation, so an inline comment can only ever target a line this pull request actually added. Rejected targets are returned with a reason rather than dropped, so a run can report its invalid-line rate.
- Guardrail gate with structured decisions for generated, lockfile, vendored, binary, oversized-file, oversized-diff, unsupported-language, and unsafe-path files, plus a per-pull-request reviewable-file budget. Every skip carries an explicit reason.
- Context retrieval: `prcritiq review --context` downloads the repository at the head commit, chunks it by real symbol boundaries, and retrieves related code by imports, nearby tests, directory siblings, and identifier-aware BM25 ranking. Every retrieved chunk carries a chunk id and the reason it was chosen, so a later finding can cite it.
- Bounded and safe: extraction rejects traversal and link members, is capped by file count and total bytes, and writes only inside a temporary workspace that is deleted afterwards. Context has an explicit budget, with a reserved share for the changed code so a large diff cannot starve out related context.
- Static analysis: `prcritiq review --tools` runs allowlisted checks over the changed files and reports structured diagnostics, flagging which land on lines the pull request actually changed. Commands come from a fixed allowlist, never from the repository; no shell is used; the executable must resolve outside the workspace so a repository cannot supply its own binary; the child process gets a scrubbed environment so no token reaches it; and runs are bounded by a timeout and an output cap.
- Every tool is reported even when it does not run, with the reason. `eslint` is refused on repositories that configure it, because eslint loads the repository's own config as JavaScript and would execute the pull request's code. `tsc` is refused because the snapshot excludes `node_modules`, so it would report a missing module for every dependency rather than real findings.
- LangGraph review loop: `prcritiq review --review` runs the seven-node graph from the design, `fetch_diff -> guardrail_gate -> static_analysis -> retrieve_context -> reason_and_draft -> self_critique -> post_or_summarize`. Each node is an inspectable step, not a line in a prompt.
- Structured findings carry severity, model confidence, category, evidence, a suggested fix, and source references when retrieved or tool evidence was used.
- Deterministic critique validates target lines and structural evidence, rejects unknown references, generic filler and exact duplicates, and applies the publication threshold. The production review graph still has no semantic model check.
- Experimental semantic replay: `prcritiq verify` preserves saved published and suppressed candidates, gathers bounded base/head function context, and prepares an auditable report without model calls. Explicit `--live` enables a separate structured Sol/Terra verifier. It never posts or changes review defaults; [workflow and limitations](docs/verification.md).
- Deterministic model routing has no silent fallback. OpenAI uses typed Responses API output with explicit reasoning effort; benchmark cases record the actual provider, model, effort, route reason, usage, latency, and cost.
- Prompt-injection defense: diffs, retrieved code, and tool output are fenced as labelled untrusted data, the system prompt states they cannot change the instructions, and every finding is validated against the diff afterwards regardless of what the model was told.
- Postgres run store: `prcritiq review --persist` records the run, its per-file guardrail verdicts, tool outcomes, findings, and routing decision. Suppressed findings are stored alongside published ones, because the invalid-line and no-evidence rates a benchmark reports are computed from them.
- Idempotency enforced by the database, not by an application check: the run key is `UNIQUE`, so a redelivered webhook or a repeated command resolves to the existing run instead of writing a second one. Two workers racing cannot both win.
- A run state machine with no way back: `pending -> running -> summarized -> posted`, with `failed` and `skipped` as terminal outcomes. A finished run cannot be reopened and rewritten.
- Optional LangSmith trace linkage. Tracing is off unless it is both selected and credentialed, and a run never records a fabricated trace id, because an identifier nobody can resolve is worse than none.
- GitHub posting behind three gates: `prcritiq review --post` publishes only findings that survived self-critique, re-validates every target line immediately before the write because GitHub accepts comments on a wider set of lines than a pull request actually added, and refuses to post a body whose hash is already recorded for that run. Posting requires `--review`, `GITHUB_TOKEN`, and a database, and fails loudly without them.
- A quiet run stays quiet. When nothing clears the bar, nothing is posted and the outcome is recorded in run history instead, because a comment announcing that there is nothing to say is still a comment.
- Markdown reports: `--markdown PATH` renders the run for a human reader, listing findings in full, suppression counts with the invalid-line rate, files not reviewed with reasons, and tool outcomes. Untrusted pull request text is escaped so it cannot break out of a table cell.
- Benchmark v2 foundations: provenance-aware label candidates, exact-revision and added-line eligibility, human-adjudication status, one-to-one matching, full output retention, honest failed-case denominators, and a dataset-certification gate.
- `prcritiq eval` supports explicit provider/model/effort overrides for bake-offs and writes JSON plus Markdown reports. Legacy labels can produce diagnostic overlap metrics but cannot produce a passing certified report.
- The invalid-line rate is verified against each pull request's real changed lines rather than assumed from the fact that self-critique ran.
- Working dry-run review: `prcritiq review` fetches a real pull request, parses every patch, applies the guardrail gate, and prints a JSON report of what it would and would not review. No model is called and nothing is posted.
- FastAPI app with `GET /health`, `POST /demo/review` running that same dry run, signed `POST /webhooks/github`, and `GET /runs/{id}`.
- Repository references accept the HTTPS and SSH clone forms, a pasted pull-request URL, and the `owner/name` shorthand.
- GitHub webhook signature verification, pull-request idempotency keys, and a thin GitHub REST client boundary for PR metadata and changed files.
- Configuration surface with strict `ACCELERATION=none|gpu|npu` validation.
- Ruff, pytest, and GitHub Actions CI.

Not implemented, and deferred beyond v0.1.0:

- A statistically sufficient human-adjudicated benchmark corpus and a passing benchmark result; the approved smoke set has only 3 PRs / 5 defects.
- High-recall/per-hunk generation and production integration of candidate-local context plus semantic verification; the separate replay tool has completed its first frozen-candidate comparison but remains experimental.
- Posting from webhook runs. The deployed service is dry-run only, and its GitHub App has read-only pull request access.
- Reclaiming a webhook run interrupted by a restart, distributed workers, and crash-safe exactly-once posting.

## Acceptance Criteria

| Criterion | Status | Evidence |
| --- | --- | --- |
| AC1 GitHub App mode with idempotent runs | Met in code; live evidence pending | Signed events create one run per delivery under a `UNIQUE` key, and a redelivery is not executed again ([API tests](tests/test_api.py), [intake](src/prcritiq/intake.py)). A live delivery is recorded once the app is installed. |
| AC2 Dry-run demo with Markdown/JSON report | Met | `prcritiq review --markdown`, `POST /demo/review`. |
| AC3 Diff parser and guardrails | Met | Strict parser, reasoned skips, regression tests. |
| AC4 Retrieval | Met | Identifier-aware BM25 plus structural signals (ADR-016), regression-tested. |
| AC5 Static checks | Met | Allowlisted `ruff`; unsafe `eslint` and `tsc` runs are declined with reasons. |
| AC6 LangGraph with self-critique | Met | Seven-node graph; each node reports that it ran. |
| AC7 Finding quality fields | Met | The schema requires severity, confidence percentage, evidence, and a suggested fix. |
| AC8 Line-valid comments | Met | Invalid-line findings are suppressed, and the rate is verified against real changed lines. |
| AC9 Quiet behavior | Met | Quiet runs are recorded without comments. |
| AC10 Persistence and observability | Met | Postgres run store; optional LangSmith trace linkage. |
| AC11 20-PR certified benchmark | Not met, deferred | The legacy 20-PR corpus is diagnostic, not certified; the certified smoke set is 3 PRs / 5 defects. |
| AC12 Recall above 50% with precision gates | Not met, deferred | Smoke-set evidence cannot establish it, and no claim is made. |
| AC13 Public docs match behavior | Met | This README and `docs/`. |
| AC14 Public deployment | Met in code; live evidence pending | Hetzner, Caddy, and Postgres on one node (ADR-019, the approved equivalent to Modal). Live once the owner provisions the server, DNS, and secrets. |
| AC15 CI and repository hygiene | Partly met | CI job `test` passes on `main`; license and badges are present. Branch protection and the `v0.1.0` tag are owner actions. |

The [portfolio release checklist](docs/portfolio-release.md) records the release scope and the accepted deferrals.

### Benchmark status: certified smoke set, no production winner

The original 20-PR corpus contains 46 mechanically harvested inline comments. Independent review found author replies, bots, preferences and discussions, plus labels attached to earlier revisions that the final fixtures cannot reproduce. Those reports therefore measure mechanical agreement with a noisy comment set—not defect detection—and cannot select a model honestly.

The historical diff-only Opus 5 run cost $2.06 with a 27-second median; broad repository context cost $5.98 with a 37-second median and produced worse overlap scores. These are retained as diagnostic evidence that the current global retrieval strategy wastes context, not as product-quality metrics.

New reports exclude unreachable label targets, require explicit human adjudication for certification, use one-to-one matching, and retain every finding for manual review.

The approved four-way smoke comparison completed all 12 reviews for an estimated $0.372–$0.381. Sol-low is a promising lower-cost quality baseline and Terra-low the budget challenger, but three PRs cannot select a production winner. An AI evidence audit found both false matches and missed correct findings in the automatic overlap scores. See the [comparison and limitations](eval/runs/2026-09-18-certified-smoke/comparison.md) and [evaluation documentation](docs/evaluation.md).

The subsequent [frozen-candidate verifier comparison](eval/runs/2026-09-20-verifier-comparison/comparison.md) completed 48 calls for an estimated $0.704518 in verification-only cost. Each verifier retained all 27 candidates, assessed 24, and abstained on 3 with unavailable context. Sol-low was the safer experimental baseline; Terra-low was cheaper and faster but confirmed a false critical claim despite an explicit `break`. Both overconfirmed an unproven Ansible contract claim. Partial allegations remain partial even when a verifier supplies a correct narrower example. No production promotion, routing, or threshold change followed.

## Quick Start

```powershell
uv sync --dev
uv run prcritiq health
uv run prcritiq review --repo pydantic/pydantic --pr 13680 --mode dry-run
uv run prcritiq review --repo pydantic/pydantic --pr 13680 --context --tools
uv run prcritiq review --repo pydantic/pydantic --pr 13680 --context --tools --review --markdown report.md
uv run prcritiq eval --fixture-mode          # measurement harness, mocked model
uv run python eval/adjudicate_dataset.py --export eval/adjudications.jsonl
uv run prcritiq eval --dataset eval/ground-truth-candidates/dataset-certified.jsonl --provider openai --model gpt-5.6-terra --effort low
docker compose up -d   # local Postgres for --persist
uv run ruff check .
uv run pytest
```

Without `--review` the command reads the pull request and reports parsed diffs and guardrail decisions without calling a model. With `--review` it drafts and critiques findings using the routed provider, which requires an API key and spends credits. Nothing reaches GitHub unless you add `--post`, which is the only flag that writes. The report always names which stages actually ran, so an empty findings list is never mistaken for a clean bill of health.

Public repositories work without a token. Set `GITHUB_TOKEN` to raise the API rate limit or to reach a private repository.

## Deployment

The public demo runs on one Hetzner node with Docker Compose: Caddy for TLS, the app, and Postgres. The owner creates the server, adds one DNS record, registers the GitHub App, and fills in the server's `.env`. One command then ships a committed revision:

```bash
PRCRITIQ_HOST=<server IPv4> bash deploy/hetzner/deploy.sh
```

[docs/deployment.md](docs/deployment.md) covers setup, verification, the public surface, operations, and teardown.

## Project Shape

```text
src/prcritiq/       Python package
tests/              unit, integration, and smoke tests
docs/               public project docs
eval/               benchmark fixtures, datasets, and retained evidence runs
deploy/hetzner/     Compose stack, Caddy, cloud-init, and deploy script
Dockerfile          service image
.github/workflows/  CI workflow
```

## Design Direction

The target reviewer flow is:

```text
fetch_diff -> guardrail_gate -> static_analysis -> retrieve_context -> reason_and_draft -> self_critique -> post_or_summarize
```

PRCritiq prefers silence over weak comments. Every reportable finding must include severity, confidence, evidence, and a suggested fix, and inline comments must target valid changed PR lines.

## Documentation

- [Getting started](docs/getting-started.md) - local setup and current commands.
- [Architecture](docs/architecture.md) - components and what is built today.
- [Configuration](docs/configuration.md) - environment variables.
- [Security](docs/security.md) - security posture and current limits.
- [Deployment](docs/deployment.md) - the Hetzner deployment, public surface, and teardown.
- [Evaluation](docs/evaluation.md) - benchmark plan and current evidence status.
- [Verification](docs/verification.md) - the experimental semantic verifier replay.
- [Portfolio release](docs/portfolio-release.md) - release scope, evidence, and deferrals.

## License

MIT.
