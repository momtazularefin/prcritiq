#!/usr/bin/env bash
# Deploy a committed PRCritiq revision to the Hetzner node (ADR-019).
#
#   PRCRITIQ_HOST=<server IPv4> deploy/hetzner/deploy.sh [git-ref]
#
# Ships `git archive` of the ref (default HEAD), never the working tree, so
# what runs is exactly a commit and nothing uncommitted or ignored, .env above
# all, can leave this machine. The previous release is kept as app.prev for a
# manual rollback.
#
# Optional: PRCRITIQ_SSH_USER (default deploy), PRCRITIQ_DOMAIN (default
# prcritiq.arefin.app).

set -euo pipefail

host="${PRCRITIQ_HOST:?Set PRCRITIQ_HOST to the server IPv4 address}"
user="${PRCRITIQ_SSH_USER:-deploy}"
domain="${PRCRITIQ_DOMAIN:-prcritiq.arefin.app}"
ref="${1:-HEAD}"

repo_root="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
commit="$(git -C "$repo_root" rev-parse --verify "$ref^{commit}")"

if [ "$ref" = "HEAD" ] && [ -n "$(git -C "$repo_root" status --porcelain)" ]; then
  echo "Note: the working tree has uncommitted changes; they are not deployed." >&2
fi

archive="$(mktemp)"
trap 'rm -f "$archive"' EXIT
git -C "$repo_root" archive --format=tar.gz -o "$archive" "$commit"

echo "Deploying ${commit:0:12} to ${user}@${host}"
scp -q "$archive" "${user}@${host}:/opt/prcritiq/release.tar.gz"

ssh "${user}@${host}" COMMIT="$commit" bash -s <<'REMOTE'
set -euo pipefail
cd /opt/prcritiq
if [ ! -f .env ]; then
  echo "Missing /opt/prcritiq/.env; copy deploy/hetzner/.env.example there and fill it in." >&2
  exit 1
fi
rm -rf app.next
mkdir app.next
tar -xzf release.tar.gz -C app.next
rm -f release.tar.gz
echo "$COMMIT" > app.next/REVISION
ln -s /opt/prcritiq/.env app.next/deploy/hetzner/.env

rm -rf app.prev
if [ -d app ]; then mv app app.prev; fi
mv app.next app

cd app/deploy/hetzner
docker compose up -d --build --remove-orphans
docker image prune -f >/dev/null
docker compose ps
REMOTE

echo "Waiting for https://${domain}/health (the first deploy also issues the certificate)"
if curl -fsS --retry 20 --retry-delay 6 --retry-all-errors --max-time 10 \
  "https://${domain}/health"; then
  echo
  echo "Deployed ${commit:0:12}."
else
  echo "Health check failed. On the server: cd /opt/prcritiq/app/deploy/hetzner && docker compose logs" >&2
  exit 1
fi
