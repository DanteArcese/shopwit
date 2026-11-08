#!/usr/bin/env bash
# Deploy a CI-green commit to the droplet. Usage: deploy/deploy.sh [git-ref]
# Needs UNITPRICER_DEPLOY_HOST (e.g. deploy@203.0.113.7), gh, git, ssh, scp.
set -euo pipefail

REF="${1:-origin/main}"
HOST="${UNITPRICER_DEPLOY_HOST:-}"
if [ -z "$HOST" ]; then
  echo "UNITPRICER_DEPLOY_HOST is not set (e.g. deploy@<droplet-ip>)" >&2
  exit 2
fi
KEY="${UNITPRICER_DEPLOY_KEY:-$HOME/.ssh/unitpricer_deploy}"
SSH_OPTS=(-i "$KEY" -o IdentitiesOnly=yes)
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

SHA="$(git rev-parse --verify "${REF}^{commit}")"

GREEN="$(gh run list --commit "$SHA" --workflow CI --json conclusion,event \
  --jq '[.[] | select(.event == "push" and .conclusion == "success")] | length')"
if [ "${GREEN:-0}" -lt 1 ]; then
  echo "Refusing to deploy $SHA: no successful CI push run (the sha- image may not exist)." >&2
  exit 1
fi

scp "${SSH_OPTS[@]}" "$ROOT/compose.prod.yaml" "$HOST:/srv/unitpricer/compose.yaml"

ssh "${SSH_OPTS[@]}" "$HOST" bash -s <<REMOTE
set -euo pipefail
cd /srv/unitpricer
touch .env
{ grep -v '^UNITPRICER_IMAGE_TAG=' .env || true; echo 'UNITPRICER_IMAGE_TAG=sha-$SHA'; } > .env.new
chmod 600 .env.new
mv .env.new .env
docker compose pull
docker compose run --rm web python manage.py migrate --noinput
docker compose run --rm web python manage.py createcachetable
docker compose up -d --remove-orphans
for _ in \$(seq 1 18); do
  status="\$(docker inspect --format '{{.State.Health.Status}}' "\$(docker compose ps -q web)" 2>/dev/null || true)"
  [ "\$status" = healthy ] && break
  sleep 5
done
if [ "\$status" != healthy ]; then
  docker compose logs --tail 100 web
  echo "web did not become healthy within 90 s" >&2
  exit 1
fi
docker image prune -f
REMOTE

echo "Deployed $SHA"
