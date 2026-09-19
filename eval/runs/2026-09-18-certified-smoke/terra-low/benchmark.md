# PRCritiq benchmark

- Dataset: `dataset-certified.jsonl` (3 pull requests, 5 labels)
- Reviewed: 3 of 3 (0 failed)
- Mode: live
- Model: `openai` / `gpt-5.6-terra`
- Generated: 2026-09-18T17:38:01Z

## Gates

| Gate | Target | Actual | Result |
| --- | --- | --- | --- |
| corpus size | >= 20 reviewed cases | 3 of 3 | FAIL |
| run completeness | 0 failed cases | 0 failed | PASS |
| dataset certification | all scored labels adjudicated and revision-valid | 5/5 confirmed, 0 invalid targets | PASS |
| issue recall | > 0.50 | 0.20 | FAIL |
| comment precision | >= 0.70 | 0.20 | FAIL |
| invalid-line rate | = 0.00 | 0.00 | PASS |
| no-evidence rate | = 0.00 | 0.00 | PASS |
| candidate-noise rate | <= 0.10 | 0.00 | PASS |

**Overall: FAIL**

## Metrics

| Metric | Value |
| --- | --- |
| Issue recall | 0.20 |
| Label-overlap precision (proxy) | 0.20 |
| Confirmed labels | 5 of 5 |
| Invalid label targets excluded | 0 |
| Invalid-line rate | 0.00 |
| No-evidence rate | 0.00 |
| Candidate-noise rate | 0.00 |
| Findings published | 5 |
| Human labels matched | 1 of 5 |
| Quiet runs | 1 of 3 |
| Suppressed by reason | none |
| Median latency | 28.19s |
| P95 latency | 31.97s |
| Cost per PR | $0.0181 |
| Total cost | $0.0542 |
| Tokens | 6931 in (2146 cached), 3687 out (2702 reasoning) |

## Per case

| Case | Labels | Published | Matched | Suppressed | Latency | Cost |
| --- | --- | --- | --- | --- | --- | --- |
| django/django#21875 | 2 | 4 | 1 | 0 | 32.0s | $0.0277 |
| ansible/ansible#87412 | 1 | 0 | 0 | 0 | 11.0s | $0.0070 |
| psf/black#5068 | 2 | 1 | 0 | 0 | 28.2s | $0.0195 |

## Publish-threshold sweep

Scored from one set of model calls: suppression is post-processing over the
same drafted candidates, so the curve costs nothing extra.

| Min confidence | Recall | Precision | Findings | Quiet runs |
| --- | --- | --- | --- | --- |
| 50 | 0.20 | 0.20 | 5 | 1 |
| 55 | 0.20 | 0.20 | 5 | 1 |
| 60 | 0.20 | 0.20 | 5 | 1 |
| 65 | 0.20 | 0.20 | 5 | 1 |
| 70 | 0.20 | 0.20 | 5 | 1 |
| 78 | 0.20 | 0.25 | 4 | 1 |

## Limitations

- A label remains provisional until a named human records a final verdict and explicit approval; provisional labels force the dataset-certification gate to fail.
- Finding-to-label matching is mechanical: same file, a line within 5, and at least 18% shared distinctive vocabulary. The eval plan asks for manual adjudication of ambiguous matches, which this run does not perform.
- Label-overlap precision is a matching proxy, not adjudicated correctness. The report retains full findings so a human can assess unmatched output.
