# Getting Started

## Status

The local review pipeline is implemented through diff intake, optional repository context and static analysis, model drafting, deterministic critique, persistence, and opt-in posting. A separate experimental verifier replays saved candidates. The certified smoke dataset contains 3 PRs and 5 approved defects; it is not a sufficient product-quality benchmark. See the [portfolio release checklist](portfolio-release.md) for implemented capabilities and remaining release decisions.

## Requirements

- Python 3.12.
- uv.

## Install

Run commands from the project root. Installing dependencies can require network access; no API keys or database are needed for the offline examples below.

```powershell
uv sync --dev
uv run prcritiq health
```

## Offline Demo: No Keys Or Model Calls

After installation, this demo reads only local fixtures and uses a deterministic mock provider. It makes no GitHub or model requests and does not post comments:

```powershell
uv run prcritiq eval --dataset eval/ground-truth-candidates/dataset-certified.jsonl --fixture-mode --out eval/reports/portfolio-demo
```

Inspect `eval/reports/portfolio-demo/benchmark.md` and `benchmark.json`. The default mock returns no findings, so recall gates fail and the command exits with status 1 after writing its reports. That is expected for this harness demonstration, not an installation failure. Mock scores are not measured model accuracy. Reusing the same output path replaces those generated reports.

For actual retained model outputs and their limitations, read the [four-way drafting smoke](../eval/runs/2026-09-18-certified-smoke/comparison.md) and [frozen verifier comparison](../eval/runs/2026-09-20-verifier-comparison/comparison.md). Neither establishes production precision or recall.

### Optional Offline Verification Preparation

Reuse the retained source bundle without fetching GitHub context or calling a verifier:

```powershell
uv run prcritiq verify --dataset eval/ground-truth-candidates/dataset-certified.jsonl --report eval/runs/2026-09-18-certified-smoke/opus-5-low/benchmark.json --source-bundle eval/runs/2026-09-20-verifier-comparison/sources.json --out eval/reports/portfolio-verification
```

The output directory must not already exist. The command writes `sources.json` and `verification.json`, preserving every candidate and recording available context with zero model calls. Missing context remains explicit and produces a nonzero exit status; preparation is not a semantic verdict or evidence of quality improvement. Do not add `--live` for this offline demonstration. See [verification](verification.md) for the complete workflow.

## Review A Public PR: Network, No Model Spend

```powershell
uv run prcritiq review --repo pydantic/pydantic --pr 13680 --mode dry-run --markdown reports/dry-run.md
```

Create the `reports` directory first if using that Markdown path. This command fetches PR metadata and diffs from GitHub and reports diff/guardrail decisions; it does not draft model findings or post comments. Public repositories work anonymously subject to GitHub rate limits. Here, **dry-run means no posting, not no network**.

To draft findings, the following optional command **spends model API credit** and requires the configured provider key. `--context` and `--tools` also download a bounded source archive:

```powershell
uv run prcritiq review --repo pydantic/pydantic --pr 13680 --context --tools --review
```

Posting is separately opt-in with `--post`, which requires `--review`, `GITHUB_TOKEN`, and `DATABASE_URL`. See [configuration](configuration.md) and [security](security.md) before supplying credentials or publishing findings.

## Local API Demo

Start the API on the loopback interface:

```powershell
uv run uvicorn prcritiq.api:app --host 127.0.0.1 --port 8000
```

In another PowerShell terminal:

```powershell
Invoke-RestMethod -Uri http://127.0.0.1:8000/health
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/demo/review -ContentType application/json -Body '{"repo":"https://github.com/pydantic/pydantic","pr":13680,"mode":"dry-run"}'
```

The demo request reads GitHub data but calls no model, uses no database, and posts nothing. It refuses private repositories and allows 6 requests a minute per client by default. Interactive API documentation is available at `http://127.0.0.1:8000/docs`.

The signed `/webhooks/github` endpoint needs `GITHUB_WEBHOOK_SECRET` and `DATABASE_URL`. A supported `pull_request` event becomes one recorded dry-run review run, executed in the background and never posted; `GET /runs/{id}` reports its state. A redelivery of the same event returns the same run without executing it again.

This is a local example. The public deployment, with its GitHub App, TLS, and abuse controls, is described in [deployment](deployment.md).

## Local Postgres

`--persist` needs a database. The bundled compose file provides one on port 5433 to avoid the usual Postgres port 5432; port 5433 must still be available:

```powershell
docker compose up -d
$env:DATABASE_URL = "postgresql://prcritiq:prcritiq@localhost:5433/prcritiq"
uv run prcritiq review --repo pydantic/pydantic --pr 13680 --persist
```

Migrations run automatically on connect and are idempotent. The command above fetches GitHub data but does not call a model or post comments unless the corresponding flags are supplied.

## Validation

```powershell
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

Database-dependent tests need `DATABASE_URL` and a running Postgres; otherwise they are skipped. CI supplies Postgres and mocks model calls. These are reproduction instructions, not a claim that the current checkout has passed or that remote CI is green. Release evidence must be recorded for the final revision.
