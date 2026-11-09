#!/usr/bin/env bash
# Deploy a CI-green commit to the droplet. Usage: deploy/deploy.sh [git-ref]
# Needs UNITPRICER_DEPLOY_HOST (e.g. deploy@203.0.113.7), gh, git, ssh.
# UNITPRICER_BUILD_ON_SERVER=1 builds the app images on the droplet instead of
# pulling them from GHCR (for private packages).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

REF="${1:-origin/main}"
HOST="${UNITPRICER_DEPLOY_HOST:-}"
if [ -z "$HOST" ]; then
  echo "UNITPRICER_DEPLOY_HOST is not set (e.g. deploy@<droplet-ip>)" >&2
  exit 2
fi
KEY="${UNITPRICER_DEPLOY_KEY:-$HOME/.ssh/unitpricer_deploy}"
SSH_OPTS=(-i "$KEY" -o IdentitiesOnly=yes)
REPO_URL="https://github.com/DanteArcese/unitpricer.git"

git fetch --quiet origin
SHA="$(git rev-parse --verify "${REF}^{commit}")"

GREEN="$(gh run list --commit "$SHA" --workflow CI --branch main --json conclusion,event \
  --jq '[.[] | select(.event == "push" and .conclusion == "success")] | length')"
if [ "${GREEN:-0}" -lt 1 ]; then
  echo "Refusing to deploy $SHA: no successful CI push run on main." >&2
  exit 1
fi

# Use the compose file from the commit being deployed (matters for rollback).
git show "$SHA:compose.prod.yaml" | ssh "${SSH_OPTS[@]}" "$HOST" 'cat > /srv/unitpricer/compose.yaml'

if [ "${UNITPRICER_BUILD_ON_SERVER:-0}" = 1 ]; then
  FETCH="docker build -t ghcr.io/dantearcese/unitpricer:sha-$SHA \"$REPO_URL#$SHA\"
"
  if git cat-file -e "$SHA:deploy/backup/Dockerfile" 2>/dev/null; then
    FETCH+="docker build -t ghcr.io/dantearcese/unitpricer-backup:sha-$SHA \"$REPO_URL#$SHA:deploy/backup\"
"
  else
    echo "Note: deploy/backup/Dockerfile not in $SHA; skipping backup image build." >&2
  fi
  FETCH+="docker compose pull db cloudflared"
else
  FETCH="docker compose pull"
fi

read -r -d '' SCRIPT <<REMOTE || true
set -euo pipefail
umask 077
cd /srv/unitpricer
touch .env
{ grep -v '^UNITPRICER_IMAGE_TAG=' .env || true; echo 'UNITPRICER_IMAGE_TAG=sha-$SHA'; } > .env.new
mv .env.new .env
$FETCH
# Settings and system checks (E023 etc.) on the new image; set -e aborts before migrate.
docker compose run --rm -T web python manage.py check --deploy --fail-level ERROR </dev/null
docker compose run --rm -T web python manage.py migrate --noinput </dev/null
# Category YAML ships in the image; new and changed categories reach the database here.
docker compose run --rm -T web python manage.py sync_categories </dev/null
docker compose run --rm -T web python manage.py createcachetable </dev/null
docker compose up -d --remove-orphans
status=
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

# The script travels as an argument: docker compose run would eat stdin.
ssh "${SSH_OPTS[@]}" "$HOST" "bash -c $(printf %q "$SCRIPT")" </dev/null

echo "Deployed $SHA"
