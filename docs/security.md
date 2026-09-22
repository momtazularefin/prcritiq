# Security

## Status

PRCritiq verifies GitHub webhook signatures, reads pull requests through the REST API, can call configured model providers, and can post validated comments when `--post` is explicitly supplied. It does not execute pull-request code; static analysis is restricted to a fixed allowlist over a bounded temporary snapshot.

## Standing Rules

- Never commit `.env` or secret values.
- Treat PR diffs, repository files, comments, markdown, and tool output as untrusted data.
- Do not allow repository content to override system review instructions.
- Do not run arbitrary test commands from untrusted PRs.
- Use allowlisted tools, isolated workspaces, and timeouts for static analysis.
- Verify GitHub webhook signatures before accepting live webhook events.
- Post comments only after line and evidence validation.

## Current Behavior

- `/webhooks/github` requires `GITHUB_WEBHOOK_SECRET` and rejects unsigned or mis-signed requests.
- An accepted `pull_request` event becomes one recorded run, keyed by installation, repository, pull request, head SHA, and delivery id under a `UNIQUE` constraint. A redelivery resolves to the same run and is not executed again. Webhook runs are dry-run: nothing in the webhook path can post.
- With `GITHUB_APP_ID` and `GITHUB_PRIVATE_KEY`, a webhook run reads through a short-lived installation token minted from a signed app JWT. A failure to mint one fails the run; it never falls back to another credential. The private key stays in the process and is never logged or stored.
- Private repositories are refused by default on every public surface. The webhook ignores an event whose repository is private or whose payload omits visibility, execution re-checks visibility from the API before reading any changed file, and the demo checks it before reading files. `PRCRITIQ_ALLOW_PRIVATE_REPOS=true` is the only way to change that.
- Model review on webhook runs is off unless `PRCRITIQ_WEBHOOK_REVIEW=true`, so an event cannot spend model credit by default.
- `GET /runs/{id}` is public and reports status, the count-only summary, errors, and file and finding counts. It never returns finding text, diffs, or code.
- The dry run makes outbound authenticated or anonymous GET requests to the GitHub REST API for pull-request metadata and patches.
- With `--context`, it additionally downloads a source archive at the head commit. Archive members are read individually rather than through `extractall`, so the archive never drives a filesystem write directly. Only regular files are considered, which drops symlinks, hardlinks, and device entries; member names that are absolute or contain `..` are rejected; every resolved target is confirmed to sit under the workspace root before the write; and extraction is capped by file count, total bytes, and per-file size.
- The workspace is a temporary directory removed when retrieval finishes. Nothing from a pull request is executed.
- Patch text is parsed as data. Nothing in a diff is interpreted as an instruction.
- The guardrail gate rejects repository paths that are absolute, carry a drive letter, contain a backslash or control characters, or traverse `..` or `.git`, before any later stage can act on them.
- With `--tools`, allowlisted static analysis runs against the snapshot. The command comes from a fixed allowlist rather than from the repository, no shell is involved, the executable must resolve outside the workspace so a repository cannot supply its own binary, the child process receives only an allowlisted set of environment variables so no token or API key reaches it, and each run is bounded by a timeout and an output cap.
- Only tools that analyze code without executing it are eligible. `ruff` qualifies: it never imports the code, and its configuration is declarative TOML. `eslint` does not, because it loads the repository's own configuration as JavaScript and resolves plugins from its `node_modules`, which on an untrusted pull request is arbitrary code execution. `tsc` is declined for a different reason: the snapshot excludes `node_modules`, so its output would be missing-module noise, and installing dependencies would run the pull request's install scripts.
- Tool output is evidence data. Nothing a tool prints is treated as an instruction.
- Model prompts fence diffs, retrieved code, and tool output in labelled blocks, and the system prompt states that content inside them is untrusted data that cannot change the instructions.
- Prompt-injection defense does not rely on the model complying. Every drafted finding is re-validated after the call against the diff, the retrieved chunk ids, and the tool diagnostics; a finding that targets a line the pull request never added, or cites a source this run never produced, is suppressed regardless of what the model claimed.
- No model output can trigger shell execution. The tool allowlist is fixed in code and takes no input from a model.
- Database credentials come from `DATABASE_URL` and are never committed. The bundled compose file is local development only and uses obviously non-production credentials.
- Posting is the only write to GitHub and is off unless `--post` is given. It requires a review, a token, and a database; PRCritiq will not attempt an unauthenticated write.
- A stored body hash prevents ordinary repeat posting within one run. It is not a transactional exactly-once guarantee across GitHub and Postgres: concurrent workers or a crash after GitHub accepts a comment but before the database records it can still duplicate a post. Deployment must serialize posting or add reconciliation before claiming exactly-once behavior.
- Every target line is re-validated immediately before the write, because GitHub accepts a comment on any line it considers part of the diff, which is wider than the lines the pull request added.
- Markdown rendering escapes pipes and newlines in pull request text, so untrusted content cannot forge table structure in a published report.
- The demo endpoint does not post comments or call a model. It is rate limited per client address, reviews public repositories only by default, and can be switched off with `PRCRITIQ_DEMO_ENABLED=false`. Behind the deployment's Caddy proxy, the client address is the one Caddy observed; Caddy discards a client-supplied `X-Forwarded-For`.
- The deployed containers run as non-root, with every Linux capability dropped and `no-new-privileges`. Only the reverse proxy publishes ports; the app and Postgres are reachable only on the private Compose network. See [deployment](deployment.md).
- Invalid `ACCELERATION` configuration fails explicitly.
