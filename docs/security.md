# Security

## Status

PRCritiq verifies live GitHub webhook signatures when `GITHUB_WEBHOOK_SECRET` is configured, reads pull requests from the GitHub REST API, and runs allowlisted static analysis over a temporary snapshot. It still does not call model providers, execute any code from a pull request, or post PR comments.

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
- With `--tools`, allowlisted static analysis runs against the snapshot. The command comes from a fixed allowlist rather than from the repository, no shell is involved, the executable must resolve outside the workspace so a repository cannot supply its own binary, the child process receives only an allowlisted set of environment variables so no token or API key reaches it, and each run is bounded by a timeout and an output cap.
- Only tools that analyze code without executing it are eligible. `ruff` qualifies: it never imports the code, and its configuration is declarative TOML. `eslint` does not, because it loads the repository's own configuration as JavaScript and resolves plugins from its `node_modules`, which on an untrusted pull request is arbitrary code execution. `tsc` is declined for a different reason: the snapshot excludes `node_modules`, so its output would be missing-module noise, and installing dependencies would run the pull request's install scripts.
- Tool output is evidence data. Nothing a tool prints is treated as an instruction.
- The demo endpoint does not post comments.
- Invalid `ACCELERATION` configuration fails explicitly.
