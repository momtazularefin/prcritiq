# Portfolio Release Checklist

## Release Scope

The project owner decided the v0.1.0 scope on 2026-09-22:

- **Hosting is required.** The public demo runs on one small Hetzner node with Docker Compose, Caddy TLS at `prcritiq.arefin.app`, and Postgres on the node. ADR-019 records this as the approved equivalent to the original Modal and managed-Postgres plan. See [deployment](deployment.md).
- **Webhook execution is in scope, dry-run only.** A GitHub App `pull_request` event creates a persisted, idempotent review run and records its report. It never posts.
- **The benchmark ships with a documented deferral.** The retained smoke and verifier evidence is published as it is. AC11 and AC12 are stated as unmet, and no further paid runs are part of the release.

This is a bounded closeout, not another research cycle. A deadline or a demo does not make unmet original criteria pass.

## Implemented Portfolio Capabilities

- Diff-aware intake, changed-line guardrails, bounded repository retrieval, and allowlisted static checks.
- A visible LangGraph review workflow with typed findings, deterministic suppression, and Markdown and JSON reporting.
- Postgres run persistence, optional tracing, and explicit opt-in GitHub posting from the CLI, with documented idempotency limits.
- GitHub App webhook execution: signed events become idempotent dry-run review runs, read through installation tokens, with public run status.
- A public demo endpoint that is rate limited, refuses private repositories, calls no model, and posts nothing.
- A non-root container image and a Hetzner Compose stack with Caddy TLS, rehearsed locally end to end.
- Offline fixture evaluation and separate saved-candidate verification preparation with no model calls.
- The retained [four-way drafting smoke](../eval/runs/2026-09-18-certified-smoke/comparison.md) and [frozen verifier comparison](../eval/runs/2026-09-20-verifier-comparison/comparison.md), including costs, latency, source provenance, and known false or partial claims.
- Tests, Ruff checks, a CI job named `test` with real Postgres and mocked model calls, a dependency lockfile, and an MIT license.

## Closeout Evidence

- [x] Project owner decided the hosting question and approved the equivalent deployment (ADR-019).
- [x] Verifier v2 refinement finished and validated. Its `partial` verdict and mandatory evidence fields are not presented as measured accuracy gains or automatic publication approval.
- [x] Webhook execution implemented and tested, including redelivery, private-repository refusal, moved-head skip, and failure recording, with and without Postgres.
- [x] Container image built, and the full Compose stack rehearsed locally through Caddy: health, signed delivery, background execution to `summarized`, redelivery returning the same run, a bad signature refused with 401, and the demo limited on the third request with `Retry-After`.
- [x] README and public docs agree about the demo, the webhook, paid flags, experimental verification, and the deployment.
- [x] Remote CI job `test` passed on `main` at `9999b65`, the last pushed revision before this release work.
- [x] Owner committed and pushed the release work as `e645b44` and `678da08`; CI `test` passed on `678da08`.
- [x] Owner provisioned the `cx23` server, the Porkbun A record, and the GitHub App `prcritiq-demo` (read-only contents, metadata, and pull requests; `pull_request` event; installed on this public repository). The server `.env` was filled and each credential checked against GitHub without printing it.
- [x] Deployed `678da08` on 2026-09-22. Public evidence: `/health` answers over HTTPS with a Let's Encrypt certificate, HSTS, and an HTTP-to-HTTPS redirect; a demo review of `octocat/Hello-World#1` returned a dry-run report with nothing posted; a signed delivery through the public URL minted a real installation token, became run 1, finished `summarized`, and its redelivery returned run 1 without running it again.
- [ ] Record one GitHub-originated `pull_request` delivery and one redelivery from the app's Advanced tab.
- [ ] Owner sets branch protection to require `test`, adds repository topics, and tags `v0.1.0`.
- [ ] Record the release revision and its evidence here.

## Original Acceptance Criteria Not Fully Met

| Criterion | Current evidence | Treatment |
| --- | --- | --- |
| AC1: GitHub App PR events create idempotent review runs | Implemented, tested, and exercised on the deployed app with a signed delivery using the real installation. | Met once one GitHub-originated `pull_request` delivery is recorded. |
| AC11: certified evaluation across at least 20 real code-heavy PRs | The legacy 20-PR/46-comment corpus is diagnostic, not certified ground truth. The approved smoke set contains 3 PRs and 5 defects. | Deferred. The smoke evidence and its limits are published; the original corpus requirement is not claimed. |
| AC12: meaningful-issue recall above 50% with precision and spam gates passing | The small smoke comparisons, mechanical matching, and AI evidence audits do not establish these gates. | Deferred. Not replaced by mock scores or verifier confirmations. |
| AC14: public demo on Modal and managed Postgres, or an approved equivalent | Live at `https://prcritiq.arefin.app` since 2026-09-22 (ADR-019). | Met. |
| AC15: passing CI and release hygiene | CI passes; license, badges, and lockfile are present. | Met once branch protection and the `v0.1.0` tag exist. |

## Accepted Deferrals

- Larger positive and clean-PR human adjudication, and statistically meaningful quality claims (AC11, AC12).
- Further model sweeps, calibrated confidence thresholds, and automatic verifier-based publication. `PRCRITIQ_MIN_PUBLISH_CONFIDENCE` stays at 78, because moving it on evidence from this corpus would be false calibration.
- Higher-recall candidate generation, production candidate-targeted context, and deeper dependency-contract retrieval.
- Posting from webhook runs, reclaiming runs interrupted by a restart, distributed workers, and crash- and concurrency-safe posting reconciliation.

The frozen comparison used the earlier verifier protocol. Protocol v2 asks for concrete introduced-defect or contract evidence and separates partial support, but no live quality improvement has been measured. Historical raw results remain historical evidence. Sol-low remains an experimental baseline, and production routing and posting are unchanged.
