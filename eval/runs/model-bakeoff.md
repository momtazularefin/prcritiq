# PRCritiq one-case model bake-off diagnostic

Run date: 2026-09-02

## Outcome

This run verified the live Anthropic and OpenAI provider paths, attribution,
usage accounting, and fail-closed dataset preflight. It did **not** identify a
production model winner.

The human adjudication retained one confirmed label from 18 candidates, so all
four configurations reviewed the same single PR: HypothesisWorks/hypothesis
#4861 at head `37999eb97e416a19881c3cc463f9f7a73623d0e7`. Every API call completed
without an error, but no configuration matched the retained label at any
threshold from 50 through 78.

The four calls cost an estimated **$0.1374** in total.

| Configuration | Latency | Estimated cost | Published at 65 | Suppressed | Mechanical label matches |
| --- | ---: | ---: | ---: | ---: | ---: |
| Claude Opus 5, low | 25.05 s | $0.0632 | 0 | 3 low-confidence | 0 |
| GPT-5.6 Terra, low | 28.98 s | $0.0262 | 2 | 0 | 0 |
| GPT-5.6 Terra, medium | 18.44 s | $0.0187 | 1 | 0 | 0 |
| GPT-5.6 Sol, low | 20.27 s | $0.0293 | 1 | 0 | 0 |

One observation is not a benchmark conclusion: Terra medium happened to be the
fastest and cheapest call because it produced fewer output tokens. Terra's
published token rates do not become cheaper at medium effort.

## Ground-truth integrity finding

The only retained label says the new `builtins.sentinel` strategy must generate
valid Python identifiers for pickle support. The frozen fixture also contains a
direct reply from the PR author disputing that conclusion: these generated
objects are not bound to matching importable module globals, so filtering their
names to identifiers would not make them picklable.

The final PEP 661 specification supports the reply. Each `sentinel(name)` call
returns a fresh object; pickling works only when a sentinel is importable from
its module or class under the matching name. The name is required to be a
string, not a valid identifier. The PR merged without adding the proposed
identifier filter.

This does not silently reverse the human verdict. It means the verdict must be
revisited with the thread context before it can act as ground truth. Ten of the
18 candidate comments have captured replies, but the first adjudication queue
did not show them. `adjudications-with-context.jsonl` now carries those replies
and marks which were written by the PR author.

Sources:

- [PEP 661 final specification](https://peps.python.org/pep-0661/)
- [Python 3.15 `sentinel` documentation](https://docs.python.org/3.15/library/functions.html)
- [Hypothesis compatibility policy](https://hypothesis.readthedocs.io/en/latest/compatibility.html)

## Manual finding audit

Mechanical overlap precision is zero because every output differs from the one
retained label. Manual inspection gives a more useful, but still non-final,
picture:

- **Sol low:** raised one plausible portability/forward-compatibility defect.
  The new opcode fields use `dis.opmap[...]` behind version checks even though
  the comment promises absent opcodes become `None`. Hypothesis supports PyPy,
  so actual opcode membership is stronger evidence than a CPython version
  number. `dis.opmap.get(...)` would make the stated fallback real.
- **Terra low:** raised the same opcode risk, plus an incorrect finding about
  `typing.ByteString`. The `or` expression short-circuits on Python 3.15, so the
  warned-about attribute is not evaluated there.
- **Terra medium:** raised a plausible but unverified namespace-contract concern
  about deleting `__spec__` before executing generated source. The diff alone
  does not establish that supported callers execute source which reads that
  global.
- **Opus low:** suppressed all three candidates at threshold 65. Its leading
  candidate claimed sentinels are globally interned by name, but the final PEP
  explicitly rejected that registry design and specifies a fresh object per
  call.

Sol low produced the cleanest output on this one PR, but a single disputed-label
case cannot justify selecting it. Terra low demonstrated the desired recall
orientation and also the exact false-positive risk that requires a semantic
verification stage.

## Price context

The local estimator uses the current standard API prices:

| Model | Input / MTok | Output / MTok |
| --- | ---: | ---: |
| Claude Opus 5 | $5 | $25 |
| GPT-5.6 Terra | $2 | $12 |
| GPT-5.6 Sol | $4 | $20 |

Official references:

- [Claude Opus 5 model and pricing](https://platform.claude.com/docs/en/about-claude/models/whats-new-claude-4-7)
- [GPT-5.6 Terra](https://developers.openai.com/api/docs/models/gpt-5.6-terra)
- [GPT-5.6 Sol](https://developers.openai.com/api/docs/models/gpt-5.6-sol)

## Decision and next step

No model is selected. Paid expansion pauses until the contextual adjudication
queue is reviewed. If the only confirmed row becomes excluded, harvest more
exact-revision candidates until there is a multi-case smoke set of real defects.

After that, repeat this same four-way matrix. The next architecture experiment
remains a recall-oriented per-hunk generator followed by candidate-targeted
context and a separate semantic verifier. The current run suggests Sol low is a
useful quality reference and Terra low is the cost-sensitive generation
candidate, but neither role is proven yet.
