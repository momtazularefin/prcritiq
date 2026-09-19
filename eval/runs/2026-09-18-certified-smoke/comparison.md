# Certified smoke comparison — 2026-09-18/19

## Outcome

All 12 case reviews completed without provider errors. Recorded-usage cost is
$0.3717 before the previously unrecorded OpenAI cache-write premium; the maximum
correction from that missing counter is $0.0096, yielding $0.3717–$0.3813.
This is an estimate, not a reconciled billing statement.

Sol-low is the most promising lower-cost **quality baseline for the next
experiment**, with Terra-low the budget challenger. No production model is
selected. Opus was fastest here and drafted more approved issues, but also
published a false critical finding at confidence 95. Terra-medium cost more and
took longer than Terra-low without detecting an approved defect precisely.

## Comparable runs

Every arm reviewed the same three immutable PR snapshots and five
project-owner-approved defects once. Context and tools were off; the prompt,
schema, runtime, 64,000 output-token cap, and threshold 65 stayed unchanged.
Arms and cases ran sequentially in the order below. A usage-limit interruption
separated the first two arms from the last two by roughly five hours; this was
not an interleaved or repeated latency experiment. Report timestamps are UTC.

| Configuration | Cost for 3 PRs* | Median latency | Automatic label matches | AI-audited full label detections, published | Published / drafted |
|---|---:|---:|---:|---:|---:|
| opus-5-low | $0.1799 | 17.30s | 4/5 | 2/5 + 1 partial | 6 / 12 |
| terra-low | $0.0542–$0.0566 | 28.19s | 1/5 | 1/5 | 5 / 5 |
| terra-medium | $0.0706–$0.0730 | 40.27s | 1/5 | 0/5 | 5 / 5 |
| sol-low | $0.0670–$0.0718 | 25.55s | 1/5 | 2/5 | 5 / 5 |

*The OpenAI intervals bound only the unrecorded cache-write premium. Anthropic
uses recorded input/output at standard rates; separate Anthropic reasoning/cache
counters were not collected. No arm explicitly requested Anthropic caching.
Retries, taxes, account adjustments, and billing-ledger reconciliation are
outside these estimates.

The AI audit is **not human-adjudicated precision**. “Full detection” means the
claim clearly identifies the approved defect. “Partial” keeps a related concern
with material gaps separate, without half-credit or rounding it into a hit.
Unmatched findings can still be real extra defects.

## Approved-label coverage

| Approved defect | Opus low | Terra low | Terra medium | Sol low |
|---|---|---|---|---|
| Django missing-module diagnostic (3933626526) | Published | Missed | Missed | Missed |
| Django admindocs closure name (3933613984) | Published | Published | Missed | Published; automatic matcher missed it |
| Ansible empty collection plus empty accessor (3778118024) | Partial candidate, suppressed at 55 | Missed | Missed; different empty-string claim | Missed |
| Black TokenError text mistaken for source (3006479284) | Correct candidate suppressed at 60 | Missed | Missed | Published |
| Black generic ParseError diagnostic (3006468789) | Partial published finding | Missed | Missed | Missed |

Across all drafted candidates, Opus has 3 clear approved-issue detections plus
2 partial ones. The other arms have the same coverage before and after
suppression. None provides sufficient published coverage for model selection.

## What the automatic scores got wrong

- **Opus / Black:** the 95-confidence “deindented assignment causes undefined or
  stale error” claim is false: successful parsing immediately breaks out of the
  loop, and both error branches initialize the variable. It nevertheless matches
  the nearby TokenError label. The actual TokenError finding is suppressed.
  At threshold 78, Opus publishes only this false critical finding while the
  mechanical precision proxy reports 1.00.
- **Terra-medium / Ansible:** its empty-string concern mechanically matches the
  approved empty-collection/empty-accessor regression. They are different
  claims. Empty-string lookup behavior is unchanged; its rejection requirement
  is unestablished. Strict approved-label coverage is zero, not the proxy's 1/5.
- **Sol / Django:** the correct closure-name finding gets no automatic match,
  because scoring compares it with the original comment's incomplete
  missing-module theory instead of the approved defect semantics.
- **Extra real issues:** all five Terra-low findings are code-supported in this
  AI audit, despite a mechanical overlap precision of 0.20. They include local
  task-exception handling, warning construction, resolver naming, and Black tab
  alignment. These are not automatically new ground-truth labels.

Published-output audit counts: Opus 3 supported / 2 partial / 1 unsupported;
Terra-low 5 supported; Terra-medium 3 supported / 2 uncertain; Sol 4 supported /
1 uncertain. These are review assessments, not calibrated precision estimates.
The shared uncertain Django claim concerns local-classmethod importability:
the helper inconsistency reproduces, but required contract/actionability is not
established, and the serializer consequence already existed before this PR.

## Why architecture remains the next lever

The changed Ansible Python patch omits collection initialization and loop
details; its YAML tests are filtered out. Black's short patch omits important
surrounding control flow. Django's relevant helper is visible across its diff,
yet coverage varies. “Per-hunk” alone is not enough: candidate-targeted
surrounding code and verification of the claimed failing path are needed.

The current critique is structural, not semantic. An evidence string and a
self-reported confidence value cannot distinguish a false 95 from a correct 60.
Lowering the threshold would recover one Opus defect but also admit weak claims;
raising it leaves the false critical finding. The threshold remains unchanged.

Next implementation should separate high-recall generation from candidate-local
context and semantic verification, preserve counterexamples, and evaluate
generation recall and verified output separately. Keep Sol-low and Terra-low
as baselines. Luna generation with Terra verification remains a later,
separately measured experiment, not a result of this single-reviewer matrix.

## Repairs after the frozen matrix

The audit found a separate benchmark parity bug: execution passed evidence to
graph critique, but rescoring discarded it. Raw runs now retain retrieval/tool
evidence, so scoring and threshold sweeps preserve unknown-source suppression.
This cannot change these diff-only results.

Official OpenAI documentation and the installed SDK also exposed an omitted
cache-write counter. Future runs retain it and price the write portion at
1.25 times ordinary input, disjoint from cached-read and ordinary input.
Historical raw reports are unchanged; their missing counts cannot be recovered
from retained usage, so this report provides conservative bounds instead of
inventing exact values. Both repairs occurred after all paid calls.

## Usage and cost provenance

| Configuration | Input | Cached read | Output total | Reasoning output |
|---|---:|---:|---:|---:|
| opus-5-low | 11953 | 0 | 4805 | Not collected |
| terra-low | 6931 | 2146 | 3687 | 2702 |
| terra-medium | 6931 | 2146 | 5047 | 3949 |
| sol-low | 6931 | 2146 | 2352 | 1302 |

Reasoning tokens are already included in total output; they are not charged
twice. Each OpenAI arm has 4,785 non-cache-read input tokens. The maximum missing
premium is 4,785 × 0.25 × input price / 1,000,000: $0.0023925 per Terra arm and
$0.004785 for Sol.

Standard rates checked against official pages: [Opus 5](https://platform.claude.com/docs/en/models/opus-5/whats-new-opus-5)
$5 input / $25 output per million; [Terra](https://developers.openai.com/api/docs/models/gpt-5.6-terra)
$2 / $0.20 cached read / $12 output; [Sol](https://developers.openai.com/api/docs/models/gpt-5.6-sol)
$4 / $0.40 cached read / $20 output. Sol's listed promotional pricing runs at
least through November 21, 2026. [Cache writes](https://developers.openai.com/api/docs/guides/prompt-caching)
use 1.25× input instead of, not in addition to, the full ordinary-input rate.

## Reproducibility and limits

- Final local verification on 2026-09-19: 392 tests passed, 19 database-dependent
  tests skipped; Ruff lint, all 46 format checks, and diff whitespace checks
  passed. A separate artifact audit reconciled all 27 candidates, counts,
  cost bounds, and local source/report links. No paid calls were repeated.
- [Manifest](manifest.json): code baseline, exact models/effort/settings, source
  and dataset hashes, package versions, pricing sources, and scheduling caveats.
- [Machine-readable comparison](comparison.json) and
  [all 27 candidate assessments](finding-audit.json).
- Original reports: [Opus](opus-5-low/benchmark.json),
  [Terra low](terra-low/benchmark.json),
  [Terra medium](terra-medium/benchmark.json),
  [Sol low](sol-low/benchmark.json), and [offline preflight](preflight/benchmark.json).
- [Approved decision sheet](../../ground-truth-candidates/adjudication-review.md).
  The original candidate dataset and historical reports were preserved.
- Labels and review discussions were not sent to the models. Human approval
  applies to five dataset defects and two exclusions, not these model findings.
- Three selected positive PRs are below the 20-case gate and contain no clean-PR
  control sample. Single runs cannot measure variance; P95 over three is just
  the slowest observation. Model aliases and cache/backend load are not fixed.
- Findings were checked against frozen code and isolated probes, not complete
  Django/Ansible/Black test suites. Extra issues need independent adjudication.
- All live commands exit 1 because overall benchmark gates fail; all three cases
  in every arm completed successfully. Nothing was posted to GitHub.
