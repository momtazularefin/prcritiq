# Security

## Status

M1 verifies live GitHub webhook signatures when `GITHUB_WEBHOOK_SECRET` is configured. It still does not run external tools, call model providers, or post PR comments.

## Standing Rules

- Never commit `.env` or secret values.
- Treat PR diffs, repository files, comments, markdown, and tool output as untrusted data.
- Do not allow repository content to override system review instructions.
- Do not run arbitrary test commands from untrusted PRs.
- Use allowlisted tools, isolated workspaces, and timeouts when tool execution is implemented.
- Verify GitHub webhook signatures before accepting live webhook events.
- Post comments only after line and evidence validation.

## Current M1 Behavior

- The scaffold requires `GITHUB_WEBHOOK_SECRET` for `/webhooks/github`.
- The dry-run command does not fetch remote code.
- The demo endpoint does not post comments.
- Invalid `ACCELERATION` configuration fails explicitly.
