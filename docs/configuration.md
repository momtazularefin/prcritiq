# Configuration

## Status

The configuration surface is validated at load. Guardrail and retrieval budgets, model routing, reasoning effort, persistence, tracing, and opt-in publishing are active. Unknown routing or acceleration values fail rather than silently selecting another path.

## Non-Secret Settings

| Variable | Default | Purpose |
| --- | --- | --- |
| `PRCRITIQ_ENV` | `local` | Runtime environment label. |
| `PRCRITIQ_PUBLIC_BASE_URL` | `http://localhost:8000` | Public base URL for deployed app links. |
| `PRCRITIQ_DEFAULT_MODE` | `dry-run` | Default review mode. |
| `PRCRITIQ_MODEL_POLICY` | `auto` | Routing policy: `auto`, `anthropic`, `openai`, or `mock`. Anything else fails. |
| `PRCRITIQ_ANTHROPIC_MODEL` | `claude-opus-5` | Model used when routing selects Claude. |
| `ANTHROPIC_WORKSPACE_ID` | unset | Required when `ANTHROPIC_API_KEY` is identity-linked; the API rejects the request without it. |
| `PRCRITIQ_OPENAI_MODEL` | `gpt-5.6-terra` | Model used when routing selects OpenAI. GPT-5.6 uses the Responses API and typed structured output. |
| `PRCRITIQ_MODEL_EFFORT` | `low` | Provider reasoning effort. OpenAI accepts `none` through `max`; Anthropic accepts `low` through `max`. Unsupported values fail. |
| `PRCRITIQ_MAX_FILES` | `30` | Maximum reviewable changed files per pull request. Files beyond it are reported as skipped, not dropped. |
| `PRCRITIQ_MAX_DIFF_LINES` | `2000` | Maximum changed lines before a file is skipped as an oversized diff. |
| `PRCRITIQ_MAX_FILE_BYTES` | `250000` | Maximum patch size to review, and maximum file size to extract into the workspace. |
| `PRCRITIQ_SIMILARITY` | `lexical` | Retrieval ranking: `lexical` or `embedding`. Lexical is the accepted v1 ranking; `embedding` is not implemented and fails rather than falling back. |
| `PRCRITIQ_MAX_CONTEXT_CHUNKS` | `24` | Maximum chunks of review context, focus and related combined. |
| `PRCRITIQ_MAX_CONTEXT_BYTES` | `200000` | Maximum bytes of review context. 70 percent is reserved for the changed code so a large diff cannot starve out related context. |
| `PRCRITIQ_MAX_ARCHIVE_FILES` | `20000` | Maximum indexable files extracted from a repository archive. |
| `PRCRITIQ_MAX_ARCHIVE_BYTES` | `80000000` | Maximum total bytes extracted from a repository archive. |
| `PRCRITIQ_TOOL_TIMEOUT_SECONDS` | `60` | Timeout for a single static-analysis command. |
| `PRCRITIQ_MAX_TOOL_OUTPUT_BYTES` | `200000` | Cap on captured stdout and stderr per tool run. |
| `PRCRITIQ_MIN_PUBLISH_CONFIDENCE` | `78` | Findings below this model-reported confidence are suppressed. Benchmark reports include a threshold sweep because the value is not yet calibrated on certified data. |
| `PRCRITIQ_TRACE_PROVIDER` | `none` | Tracing provider: `none` or `langsmith`. LangSmith also needs `LANGSMITH_API_KEY`. |
| `LANGSMITH_PROJECT` | `prcritiq` | LangSmith project a traced run is filed under. |
| `ACCELERATION` | `none` | Strict acceleration mode: `none`, `gpu`, or `npu`. |
| `GITHUB_API_BASE_URL` | `https://api.github.com` | GitHub REST API base URL, overridden only for tests or enterprise installations. |
| `GITHUB_REQUEST_TIMEOUT_SECONDS` | `15` | Timeout for GitHub API requests. |

## Secret Settings

`.env` is loaded automatically when present, and a real environment variable always wins over it, so an export or a CI secret is never overridden by a stale local file.

These variables are listed in `.env.example` but must remain blank in committed files:

- `GITHUB_APP_ID`
- `GITHUB_WEBHOOK_SECRET`
- `GITHUB_PRIVATE_KEY`
- `GITHUB_TOKEN`
- `ANTHROPIC_API_KEY`
- `OPENAI_API_KEY`
- `DATABASE_URL`
- `LANGSMITH_API_KEY`

## Acceleration Semantics

`ACCELERATION=none` means eligible local model work uses CPU providers.

`ACCELERATION=gpu` means eligible local model work requires a GPU provider. If the provider is not available, the program must raise a clear error.

`ACCELERATION=npu` means eligible local model work requires an NPU provider. If the provider is not available, the program must raise a clear error.

There is no silent CPU fallback for requested hardware acceleration.

Today the only local model work retrieval could accelerate is embedding, which is not implemented. Requesting `gpu` or `npu` therefore raises rather than running the lexical path and reporting success, because reporting accelerated work that did not happen is the failure the rule exists to prevent.
