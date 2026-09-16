# Evaluation

## Status

The evaluation harness is implemented, but the shipped 20-PR corpus is a legacy diagnostic dataset, not certified ground truth. Its 46 labels were harvested mechanically from inline comments. An audit found PR-author replies, automated comments, design discussion, and comments attached to revisions that the final fixture does not preserve. The old live results remain useful as historical diagnostics; they must not be used to choose a production model or claim product recall and precision.

Benchmark v2 enforces the missing foundations:

- primary comments only—thread replies are excluded;
- PR-author and GitHub Bot comments are excluded;
- a comment must target the exact frozen head revision and an added line in that fixture;
- every retained label carries reviewer, comment id, and review-commit provenance;
- harvested labels start as `unreviewed` and cannot pass the dataset-certification gate until a human marks them `confirmed_defect`;
- errored cases do not contribute labels to quality denominators;
- finding-to-label matching is maximum-cardinality and one-to-one;
- JSON reports retain complete published and suppressed findings for independent adjudication;
- every case records its actual provider, model, effort, route reason, token use, latency, and estimated cost.

Mechanical matching remains only a triage aid: same file, a line within five, and at least 18 percent shared distinctive vocabulary. The reported “label-overlap precision” is a proxy, not adjudicated correctness. Full finding retention now makes a proper manual pass possible.

Export the stable, provenance-rich adjudication queue, read the primary comment and any captured thread replies, edit each verdict to `confirmed_defect` or `excluded`, then build a candidate certified dataset:

```powershell
uv run python eval/adjudicate_dataset.py --export eval/adjudications.jsonl
uv run python eval/adjudicate_dataset.py --apply eval/adjudications.jsonl --confirmed-only --output eval/dataset-certified.jsonl
uv run prcritiq eval --fixture-mode --dataset eval/dataset-certified.jsonl
```

The exported queue identifies the PR author and carries direct replies from each frozen review thread. Replies matter because a suggestion may be corrected, narrowed, or rejected after the primary comment. The apply step fails on duplicate, missing, unknown, still-unreviewed, or unexplained decisions. Before a live evaluation, PRCritiq verifies the selected cases against their frozen fixtures: every scored label must be human-confirmed, carry v2 reviewer and source-comment provenance, target the exact head revision, and land on an added line. If any check fails, the CLI exits before constructing or calling a provider. Fixture mode remains available for validating an uncertified candidate corpus offline.

## Legacy Diagnostic Runs

Claude Opus 5 at low effort completed the 20-PR diff-only run for $2.06, with median latency 27 seconds. Mechanical label-overlap recall peaked at 0.26 with precision 0.15. At the former threshold of 78, recall was 0.04 and precision 0.50.

Adding broad repository context increased cost to $5.98 and median latency to 37 seconds while peak overlap recall fell to 0.22. Retrieved imports consumed most of the context budget. This supports keeping global context off, but it does not establish that targeted context is harmful.

These values are not benchmark claims because the dataset-certification gate fails.

## Model Bake-off

The first certified-preflight diagnostic ran on 2026-09-02 across Opus 5 low,
Terra low/medium, and Sol low. All four live provider calls completed on the
same one-case set for an estimated $0.1374 total, but none matched the retained
label. No model was selected.

That run exposed missing adjudication context: the sole confirmed comment has a
direct author reply disputing its premise, and the final Python sentinel
specification supports the reply. Ten of the 18 candidates have captured thread
replies which the original decision queue did not show. The queue now includes
those replies and author attribution. See the
[one-case bake-off diagnostic](../eval/runs/model-bakeoff.md) for exact usage,
latency, findings, and sources.

Revisit the contextual adjudications before any paid expansion. Once a
multi-case certified smoke set exists, repeat the same matrix on exactly that
set:

```powershell
uv run prcritiq eval --dataset eval/candidates/dataset-certified.jsonl --provider anthropic --model claude-opus-5 --effort low --out eval/runs/opus-5-low
uv run prcritiq eval --dataset eval/candidates/dataset-certified.jsonl --provider openai --model gpt-5.6-terra --effort low --out eval/runs/terra-low
uv run prcritiq eval --dataset eval/candidates/dataset-certified.jsonl --provider openai --model gpt-5.6-terra --effort medium --out eval/runs/terra-medium
uv run prcritiq eval --dataset eval/candidates/dataset-certified.jsonl --provider openai --model gpt-5.6-sol --effort low --out eval/runs/sol-low
```

GPT-5.6 Luna should be evaluated as a high-recall candidate generator after generation and verification are split into separate stages. Comparing it as the sole reviewer would test a different, weaker architecture than the intended production cascade.

Choose the lowest-cost configuration among those that meet adjudicated precision, recall, invalid-line, and evidence requirements. Latency and cost are selection criteria only after quality passes.

## Validation Evidence

- All model calls are mocked in CI.
- Provider tests verify missing-key failures and no silent fallback.
- OpenAI tests verify typed Responses API input, explicit reasoning effort, structured parsing, cached-token accounting, and GPT-5.6 prices.
- Benchmark tests cover one-to-one matching, invalid label exclusion, full output retention, threshold sweeps, and genuine billing fail-fast behavior.
- Posting tests never write to a real pull request.
- Postgres tests use a real database when `DATABASE_URL` is present and skip explicitly otherwise.
- Ruff and the complete pytest suite run in CI.

## Certification Requirements

A publishable benchmark needs at least 20 completed code-heavy PR cases, all labels adjudicated as real defects, exact revision-valid targets, and no failed cases. Final reporting should include:

- candidate recall before suppression;
- published issue recall;
- human-adjudicated precision;
- invalid-line and no-evidence rates;
- candidate-noise and quiet-run behavior;
- median and P95 latency;
- input, cached-input, reasoning-output, and total-output tokens;
- total cost and cost per PR.
