# Evidence run: django/django#21875 for the README image

One paid, model-assisted benchmark replay, run on 2026-09-24 to produce the review image in the README (`docs/media/review-django-21875.png`). It is a single case from the certified smoke set, not a passing benchmark result, and changes no default. The replay did not post comments to GitHub.

## Why a benchmark case

The two human-confirmed defects in this pull request were fixed before it merged, so `prcritiq review` on the live pull request would review the fixed head and could not show them. The benchmark harness reviews the frozen revision `baa5d52` that the human reviewer commented on, so the defects are present.

## Command and settings

```bash
uv run prcritiq eval --dataset eval/ground-truth-candidates/dataset-certified.jsonl --limit 1 --out <dir>
```

- Model: Anthropic `claude-opus-5`, low effort, selected by `PRCRITIQ_MODEL_POLICY=anthropic`.
- Diff-only, with no repository context, which is the protocol of the [certified smoke comparison](../2026-09-18-certified-smoke/comparison.md).
- Publish threshold 65, the smoke-comparison setting. The shipped default is 78.
- The command exits nonzero by design: one case cannot pass the corpus-size gate.

## Result

- 5 candidates: 2 marked publishable by the offline evaluator and 3 suppressed for low confidence. The report and image call the first group “published”; this is an evaluator state, not evidence of GitHub comments. No invalid lines and no findings without evidence.
- The mechanical matcher credits 1 of the 2 confirmed defects. It matched the published `admindocs/utils.py:31` finding to the label at line 34. That is a related claim on a nearby line, not a verified identical defect.
- The suppressed `serializer.py:196` finding, at 63% confidence, describes the same defect as the label at line 198: the changed "Could not find function … in None." error message. This is the agent's reading. The matcher scores published findings only.
- In the threshold sweep, 60 gives recall 1.00 with precision 0.67, and 78 gives a quiet run.
- Latency was 30.1 seconds, and the model cost $0.0921.

## Files

- `benchmark.json`: the raw report, unedited.
- `benchmark.md`: the report as PRCritiq rendered it.

The image renders the finding fields from `benchmark.json` unchanged. Only its headings, the paraphrased human labels, and the labelled agent assessment were written by hand.
