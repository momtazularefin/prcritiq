# Controlled semantic-verifier smoke comparison

Completed 2026-09-20; evidence reviewed and retained 2026-09-21. **Prefer Sol-low
as the experimental verifier baseline, but promote neither model to an automatic
approval gate.** Production routing, thresholds, review behavior, and posting are
unchanged.

## Cost and latency

| Verifier, low effort | Calls | Estimated cost | Median seconds per candidate | Confirmed / rejected / uncertain / unavailable |
| --- | ---: | ---: | ---: | --- |
| GPT-5.6 Sol | 24 | $0.469708 | 7.358 | 16 / 4 / 4 / 3 |
| GPT-5.6 Terra | 24 | $0.234810 | 4.603 | 20 / 4 / 0 / 3 |

Total: **48 requests, $0.704518**, below the $2 working budget. All requested
responses had complete recorded usage and valid structured decisions; no provider
error or retry occurred. These costs are token-derived estimates, not an invoice.
Terra cost about 50% less and its median was about 37% lower in this run. That
advantage does not outweigh the decisive false confirmation described below.
Latency is per verification candidate, not the earlier generation time per PR.

The estimates include cache-write tokens separately from uncached and cached-read
input. Frozen per-million prices were Sol $4 / $0.40 / $5 / $20 and Terra
$2 / $0.20 / $2.50 / $12 for input / cache read / cache write / output, checked
against the official [Sol](https://developers.openai.com/api/docs/models/gpt-5.6-sol)
and [Terra](https://developers.openai.com/api/docs/models/gpt-5.6-terra) pages before
the run. Reasoning tokens are part of output, not an additional charge.

## Controlled inputs and limitations

- Reused all 27 candidates from the four frozen generator arms: Opus-low,
  Terra-low, Terra-medium, and Sol-low, including six suppressed candidates.
  No drafting calls were repeated. Both verifiers received identical candidate,
  context, and prompt data for each pair.
- 24 candidates per model were callable. Three newly added Django helper
  allegations lacked a same-qualified base symbol and were left
  `context_unavailable`, without calls. These are preparation gaps, not model
  verdicts.
- One repetition, three positive PRs, five approved defects, no clean PRs, and
  several duplicate/paraphrased allegations. Results are descriptive smoke
  evidence, **not calibrated precision, product recall, or a production winner**.
- Sequential generator blocks alternated which verifier ran first. This limits
  but does not eliminate order/cache/network effects. Requested aliases and SDK
  version are recorded; immutable backend snapshots are not attested.
- Frozen base/head sources were checked against available patch lines. This is
  operator-selected revision binding, not Git-object attestation of historical
  generation. Dependency contracts and downstream consumers can still be absent.
- The [blind audit](blind-audit.json) was prepared before calls and remains
  unchanged after unblinding. It is an independent AI evidence review, not new
  human adjudication. Its candidate labels must not be confused with the five
  previously human-approved corpus defects.

## What the verdicts actually show

| Blind assessment | Sol: confirm / reject / uncertain / unavailable | Terra: confirm / reject / uncertain / unavailable |
| --- | --- | --- |
| Supported | 13 / 0 / 3 / 0 | 15 / 1 / 0 / 0 |
| Partial | 1 / 2 / 1 / 3 | 2 / 2 / 0 / 3 |
| Unsupported | 0 / 2 / 0 / 0 | 1 / 1 / 0 / 0 |
| Uncertain | 2 / 0 / 0 / 0 | 2 / 0 / 0 / 0 |

These are assessment/verdict counts, not an accuracy confusion matrix. In
particular, Sol's three abstentions on supported Black alignment claims concern
tokenizer column/tab contracts absent from the supplied snippets; they cannot
automatically be treated as reasoning errors.

The material findings, with candidate-ID prefixes for lookup in the raw reports:

1. **Terra confirms a disproved critical Black allegation (`ca6db284`).** The
   supplied function breaks after successful parsing at head line 74, skipping
   the deindent assignment at line 102. Terra's explanation says that assignment
   still runs after a successful parse. There is no `finally`. Sol correctly
   rejects the allegation. The complete relevant control flow was supplied:
   this is not fixed merely by increasing the context window.
2. **Both overconfirm unchanged Ansible empty-string behavior (`16bb6432`,
   `d7a89553`).** Base and head both turn `''` into `['']` and perform the same
   lookup. Their explanations infer a required rejection from the new empty-list
   guard/error wording, without a supporting contract. The blind assessment
   remains uncertain, not confirmed ground truth.
3. **Partial support is not permission to publish unchanged wording.** Both
   models find the valid narrower counterexample `subelements([], [])` for
   `3fda8487` while correcting the original candidate's generic pairing premise.
   That is useful reasoning, but the unchanged candidate stays partial. Terra
   also confirms generic Black format-change claim `4ab4ba52` without establishing
   its assumed exact-message consumer or the approved ParseError defect.
4. **Terra is inconsistent across equivalent Django resolver allegations.** It
   rejects Opus `5cdc5371` as an intentional fallback while confirming
   `f35d29bb` and `a871e05f`. All three context objects are identical. The supplied
   display-identity consumer does not require an importable serialization path;
   transferring the helper's restriction to that consumer is unjustified. Sol
   consistently confirms the three claims. Neither paraphrase consistency nor
   intentionality should be inferred from one favored wording.
5. **Useful recovery is real, but limited.** Both confirm the Black TokenError
   error-description-as-source defect (`a59880bb`, previously suppressed Opus,
   and `1c3ad103`). Both retain all six fully matched approved-label candidate
   instances, which represent **three unique approved defects out of five**, not
   six defects or five-of-five recovery. The Ansible and generic ParseError
   partial matches are excluded. Per generator, strict unique confirmed labels
   are Opus 3, Terra-low 1, Terra-medium 0, and Sol-low 2; pooling is not a shipped
   multi-generator pipeline.

## Recommendation and next development

Keep Sol-low as the experimental final-verifier baseline and Terra-low as a
cost-oriented challenger/candidate-generation option. Neither is authorized to
promote an unchanged finding automatically on this evidence.

Next, require a concrete introduced failure under the same base/head input, or
an evidenced new contract obligation; distinguish an exact confirmed allegation
from a narrower corrected claim. Add regression cases for the observed false
confirmations, consumer-purpose transfer, and paraphrase instability. Then add
targeted dependency-contract context and conservative added-symbol handling.
Do not silently relabel the blind audit or tune on these examples and call the
same examples an independent test. Expand held-out positive and clean-PR human
adjudication before making product-level quality claims. No new paid run is
needed to preserve or reproduce this analysis.

## Retained evidence and offline reproduction

- [Manifest](manifest.json): schedule, requested aliases, SDK, budget ledger,
  frozen input/runtime hashes and report hashes.
- `raw/<generation-arm>--<verifier>-low/verification.json`: all eight paid
  reports, byte-for-byte unchanged, including candidate/context/decision/usage.
  Their historical `generation_metrics` remain uncorrected raw fields; do not
  report those mechanical overlap metrics as semantic quality.
- [Sources](sources.json): one copy of the common frozen source bundle; eight
  identical duplicates were not retained. Source text is upstream evidence, not
  executable trusted code.
- `dataset-certified.jsonl`: the exact three-case/five-label input, preserved
  separately so future corpus expansion cannot change this comparison's denominator.
- [Comparison JSON](comparison.json): validated paired joins, descriptive
  aggregates, token sums, and nearest-rank p95s (small samples, not SLOs).
- `.gitattributes` preserves paid artifact bytes across Windows/Linux checkouts.

From the project root, this makes **no network or model calls**:

```powershell
.venv/Scripts/python.exe eval/summarize_verifier_comparison.py eval/runs/2026-09-20-verifier-comparison
```

Use `--write` only to regenerate derived `comparison.json`. Never rerun the live
driver to resume this completed experiment. An independent audit also recomputed
all 48 prompt hashes, all 174 source IDs/spans, frozen input and runtime hashes,
and per-candidate/run/ledger costs; all reconciled. Hash agreement detects drift,
not authenticity of provider execution or semantic truth.
