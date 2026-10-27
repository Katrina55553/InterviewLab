#!/usr/bin/env bash
set -euo pipefail
umask 077

SHA="${1:?previous tested commit SHA required}"
DEPLOY_PATH="${2:-/root/resume-agent}"
[[ "$SHA" =~ ^[0-9a-f]{40}$ ]] || { echo 'Invalid release SHA' >&2; exit 1; }
DEPLOY_PATH="$(cd "$DEPLOY_PATH" && pwd -P)"
exec 9>"$DEPLOY_PATH/.release.lock"
flock -n 9 || { echo 'Another release operation is running' >&2; exit 1; }
RELEASE="$DEPLOY_PATH/releases/$SHA"
[[ -f "$RELEASE/docker-compose.yml" && -f "$DEPLOY_PATH/backend/.env" ]] || exit 1
cd "$RELEASE"
sha256sum -c SHA256SUMS
docker load -i backend.tar.gz
docker load -i frontend.tar.gz
export BACKEND_IMAGE="resume-agent-backend:$SHA"
export FRONTEND_IMAGE="resume-agent-frontend:$SHA"
mkdir -p backend
ln -sfn "$DEPLOY_PATH/backend/.env" backend/.env
COMPOSE=(docker compose -p resumeagent -f "$RELEASE/docker-compose.yml")
"${COMPOSE[@]}" config --quiet
"${COMPOSE[@]}" run --rm --no-deps backend python -m app.platform.cli check-config
# Validate the old application's actual schema compatibility before replacing anything.
# A historical image that hard-codes an older head must fail here; never downgrade PG.
"${COMPOSE[@]}" run --rm --no-deps backend python -c \
  'import asyncio; from app.core.database import init_db; asyncio.run(init_db())'
trap '"${COMPOSE[@]}" ps; echo "Rollback failed; inspect the private service logs before recovery." >&2; exit 1' ERR
"${COMPOSE[@]}" stop frontend backend celery celery-report celery-beat outbox inspector metrics
"${COMPOSE[@]}" up -d --no-deps --no-build --wait --wait-timeout 180 backend celery celery-report celery-beat outbox inspector metrics frontend
"${COMPOSE[@]}" exec -T backend python -m app.platform.cli probe-jobs
printf 'BACKEND_IMAGE=%s\nFRONTEND_IMAGE=%s\nRELEASE=%s\n' "$BACKEND_IMAGE" "$FRONTEND_IMAGE" "$RELEASE" > "$DEPLOY_PATH/current-release.env.tmp"
mv "$DEPLOY_PATH/current-release.env.tmp" "$DEPLOY_PATH/current-release.env"
echo "Application rollback to $SHA passed; database schema retained"
