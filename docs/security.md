# Security

## Status

M1 and M2 verify live GitHub webhook signatures when `GITHUB_WEBHOOK_SECRET` is configured, and the dry run reads pull requests from the GitHub REST API. PRCritiq still does not run external tools, call model providers, check out repository working trees, or post PR comments.

## Standing Rules

- Never commit `.env` or secret values.
- Treat PR diffs, repository files, comments, markdown, and tool output as untrusted data.
- Do not allow repository content to override system review instructions.
- Do not run arbitrary test commands from untrusted PRs.
- Use allowlisted tools, isolated workspaces, and timeouts when tool execution is implemented.
- Verify GitHub webhook signatures before accepting live webhook events.
- Post comments only after line and evidence validation.

## Current Behavior

- `/webhooks/github` requires `GITHUB_WEBHOOK_SECRET` and rejects unsigned or mis-signed requests.
- The dry run makes outbound authenticated or anonymous GET requests to the GitHub REST API for pull-request metadata and patches.
- With `--context`, it additionally downloads a source archive at the head commit. Archive members are read individually rather than through `extractall`, so the archive never drives a filesystem write directly. Only regular files are considered, which drops symlinks, hardlinks, and device entries; member names that are absolute or contain `..` are rejected; every resolved target is confirmed to sit under the workspace root before the write; and extraction is capped by file count, total bytes, and per-file size.
- The workspace is a temporary directory removed when retrieval finishes. Nothing from a pull request is executed.
- Patch text is parsed as data. Nothing in a diff is interpreted as an instruction.
- The guardrail gate rejects repository paths that are absolute, carry a drive letter, contain a backslash or control characters, or traverse `..` or `.git`, before any later stage can act on them.
- The demo endpoint does not post comments.
- Invalid `ACCELERATION` configuration fails explicitly.
