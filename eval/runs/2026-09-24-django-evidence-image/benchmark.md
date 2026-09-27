# PRCritiq benchmark

- Dataset: `dataset-certified.jsonl` (1 pull requests, 2 labels)
- Reviewed: 1 of 1 (0 failed)
- Mode: live
- Model: `anthropic` / `claude-opus-5`
- Generated: 2026-09-24T23:41:57Z

## Gates

| Gate | Target | Actual | Result |
| --- | --- | --- | --- |
| corpus size | >= 20 reviewed cases | 1 of 1 | FAIL |
| run completeness | 0 failed cases | 0 failed | PASS |
| dataset certification | all scored labels adjudicated and revision-valid | 2/2 confirmed, 0 invalid targets | PASS |
| issue recall | > 0.50 | 0.50 | FAIL |
| comment precision | >= 0.70 | 0.50 | FAIL |
| invalid-line rate | = 0.00 | 0.00 | PASS |
| no-evidence rate | = 0.00 | 0.00 | PASS |
| candidate-noise rate | <= 0.10 | 0.00 | PASS |

**Overall: FAIL**

## Metrics

| Metric | Value |
| --- | --- |
| Issue recall | 0.50 |
| Label-overlap precision (proxy) | 0.50 |
| Confirmed labels | 2 of 2 |
| Invalid label targets excluded | 0 |
| Invalid-line rate | 0.00 |
| No-evidence rate | 0.00 |
| Candidate-noise rate | 0.00 |
| Findings published | 2 |
| Human labels matched | 1 of 2 |
| Quiet runs | 0 of 1 |
| Suppressed by reason | {'low_confidence': 3} |
| Median latency | 30.09s |
| P95 latency | 30.09s |
| Cost per PR | $0.0921 |
| Total cost | $0.0921 |
| Tokens | 6769 in (0 cached read, 0 cache write), 2331 out (0 reasoning) |

## Per case

| Case | Labels | Published | Matched | Suppressed | Latency | Cost |
| --- | --- | --- | --- | --- | --- | --- |
| django/django#21875 | 2 | 2 | 1 | 3 | 30.1s | $0.0921 |

## Publish-threshold sweep

Scored from one set of model calls: suppression is post-processing over the
same drafted candidates, so the curve costs nothing extra.

| Min confidence | Recall | Precision | Findings | Quiet runs |
| --- | --- | --- | --- | --- |
| 50 | 1.00 | 0.40 | 5 | 0 |
| 55 | 1.00 | 0.50 | 4 | 0 |
| 60 | 1.00 | 0.67 | 3 | 0 |
| 65 | 0.50 | 0.50 | 2 | 0 |
| 70 | 0.00 | 0.00 | 1 | 0 |
| 78 | 0.00 | 0.00 | 0 | 1 |

## Limitations

- A label remains provisional until a named human records a final verdict and explicit approval; provisional labels force the dataset-certification gate to fail.
- Finding-to-label matching is mechanical: same file, a line within 5, and at least 18% shared distinctive vocabulary. The eval plan asks for manual adjudication of ambiguous matches, which this run does not perform.
- Label-overlap precision is a matching proxy, not adjudicated correctness. The report retains full findings so a human can assess unmatched output.
