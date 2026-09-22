# Experimental semantic verification replay

`prcritiq verify` checks saved candidates without regenerating findings. It is an
experimental evaluation tool, not a posting path or a production review stage.
The existing review graph, routing, and confidence threshold are unchanged.

## Prepare without model spend

From the project root, run:

```powershell
uv run prcritiq verify --dataset eval/ground-truth-candidates/dataset-certified.jsonl --report eval/runs/2026-09-18-certified-smoke/opus-5-low/benchmark.json --out eval/reports/verify-prepared --fetch-context
```

This makes read-only GitHub requests at full base/head commit SHAs, but no model
calls. Public repositories need no token; `GITHUB_TOKEN` raises the rate limit.
Outputs are `sources.json` and `verification.json` in a **fresh** directory.
Existing directories are rejected, even when empty. Scratch reports under
`eval/reports/` are ignored by git; retain evidence runs deliberately after review.

Preparation of the four saved smoke reports on 2026-09-19/20 preserved all
27 candidates, including 6 previously suppressed candidates: 24 were prepared
and 3 reported `context_unavailable`. The latter target a newly introduced
Django helper for which no same-named base function exists. The Black snippets
include the successful parse `break`; the Ansible snippets include the full
function's initialization and loop. This demonstrates context coverage only.
Preparation alone does not measure verification accuracy or quality improvement.
The [preparation evidence](../eval/runs/2026-09-20-verifier-preparation.json)
retains input hashes, source IDs, all candidate identities and coverage counts.

## Completed frozen-candidate comparison

The [Sol-low versus Terra-low comparison](../eval/runs/2026-09-20-verifier-comparison/comparison.md)
subsequently replayed the same saved candidates and frozen source bundles without
another drafting run. Each verifier retained 27 candidates: 24 received a model
decision and 3 remained `context_unavailable`. The 48 calls cost an estimated
**$0.704518**, separate from generation costs.

| Verifier | Calls | Estimated cost | Median request latency |
|---|---:|---:|---:|
| GPT-5.6 Sol, low | 24 | $0.469708 | 7.36 s |
| GPT-5.6 Terra, low | 24 | $0.234810 | 4.60 s |

Sol-low is the safer **experimental baseline**, not a production recommendation.
It rejected the false Black critical allegation by following the visible `break`;
Terra-low confirmed it using an incorrect account of Python control flow. Both
verifiers confirmed Ansible empty-string allegations without evidence that the
unchanged lookup behavior violates a required contract. Both also supplied a
correct narrower empty-collection example for a partially correct allegation;
that does not make the unchanged original candidate fully correct or establish
full approved-label recovery.

The AI audit distinguishes sound rejection, unsupported confirmation, partial
support, and justified abstention. In particular, Black's tokenizer coordinate
contract was absent from the supplied context, so abstention on alignment claims
is not automatically a verifier error. These are small, single-run findings, not
human-adjudicated precision or a production-quality certification. No review
defaults, routing, confidence threshold, or posting behavior changed.

## Explicit paid replay

Only `--live` permits model calls. To compare verifiers, keep the same saved
candidate report and source bundle, use separate output directories, and change
only the model. For example (these commands **spend API credit**):

```powershell
uv run prcritiq verify --dataset eval/ground-truth-candidates/dataset-certified.jsonl --report eval/runs/2026-09-18-certified-smoke/opus-5-low/benchmark.json --source-bundle eval/reports/verify-prepared/sources.json --out eval/reports/verify-sol --live --model gpt-5.6-sol --effort low --max-candidates 20
uv run prcritiq verify --dataset eval/ground-truth-candidates/dataset-certified.jsonl --report eval/runs/2026-09-18-certified-smoke/opus-5-low/benchmark.json --source-bundle eval/reports/verify-prepared/sources.json --out eval/reports/verify-terra --live --model gpt-5.6-terra --effort low --max-candidates 20
```

Supported replay choices are Sol/Terra at low or medium effort, selected explicitly,
without fallback. `OPENAI_API_KEY` is required for live replay. The global request
cap defaults to 20 (allowed range 1–100); it is a request cap, not a dollar limit.
Output is capped at 8,192 tokens per request; automatic SDK retries are disabled.
The verifier uses the Responses API with typed output, `store=False`, and
current-turn reasoning context. See [official structured output documentation](https://developers.openai.com/api/docs/guides/structured-outputs).

## V2 evidence refinement (2026-09-21)

New reports use `schema_version: 2` and
`verification_protocol: candidate-verifier-v2`. The retained September comparison
is V1 evidence and is not rewritten or re-scored as though V2 ran then.

V2 separates `partial` from `confirmed`: a valid narrower example does not confirm
an allegation whose material premise was wrong. Both positive verdicts require a
concrete scenario, explicit `base_behavior` and `head_behavior`, and citations to
both source sides. `support_basis` must be `introduced_failure` or
`contract_violation`, not `not_established`. A contract-based verdict also needs
`contract_evidence` describing the introduced violation and `contract_source_refs`
pointing to supplied evidence. A partial verdict needs its material qualification
in `counterevidence`. These fields are required in the response schema but may be
empty when unsupported for rejected/uncertain responses.

Identical base/head outcome summaries cannot establish an introduced failure.
A newly evidenced contract can change while behavior stays unchanged, so that
case instead requires the contract evidence. Prompt instructions reject inferred
contracts from guard/error wording alone, follow actual control-flow exits, and
distinguish consumer purpose from helper restrictions. These remain **model
assertions**: the validator cannot prove different prose describes a real failure
or that a cited snippet entails a contract. Structurally incomplete assertions
become `invalid_decision`, retaining usage and the original decision for review.
Head-only context is now skipped before a model call; explicit added-symbol
absence reasoning remains deferred.

This refinement has offline regression coverage, not a new measured accuracy
gain. No live rerun or production integration is part of the portfolio closeout.
Future quality research needs held-out evaluation; these observed failure cases
are development examples, not an independent test set.

## Evidence and safety boundaries

- The **entire selected dataset** must be certified before any network or model
  call. Case identities, candidate schema, all requested paths, revisions and
  supplied source bundles are checked before spending. Labels and human comments
  never enter the verifier prompt.
- Every saved candidate is preserved with an ID, original target, confidence,
  severity, and suppression provenance. Low confidence does not disqualify a
  replay candidate. The verifier sees the allegation and evidence, not those
  confidence/severity fields, suggested fixes, or previous publication decision.
- Guardrail eligibility, added-line placement, and nonempty candidate evidence
  remain prerequisites. Verification cannot invent a replacement finding,
  retarget a line, or authorize publication.
- Source fetching allows at most 30 indexable changed paths per revision, 250 KB
  per file and 2 MB per revision. Streams are bounded before UTF-8 decoding;
  redirects, unsafe paths, non-UTF-8 data and metadata responses are refused.
  Missing files remain explicit. No repository code is executed.
- Python evidence selects the enclosing function including decorators and its
  same-qualified-name base counterpart, respecting renames and line shifts.
  Up to two changed-file helpers are selected heuristically, prioritizing actual
  bare calls and top-level definitions. This is not import/call-graph resolution.
  Other languages use bounded windows. Total snippet text is capped at 24 KB,
  with at most 160 lines per snippet. Truncation or missing/ambiguous context
  prevents a model call; newly added functions in existing files currently abstain.
- The structured verdict is `confirmed`, `partial`, `rejected`, or `uncertain`,
  with the V2 evidence requirements above. Neither confirmed nor partial is a
  publication authorization. Unknown references in either citation field
  invalidate any verdict. These checks establish
  provenance, **not semantic truth or complete dependency coverage**.
- Inputs are untrusted JSON data separated from verifier instructions. This
  reduces prompt-injection risk but does not prove model immunity.

## Reproducibility and limitations

Reports hash the dataset, original generation report, frozen fixtures, source
bundle and each paid prompt. Source IDs include revision, side, path, span and
text. `sources.json` can be reused without GitHub access:

```json
{
  "schema_version": 1,
  "cases": [{
    "case_id": "dataset-case-id",
    "repo": "owner/name",
    "base_sha": "<40 hex characters>",
    "head_sha": "<40 hex characters>",
    "base": {"src/example.py": "<base source text>"},
    "head": {"src/example.py": "<head source text>"}
  }]
}
```

Local bundle contents are operator-supplied. All available frozen patch lines
are compared with the bundle, but unseen source is not cryptographically attested
to Git objects. Legacy generation reports lack revision fields: binding them to
the selected certified dataset is explicitly recorded as an operator choice,
not proof of the original generation revision. GitHub's raw Contents endpoint
may resolve in-repository symlinks; this tool does not attest regular-file tree
mode and never follows a response-supplied URL. JSON inputs and emitted source
bundles are limited to 32 MB; each saved candidate is limited to 32 KB.

`verification.json` retains every row, including structural rejects, unavailable
context, exhausted budget, invalid decisions and errors. Usage and estimated cost
are **verification-only**, separate from copied generation metrics. Known billed
usage survives malformed, incomplete or refused responses. Unknown usage marks
`usage_complete=false`; the recorded cost is then only the known subtotal, not
the complete bill. Provider failures abort further calls. Each completed request
is checkpointed; a process kill or disk failure can still leave pending rows or
lose the most recent response. A live report is not automatically resumable.

Exit status is nonzero for preflight/provider failure, missing context, structural
rejects, exhausted budget or invalid decisions. A semantic partial, rejected, or
uncertain verdict is a completed assessment, not a command failure. An empty set of
confirmed findings must never be interpreted as a clean-PR guarantee.
