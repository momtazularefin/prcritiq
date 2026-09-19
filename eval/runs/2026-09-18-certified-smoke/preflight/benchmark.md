# PRCritiq benchmark

- Dataset: `dataset-certified.jsonl` (3 pull requests, 5 labels)
- Reviewed: 3 of 3 (0 failed)
- Mode: fixture
- Model: `mock` / `mock-reviewer`
- Generated: 2026-09-18T17:34:45Z

## Gates

| Gate | Target | Actual | Result |
| --- | --- | --- | --- |
| corpus size | >= 20 reviewed cases | 3 of 3 | FAIL |
| run completeness | 0 failed cases | 0 failed | PASS |
| dataset certification | all scored labels adjudicated and revision-valid | 5/5 confirmed, 0 invalid targets | PASS |
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
| Confirmed labels | 5 of 5 |
| Invalid label targets excluded | 0 |
| Invalid-line rate | 0.00 |
| No-evidence rate | 0.00 |
| Candidate-noise rate | 0.00 |
| Findings published | 0 |
| Human labels matched | 0 of 5 |
| Quiet runs | 3 of 3 |
| Suppressed by reason | none |
| Median latency | 0.02s |
| P95 latency | 0.03s |
| Cost per PR | $0.0000 |
| Total cost | $0.0000 |
| Tokens | 0 in (0 cached), 0 out (0 reasoning) |

## Per case

| Case | Labels | Published | Matched | Suppressed | Latency | Cost |
| --- | --- | --- | --- | --- | --- | --- |
| django/django#21875 | 2 | 0 | 0 | 0 | 0.0s | $0.0000 |
| ansible/ansible#87412 | 1 | 0 | 0 | 0 | 0.0s | $0.0000 |
| psf/black#5068 | 2 | 0 | 0 | 0 | 0.0s | $0.0000 |

## Publish-threshold sweep

Scored from one set of model calls: suppression is post-processing over the
same drafted candidates, so the curve costs nothing extra.

| Min confidence | Recall | Precision | Findings | Quiet runs |
| --- | --- | --- | --- | --- |
| 50 | 0.00 | 0.00 | 0 | 3 |
| 55 | 0.00 | 0.00 | 0 | 3 |
| 60 | 0.00 | 0.00 | 0 | 3 |
| 65 | 0.00 | 0.00 | 0 | 3 |
| 70 | 0.00 | 0.00 | 0 | 3 |
| 78 | 0.00 | 0.00 | 0 | 3 |

## Limitations

- A label remains provisional until a named human records a final verdict and explicit approval; provisional labels force the dataset-certification gate to fail.
- Finding-to-label matching is mechanical: same file, a line within 5, and at least 18% shared distinctive vocabulary. The eval plan asks for manual adjudication of ambiguous matches, which this run does not perform.
- Label-overlap precision is a matching proxy, not adjudicated correctness. The report retains full findings so a human can assess unmatched output.
