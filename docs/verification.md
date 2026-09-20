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
No verification accuracy, model ranking, or quality improvement is claimed.
The [preparation evidence](../eval/runs/2026-09-20-verifier-preparation.json)
retains input hashes, source IDs, all candidate identities and coverage counts.

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

The intended next experiment is identical-candidate Sol-low versus Terra-low
verification, followed by evidence review of retained, rejected, and uncertain
claims. It does not require another four-way drafting run. Human adjudication
remains necessary; verifier decisions do not certify ground truth.

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
- The structured verdict is `confirmed`, `rejected`, or `uncertain`, with a short
  rationale, failure scenario, counterevidence and exact source IDs. A confirmed
  verdict requires complete supplied context, a failure scenario and head-source
  citation. Unknown references invalidate any verdict. These checks establish
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
rejects, exhausted budget or invalid decisions. A semantic rejection or uncertain
verdict is a completed assessment, not a command failure. An empty set of
confirmed findings must never be interpreted as a clean-PR guarantee.
