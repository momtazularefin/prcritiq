# Configuration

## Status

The configuration surface is validated at load. Guardrail budgets, retrieval budgets, similarity mode, and acceleration are in force today; model, tracing, and publishing settings are declared but not used until their milestones.

## Non-Secret Settings

| Variable | Default | Purpose |
| --- | --- | --- |
| `PRCRITIQ_ENV` | `local` | Runtime environment label. |
| `PRCRITIQ_PUBLIC_BASE_URL` | `http://localhost:8000` | Public base URL for deployed app links. |
| `PRCRITIQ_DEFAULT_MODE` | `dry-run` | Default review mode. |
| `PRCRITIQ_MODEL_POLICY` | `auto` | Model routing policy. Not used yet. |
| `PRCRITIQ_MAX_FILES` | `30` | Maximum reviewable changed files per pull request. Files beyond it are reported as skipped, not dropped. |
| `PRCRITIQ_MAX_DIFF_LINES` | `2000` | Maximum changed lines before a file is skipped as an oversized diff. |
| `PRCRITIQ_MAX_FILE_BYTES` | `250000` | Maximum patch size to review, and maximum file size to extract into the workspace. |
| `PRCRITIQ_SIMILARITY` | `lexical` | Retrieval ranking: `lexical` or `embedding`. Lexical is the accepted v1 ranking; `embedding` is not implemented and fails rather than falling back. |
| `PRCRITIQ_MAX_CONTEXT_CHUNKS` | `24` | Maximum chunks of review context, focus and related combined. |
| `PRCRITIQ_MAX_CONTEXT_BYTES` | `200000` | Maximum bytes of review context. 70 percent is reserved for the changed code so a large diff cannot starve out related context. |
| `PRCRITIQ_MAX_ARCHIVE_FILES` | `20000` | Maximum indexable files extracted from a repository archive. |
| `PRCRITIQ_MAX_ARCHIVE_BYTES` | `80000000` | Maximum total bytes extracted from a repository archive. |
| `PRCRITIQ_MIN_PUBLISH_CONFIDENCE` | `78` | Minimum confidence percentage for posting. Not used yet. |
| `PRCRITIQ_TRACE_PROVIDER` | `none` | Tracing provider selection. Not used yet. |
| `ACCELERATION` | `none` | Strict acceleration mode: `none`, `gpu`, or `npu`. |
| `GITHUB_API_BASE_URL` | `https://api.github.com` | GitHub REST API base URL, overridden only for tests or enterprise installations. |
| `GITHUB_REQUEST_TIMEOUT_SECONDS` | `15` | Timeout for GitHub API requests. |

## Secret Settings

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
