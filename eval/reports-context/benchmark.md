# PRCritiq benchmark

- Dataset: `dataset.jsonl` (20 pull requests, 46 labels)
- Reviewed: 20 of 20 (0 failed)
- Mode: live+context
- Model: `anthropic` / `claude-opus-5`
- Generated: 2026-08-31T13:39:05Z

## Gates

| Gate | Target | Actual | Result |
| --- | --- | --- | --- |
| corpus size | >= 20 reviewed cases | 20 of 20 | PASS |
| run completeness | 0 failed cases | 0 failed | PASS |
| issue recall | > 0.50 | 0.04 | FAIL |
| comment precision | >= 0.70 | 0.09 | FAIL |
| invalid-line rate | = 0.00 | 0.00 | PASS |
| no-evidence rate | = 0.00 | 0.00 | PASS |
| spam rate | <= 0.10 | 0.00 | PASS |

**Overall: FAIL**

## Metrics

| Metric | Value |
| --- | --- |
| Issue recall | 0.04 |
| Comment precision | 0.09 |
| Invalid-line rate | 0.00 |
| No-evidence rate | 0.00 |
| Spam rate | 0.00 |
| Findings published | 22 |
| Human labels matched | 2 of 46 |
| Quiet runs | 6 of 20 |
| Suppressed by reason | {'invalid_line': 1, 'low_confidence': 50} |
| Median latency | 36.95s |
| P95 latency | 131.67s |
| Cost per PR | $0.2988 |
| Total cost | $5.9759 |
| Tokens | 917440 in, 55546 out |

## Per case

| Case | Labels | Published | Matched | Suppressed | Latency | Cost |
| --- | --- | --- | --- | --- | --- | --- |
| pydantic/pydantic#13623 | 1 | 3 | 0 | 1 | 30.6s | $0.1424 |
| pydantic/pydantic#13717 | 1 | 0 | 0 | 2 | 29.1s | $0.4024 |
| encode/starlette#3489 | 2 | 2 | 0 | 2 | 30.1s | $0.1613 |
| encode/starlette#2767 | 4 | 0 | 0 | 2 | 15.6s | $0.1486 |
| encode/starlette#3472 | 1 | 1 | 0 | 2 | 49.1s | $0.2240 |
| psf/black#5321 | 1 | 0 | 0 | 2 | 37.6s | $0.3261 |
| pytest-dev/pytest#14824 | 1 | 2 | 0 | 3 | 45.5s | $0.4290 |
| pytest-dev/pytest#14921 | 2 | 2 | 0 | 2 | 36.3s | $0.1603 |
| pytest-dev/pytest#14284 | 1 | 1 | 0 | 2 | 21.2s | $0.3002 |
| HypothesisWorks/hypothesis#4812 | 2 | 2 | 0 | 4 | 55.2s | $0.2857 |
| HypothesisWorks/hypothesis#4860 | 1 | 1 | 0 | 1 | 24.4s | $0.1791 |
| HypothesisWorks/hypothesis#4861 | 2 | 1 | 0 | 3 | 39.8s | $0.2402 |
| HypothesisWorks/hypothesis#4842 | 6 | 1 | 0 | 4 | 131.7s | $0.7868 |
| HypothesisWorks/hypothesis#4806 | 5 | 0 | 0 | 5 | 73.8s | $0.5223 |
| Textualize/rich#4070 | 2 | 2 | 1 | 4 | 46.8s | $0.4522 |
| Textualize/rich#3180 | 5 | 1 | 0 | 2 | 27.0s | $0.3271 |
| scrapy/scrapy#2746 | 3 | 0 | 0 | 1 | 20.0s | $0.1651 |
| aio-libs/aiohttp#12748 | 1 | 1 | 0 | 3 | 45.2s | $0.2107 |
| aio-libs/aiohttp#12747 | 1 | 0 | 0 | 5 | 52.5s | $0.2867 |
| aio-libs/aiohttp#12674 | 4 | 2 | 1 | 1 | 32.8s | $0.2258 |

## Publish-threshold sweep

Scored from one set of model calls: suppression is post-processing over the
same drafted candidates, so the curve costs nothing extra.

| Min confidence | Recall | Precision | Findings | Quiet runs |
| --- | --- | --- | --- | --- |
| 50 | 0.22 | 0.12 | 67 | 1 |
| 55 | 0.22 | 0.13 | 63 | 1 |
| 60 | 0.11 | 0.12 | 51 | 1 |
| 65 | 0.04 | 0.09 | 22 | 6 |
| 70 | 0.04 | 0.17 | 12 | 10 |
| 78 | 0.02 | 0.33 | 3 | 18 |

## Limitations

- Labels are inline review comments filtered by documented heuristics, not hand-adjudicated by a human.
- Finding-to-label matching is mechanical: same file, a line within 5, and at least 18% shared distinctive vocabulary. The eval plan asks for manual adjudication of ambiguous matches, which this run does not perform.
- A human comment can be a question or a design discussion rather than a defect, so recall against these labels understates nothing but also proves less than recall against curated defects would.
