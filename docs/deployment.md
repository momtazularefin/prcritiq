# Deployment

PRCritiq's public demo runs on one small Hetzner Cloud server with Docker Compose. Caddy terminates TLS with an automatic Let's Encrypt certificate at `prcritiq.arefin.app`, the app runs behind it, and Postgres keeps the run store on a local volume. ADR-019 records the choice. It replaces the earlier Modal plan.

```text
GitHub App webhook ─┐
demo visitors ──────┼─> Caddy :443 (TLS, headers, 5 MB body cap)
                    │      └─> app :8000 (uvicorn, non-root)
                    │             └─> Postgres :5432 (volume pgdata)
                    └─ only Caddy publishes ports; app and Postgres stay on the Compose network
```

The deployment only runs dry runs. Webhook runs are recorded and never posted, the app's GitHub permissions are read-only so it could not post anyway, and model review for webhook runs is off unless the owner turns it on.

## What it costs

One shared-vCPU `cx23` server (2 vCPU, 4 GB; successor to the retired `cx22`) with a public IPv4 address. At the time of writing that is about €4 a month; check Hetzner's current price list. Webhook runs make no model calls while `PRCRITIQ_WEBHOOK_REVIEW=false`, so hosting is the only running cost.

## Files

| Path | Purpose |
| --- | --- |
| `Dockerfile` | Two-stage image: locked dependencies plus the locked `ruff` from the `tools` group, run as uid 10001. |
| `deploy/hetzner/docker-compose.yml` | Postgres, app, and Caddy. Fixed project name so volumes survive releases. |
| `deploy/hetzner/Caddyfile` | TLS site, security headers, request body cap, reverse proxy. |
| `deploy/hetzner/cloud-init.yaml` | First boot: Docker from the Ubuntu archive, key-only SSH, a `deploy` user, `/opt/prcritiq`. |
| `deploy/hetzner/.env.example` | Production configuration template. The filled-in copy lives only on the server. |
| `deploy/hetzner/deploy.sh` | Ships `git archive` of a commit, rebuilds, and checks public health. |

## One-time setup (owner)

These steps need the owner's accounts, so they are written for the owner to run.

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

### 3. Create the GitHub App

At **GitHub → Settings → Developer settings → GitHub Apps → New GitHub App**:

- **Name**: any globally unique name, such as `PRCritiq Demo`.
- **Homepage URL**: `https://prcritiq.arefin.app`
- **Webhook**: active. URL `https://prcritiq.arefin.app/webhooks/github`. Secret: generate one with `openssl rand -hex 32` and keep it for the server configuration.
- **Repository permissions**: Pull requests *Read-only*, Contents *Read-only*, Metadata *Read-only*. Read-only pull request access means the app cannot post a comment even if the code tried.
- **Subscribe to events**: Pull request.
- **Where can this GitHub App be installed?** Only on this account.

After creating it, note the **App ID**, generate a **private key** (a `.pem` download), and install the app on one or more **public** repositories. The server stores the key on one line, with each line break written as `\n`:

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

Set `POSTGRES_PASSWORD` (`openssl rand -hex 24`), `GITHUB_APP_ID`, `GITHUB_WEBHOOK_SECRET`, and `GITHUB_PRIVATE_KEY`. For `GITHUB_TOKEN`, create a fine-grained personal access token with **Public repositories (read-only)** access. The demo reads through it, so a private repository cannot be read at all, whatever the app-level guard says. Leave it blank to read anonymously, which GitHub limits to 60 requests an hour.

Leave the safety switches at their written defaults unless you mean to change them.

## Deploy

From the repository root on your machine, after committing what you want to ship:

```bash
PRCRITIQ_HOST=<server IPv4> bash deploy/hetzner/deploy.sh
```

The script ships only the committed revision, which it writes to `/opt/prcritiq/app/REVISION`. It keeps the previous release as `app.prev`, rebuilds, and waits for `https://prcritiq.arefin.app/health`. The first deploy also obtains the certificate, which takes up to a minute. A later deploy of a specific ref is `deploy.sh <ref>`.

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

## Tear down

Order matters. The server's IPv4 address returns to Hetzner's pool when the server is deleted and can be assigned to a stranger. Until the DNS record is gone, `prcritiq.arefin.app` would point at their machine, and anyone connecting would get a host-key warning from a server they do not own.

1. Delete the `prcritiq` A (and AAAA) record at Porkbun. Wait for the TTL to expire.
2. Uninstall the GitHub App from its repositories, or delete the app.
3. Delete the server, then the `prcritiq` firewall, in the Hetzner Console.
4. Remove the old host key locally: `ssh-keygen -R <server IPv4>`.
