# Deployment

PRCritiq's public demo ran on one small Hetzner Cloud server with Docker Compose. Caddy terminated TLS with an automatic Let's Encrypt certificate at `prcritiq.arefin.app`, the app ran behind it, and Postgres kept the run store on a local volume. ADR-019 records the choice, which replaced the earlier Modal plan. The deployment was publicly verified in September 2026 and then retired to stop continuous billing. **The URL is not currently a live demo.** The stack remains reproducible for a requested demonstration.

## Reactivate the demo

Reactivation requires a **new** server, not just a DNS switch: the retired server and its local Postgres volume are gone. Follow the setup, deploy, and verification sections below. The deployment code contains no old server IP. Give `deploy/hetzner/deploy.sh` the new IPv4 address in `PRCRITIQ_HOST`, point the `prcritiq` DNS A record (and optional AAAA record) at the new server before deploying, and update any local SSH alias that still names the old IP. GitHub's repository Website field uses the hostname, not an IP. The App webhook was configured for that hostname during the hosted trial; confirm its current setting before reactivation, because the available GitHub token could not inspect it.

Recreate `/opt/prcritiq/.env` on the new server. Set a new Postgres password; use the existing GitHub App ID and a retained or newly generated private key; make its webhook secret match the server setting, rotating both sides if the old secret was lost. The old run rows return only if a database backup exists. Before accepting deliveries, confirm the retained App is installed only on the intended public repositories. While the demo is offline, any active webhook deliveries will fail rather than create runs.

```text
GitHub App webhook ─┐
demo visitors ──────┼─> Caddy :443 (TLS, headers, 5 MB body cap)
                    │      └─> app :8000 (uvicorn, non-root)
                    │             └─> Postgres :5432 (volume pgdata)
                    └─ only Caddy publishes ports; app and Postgres stay on the Compose network
```

When active, the deployment runs only dry runs. Webhook runs are recorded and never posted, the app's GitHub permissions are read-only so it could not post anyway, and model review for webhook runs is off unless the owner turns it on.

## What it costs

The verified deployment used one shared-vCPU `cx23` server (2 vCPU, 4 GB) with a public IPv4 address. Check Hetzner's current server, IP, and retained-resource prices before reactivating. Webhook runs make no model calls while `PRCRITIQ_WEBHOOK_REVIEW=false`, so no model credit is consumed by ordinary deliveries.

## Files

| Path | Purpose |
| --- | --- |
| `Dockerfile` | Two-stage image: locked dependencies plus the locked `ruff` from the `tools` group, run as uid 10001. |
| `deploy/hetzner/docker-compose.yml` | Postgres, app, and Caddy. Fixed project name so volumes survive releases. |
| `deploy/hetzner/Caddyfile` | TLS site, security headers, request body cap, reverse proxy. |
| `deploy/hetzner/cloud-init.yaml` | First boot: Docker from the Ubuntu archive, key-only SSH, a `deploy` user, `/opt/prcritiq`. |
| `deploy/hetzner/.env.example` | Production configuration template. The filled-in copy lives only on the server. |
| `deploy/hetzner/deploy.sh` | Ships `git archive` of a commit, rebuilds, and checks public health. |

## Setup for a requested live session (owner)

These steps need the owner's accounts. They are a reactivation procedure, not a claim that a server is currently running.

### 1. Create the server

In the Hetzner Cloud Console, in the project you use for public demos:

1. **Firewalls → Create firewall** named `prcritiq` with inbound rules: TCP 22 (restrict the source to your own address if you can), TCP 80, TCP 443, and UDP 443. The Hetzner firewall is the only firewall; cloud-init does not install ufw, because two firewalls with one owner is how people lock themselves out.
2. **Servers → Add server**: any EU location (`fsn1`, `nbg1`, or `hel1`), image **Ubuntu 24.04**, type **cx23**, public IPv4 and IPv6, your SSH key, the `prcritiq` firewall, and name `prcritiq`.
3. Paste the contents of `deploy/hetzner/cloud-init.yaml` into **Cloud config**, then create the server.

After a minute or two, `ssh deploy@<server IPv4>` should work. Cloud-init copies root's authorized key to the `deploy` user.

### 2. Point the domain at it (Porkbun)

In Porkbun's DNS editor for `arefin.app`, add one record:

| Type | Host | Answer | TTL |
| --- | --- | --- | --- |
| A | `prcritiq` | server IPv4 | 600 |

An AAAA record for the IPv6 address is optional. `arefin.app` has a wildcard `*.arefin.app` CNAME to Porkbun's parking page. An explicit record for a name takes precedence over the wildcard, so the A record is enough. Confirm the answer is the server and not the parking page before deploying, because Caddy requests the certificate on first start:

```bash
nslookup prcritiq.arefin.app 1.1.1.1
```

### 3. Reuse or create the GitHub App

The owner retained the `prcritiq-demo` GitHub App for on-request reactivation. If it remains installed, confirm it has only the intended public-repository access, the read-only permissions below, and a webhook URL at `https://prcritiq.arefin.app/webhooks/github`, not the old server IP. If the app or installation no longer exists, create it as follows.

At **GitHub → Settings → Developer settings → GitHub Apps → New GitHub App**:

- **Name**: any globally unique name, such as `PRCritiq Demo`.
- **Homepage URL**: `https://prcritiq.arefin.app`
- **Webhook**: active. URL `https://prcritiq.arefin.app/webhooks/github`. Secret: generate one with `openssl rand -hex 32` and keep it for the server configuration.
- **Repository permissions**: Pull requests *Read-only*, Contents *Read-only*, Metadata *Read-only*. Read-only pull request access means the app cannot post a comment even if the code tried.
- **Subscribe to events**: Pull request.
- **Where can this GitHub App be installed?** Only on this account.

Note the **App ID**, generate a new **private key** if the previous key is unavailable, and install the app only on the intended **public** repositories. The server stores the key on one line, with each line break written as `\n`:

```bash
awk 'NF {sub(/\r/, ""); printf "%s\\n", $0}' prcritiq.private-key.pem
```

### 4. Configure the server

Copy the template up, then fill it in on the server:

```bash
scp deploy/hetzner/.env.example deploy@<server IPv4>:/opt/prcritiq/.env
```

```bash
ssh deploy@<server IPv4> "chmod 600 /opt/prcritiq/.env && nano /opt/prcritiq/.env"
```

Set a fresh `POSTGRES_PASSWORD` (`openssl rand -hex 24`), `GITHUB_APP_ID`, `GITHUB_WEBHOOK_SECRET`, and `GITHUB_PRIVATE_KEY`. The webhook secret must match the retained App's setting; if the old value is unavailable, rotate it in the App and set the new value here. For `GITHUB_TOKEN`, create a fine-grained personal access token with **Public repositories (read-only)** access. The demo reads through it, so a private repository cannot be read at all, whatever the app-level guard says. Leave it blank to read anonymously, which GitHub limits to 60 requests an hour.

Leave the safety switches at their written defaults unless you mean to change them.

## Deploy

From the repository root on your machine, after committing what you want to ship and pointing DNS to the new server:

```bash
PRCRITIQ_HOST=<server IPv4> bash deploy/hetzner/deploy.sh
```

The script ships only the committed revision, which it writes to `/opt/prcritiq/app/REVISION`. It keeps the previous release as `app.prev`, rebuilds, and waits for `https://prcritiq.arefin.app/health`. The first deploy also obtains the certificate, which takes up to a minute. To reproduce the tagged runtime rather than current `main`, pass `v0.1.0` as the ref: `bash deploy/hetzner/deploy.sh v0.1.0` with `PRCRITIQ_HOST` set.

## Verify

```bash
curl -fsS https://prcritiq.arefin.app/health
```

```bash
curl -fsS -X POST https://prcritiq.arefin.app/demo/review -H "content-type: application/json" -d '{"repo": "https://github.com/octocat/Hello-World", "pr": 1}'
```

Then open or push to a pull request in a repository where the app is installed. The app's **Advanced** tab lists the delivery with a `200` response whose body names a `run_id`. The run's state is public:

```bash
curl -fsS https://prcritiq.arefin.app/runs/<run_id>
```

A redelivery from that tab returns the same `run_id` with "it was not run again", which is the idempotency AC1 asks for.

## Public surface

| Endpoint | Exposure | Controls |
| --- | --- | --- |
| `GET /health` | Public | Static metadata only. |
| `POST /demo/review` | Public, unauthenticated | Dry run without model calls; public repositories only; 6 requests a minute per client address (`PRCRITIQ_DEMO_REQUESTS_PER_MINUTE`); `PRCRITIQ_DEMO_ENABLED=false` removes it. |
| `POST /webhooks/github` | Public, signed | HMAC signature required; private repositories ignored; a run is recorded, executed once, and never posted. |
| `GET /runs/{id}` | Public | Status, summary, and counts. No finding text, diff, or code. |
| `/docs`, `/openapi.json` | Public | FastAPI's generated API reference. |

The rate limit lives in process memory, which suits the single app container this deployment runs. A restart clears it.

## Operate

On the server, from `/opt/prcritiq/app/deploy/hetzner`:

```bash
docker compose ps
```

```bash
docker compose logs --tail 100 app
```

Back up the run store:

```bash
docker compose exec -T postgres pg_dump -U prcritiq prcritiq | gzip > ~/prcritiq-$(date +%F).sql.gz
```

Roll back to the previous release:

```bash
cd /opt/prcritiq && mv app app.bad && mv app.prev app && cd app/deploy/hetzner && docker compose up -d --build
```

Ubuntu security updates install automatically through `unattended-upgrades`. For newer Postgres and Caddy images, run `docker compose pull` and then `docker compose up -d`.

A webhook run interrupted by a restart stays `pending` or `running`, and a redelivery reports it rather than retrying it. That is a stated limitation of the single-process design. The fix is a worker that reclaims stale runs, and it is not needed for a demo.

## Retire a future live session

Order matters. The server's IPv4 address returns to Hetzner's pool when the server is deleted and can be assigned to a stranger. Until the DNS record is gone, `prcritiq.arefin.app` would point at their machine, and anyone connecting would get a host-key warning from a server they do not own.

1. Delete the `prcritiq` A (and AAAA) record at Porkbun. Wait for the TTL to expire.
2. Either pause webhook delivery or uninstall the GitHub App. If retaining the App for another demonstration, keep its installation scoped to the intended public repositories; active deliveries fail while the endpoint is offline.
3. Delete the server, then the `prcritiq` firewall, in the Hetzner Console.
4. Remove the old host key locally: `ssh-keygen -R <server IPv4>`.
