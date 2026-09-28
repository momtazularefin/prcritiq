# Portfolio Release Checklist

## Release Scope

The project owner decided the v0.1.0 scope on 2026-09-22:

- **Hosting was required for verification, not perpetual operation.** The public demo ran on one small Hetzner node with Docker Compose, Caddy TLS at `prcritiq.arefin.app`, and Postgres on the node. ADR-019 records this as the approved equivalent to the original Modal and managed-Postgres plan. After the live evidence was captured, the owner retired the server to stop continuous billing. The endpoint is currently offline and can be [reactivated on request](deployment.md#reactivate-the-demo).
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
- [x] GitHub-originated delivery and redelivery (AC1). On 2026-09-24, opening `momtazularefin/persistentcontext#16` delivered `pull_request.opened` (delivery `492ba730-b853-11f1-967f-5b06a23499a8`); the deployed app answered 200, recorded run 3, and finished it `summarized` in about a second with 8 of 21 files reviewable and nothing posted. A redelivery of the same delivery from the app's Advanced tab 2.5 hours later answered 200 with "already run 3 (summarized); it was not run again", and the run row was untouched.
- [x] Owner added repository topics and the website link `https://prcritiq.arefin.app`, and pushed the annotated tag `v0.1.0` on `d5ca5e9`. CI `test` passed on that commit. It differs from the deployed `678da08` only in `README.md` and this checklist, so the hosted service ran the tagged runtime code.
- [x] After live verification, the owner deleted the Hetzner server and removed the `prcritiq` DNS record to stop continuous billing; the owner reports checking for retained billable resources. The endpoint is offline. The GitHub App installation and About website link are retained for possible on-request reactivation, not evidence of a current service.
- [x] Release notes written: [docs/releases/v0.1.0.md](releases/v0.1.0.md).
- [x] Owner committed and pushed the post-tag README, release notes, evidence images and raw report, and dry-run footer correction as `cc9b31f`. The `v0.1.0` tag remains on tested runtime commit `d5ca5e9`; the release note's image and raw-report links to `main` resolve publicly.
- [x] Owner published the `v0.1.0` GitHub release from those notes.
- [x] Owner re-enabled the `branch-protection` ruleset requiring `test` after the commit and GitHub Release, ending the build-phase direct-to-`main` exception.
- [x] Owner reports restricting the retained `prcritiq-demo` GitHub App installation to PRCritiq only. The current scope could not be independently read with the available GitHub token. Until a new server is deployed, active webhook deliveries will fail.

## Acceptance Criteria Needing Evidence Or Deferral

| Criterion | Current evidence | Treatment |
| --- | --- | --- |
| AC1: GitHub App PR events create idempotent review runs | A real `pull_request.opened` delivery became run 3, and GitHub's redelivery returned run 3 without running it again. | Met. |
| AC11: certified evaluation across at least 20 real code-heavy PRs | The legacy 20-PR/46-comment corpus is diagnostic, not certified ground truth. The approved smoke set contains 3 PRs and 5 defects. | Deferred. The smoke evidence and its limits are published; the original corpus requirement is not claimed. |
| AC12: meaningful-issue recall above 50% with precision and spam gates passing | The small smoke comparisons, mechanical matching, and AI evidence audits do not establish these gates. | Deferred. Not replaced by mock scores or verifier confirmations. |
| AC14: public demo on Modal and managed Postgres, or an approved equivalent | Verified live on the approved Hetzner equivalent from 2026-09-22 through at least 2026-09-27, then retired. | Demonstrated historically; currently offline. |
| AC15: passing CI and release hygiene | CI passes on the `v0.1.0` tag and post-tag `main`; license, badges, lockfile, topics, and the published GitHub Release are present. Branch protection is active. The retained Website link points to the retired demo. | Met. |

## Accepted Deferrals

- Larger positive and clean-PR human adjudication, and statistically meaningful quality claims (AC11, AC12).
- Further model sweeps, calibrated confidence thresholds, and automatic verifier-based publication. `PRCRITIQ_MIN_PUBLISH_CONFIDENCE` stays at 78, because moving it on evidence from this corpus would be false calibration.
- Higher-recall candidate generation, production candidate-targeted context, and deeper dependency-contract retrieval.
- Posting from webhook runs, reclaiming runs interrupted by a restart, distributed workers, and crash- and concurrency-safe posting reconciliation.

The frozen comparison used the earlier verifier protocol. Protocol v2 asks for concrete introduced-defect or contract evidence and separates partial support, but no live quality improvement has been measured. Historical raw results remain historical evidence. Sol-low remains an experimental baseline, and production routing and posting are unchanged.
