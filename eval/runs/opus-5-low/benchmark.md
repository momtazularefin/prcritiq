# PRCritiq benchmark

- Dataset: `dataset-certified.jsonl` (1 pull requests, 1 labels)
- Reviewed: 1 of 1 (0 failed)
- Mode: live
- Model: `anthropic` / `claude-opus-5`
- Generated: 2026-09-01T22:52:51Z

## Gates

| Gate | Target | Actual | Result |
| --- | --- | --- | --- |
| corpus size | >= 20 reviewed cases | 1 of 1 | FAIL |
| run completeness | 0 failed cases | 0 failed | PASS |
| dataset certification | all scored labels adjudicated and revision-valid | 1/1 confirmed, 0 invalid targets | PASS |
| issue recall | > 0.50 | 0.00 | FAIL |
| comment precision | >= 0.70 | 0.00 | FAIL |
| invalid-line rate | = 0.00 | 0.00 | PASS |
| no-evidence rate | = 0.00 | 0.00 | PASS |
| candidate-noise rate | <= 0.10 | 0.00 | PASS |

**Overall: FAIL**

## Metrics

| Metric | Value |
| --- | --- |
| Issue recall | 0.00 |
| Label-overlap precision (proxy) | 0.00 |
| Confirmed labels | 1 of 1 |
| Invalid label targets excluded | 0 |
| Invalid-line rate | 0.00 |
| No-evidence rate | 0.00 |
| Candidate-noise rate | 0.00 |
| Findings published | 0 |
| Human labels matched | 0 of 1 |
| Quiet runs | 1 of 1 |
| Suppressed by reason | {'low_confidence': 3} |
| Median latency | 25.05s |
| P95 latency | 25.05s |
| Cost per PR | $0.0632 |
| Total cost | $0.0632 |
| Tokens | 4417 in (0 cached), 1644 out (0 reasoning) |

## Per case

| Case | Labels | Published | Matched | Suppressed | Latency | Cost |
| --- | --- | --- | --- | --- | --- | --- |
| HypothesisWorks/hypothesis#4861 | 1 | 0 | 0 | 3 | 25.0s | $0.0632 |

## Publish-threshold sweep

Scored from one set of model calls: suppression is post-processing over the
same drafted candidates, so the curve costs nothing extra.

| Min confidence | Recall | Precision | Findings | Quiet runs |
| --- | --- | --- | --- | --- |
| 50 | 0.00 | 0.00 | 3 | 0 |
| 55 | 0.00 | 0.00 | 3 | 0 |
| 60 | 0.00 | 0.00 | 1 | 0 |
| 65 | 0.00 | 0.00 | 0 | 1 |
| 70 | 0.00 | 0.00 | 0 | 1 |
| 78 | 0.00 | 0.00 | 0 | 1 |

## Limitations

- Legacy or newly harvested labels remain unreviewed until a human marks them confirmed_defect; unreviewed labels force the dataset-certification gate to fail.
- Finding-to-label matching is mechanical: same file, a line within 5, and at least 18% shared distinctive vocabulary. The eval plan asks for manual adjudication of ambiguous matches, which this run does not perform.
- Label-overlap precision is a matching proxy, not adjudicated correctness. The report retains full findings so a human can assess unmatched output.
