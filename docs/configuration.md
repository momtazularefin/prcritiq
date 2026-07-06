# Configuration

## Status

M0 validates the public configuration surface. Most integrations are not used until later milestones.

## Non-Secret Settings

| Variable | Default | Purpose |
| --- | --- | --- |
| `PRCRITIQ_ENV` | `local` | Runtime environment label. |
| `PRCRITIQ_PUBLIC_BASE_URL` | `http://localhost:8000` | Public base URL for deployed app links. |
| `PRCRITIQ_DEFAULT_MODE` | `dry-run` | Default review mode. |
| `PRCRITIQ_MODEL_POLICY` | `auto` | Future model routing policy. |
| `PRCRITIQ_MAX_FILES` | `30` | Future maximum changed files per review. |
| `PRCRITIQ_MAX_DIFF_LINES` | `2000` | Future maximum diff lines per review. |
| `PRCRITIQ_MAX_FILE_BYTES` | `250000` | Future maximum file size for review context. |
| `PRCRITIQ_MIN_PUBLISH_CONFIDENCE` | `78` | Future minimum confidence percentage for posting. |
| `PRCRITIQ_TRACE_PROVIDER` | `none` | Future tracing provider selection. |
| `ACCELERATION` | `none` | Strict acceleration mode: `none`, `gpu`, or `npu`. |

## Secret Settings

These variables are listed in `.env.example` but must remain blank in committed files:

- `GITHUB_APP_ID`
- `GITHUB_WEBHOOK_SECRET`
- `GITHUB_PRIVATE_KEY`
- `ANTHROPIC_API_KEY`
- `OPENAI_API_KEY`
- `DATABASE_URL`
- `LANGSMITH_API_KEY`

## Acceleration Semantics

`ACCELERATION=none` means eligible local model work uses CPU providers.

`ACCELERATION=gpu` means eligible local model work requires a GPU provider. If the provider is not available, the program must raise a clear error.

`ACCELERATION=npu` means eligible local model work requires an NPU provider. If the provider is not available, the program must raise a clear error.

There is no silent CPU fallback for requested hardware acceleration.
