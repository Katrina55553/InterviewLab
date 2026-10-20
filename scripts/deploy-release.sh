#!/usr/bin/env bash
set -euo pipefail

# Uploads are release artifacts from the successful CI run, never a floating branch.
SHA="${1:?commit SHA required}"
DEPLOY_PATH="${2:-/root/resume-agent}"
[[ "$SHA" =~ ^[0-9a-f]{40}$ ]] || { echo 'Invalid release SHA' >&2; exit 1; }
[[ -f "$DEPLOY_PATH/backend/.env" ]] || { echo 'Production backend/.env is required' >&2; exit 1; }
RELEASE="$DEPLOY_PATH/releases/$SHA"
cd "$RELEASE"
sha256sum -c SHA256SUMS
docker load -i backend.tar.gz
docker load -i frontend.tar.gz
export BACKEND_IMAGE="resume-agent-backend:$SHA"
export FRONTEND_IMAGE="resume-agent-frontend:$SHA"
mkdir -p backend "$DEPLOY_PATH/backups"
ln -sfn "$DEPLOY_PATH/backend/.env" backend/.env
COMPOSE=(docker compose -p resumeagent -f "$RELEASE/docker-compose.yml")
"${COMPOSE[@]}" config --quiet
"${COMPOSE[@]}" run --rm --no-deps backend python -m app.platform.cli check-config
trap '"${COMPOSE[@]}" ps; "${COMPOSE[@]}" logs --tail=100 backend celery celery-report outbox inspector; exit 1' ERR
# Single-host maintenance window: stop writers before migrating their schema.
"${COMPOSE[@]}" stop frontend backend celery celery-report outbox inspector metrics
# Back up the existing database before the additive migration. Keep prior images/schema.
if "${COMPOSE[@]}" ps --status running postgres -q | grep -q .; then
  "${COMPOSE[@]}" exec -T postgres \
    pg_dump -U postgres -Fc resume_agent > "$DEPLOY_PATH/backups/$SHA.dump"
fi
"${COMPOSE[@]}" up -d --no-build postgres redis
"${COMPOSE[@]}" run --rm migrate
"${COMPOSE[@]}" up -d --no-build --wait --wait-timeout 180
"${COMPOSE[@]}" exec -T backend python -m app.platform.cli probe-jobs
if [[ -f "$DEPLOY_PATH/current-release.env" ]]; then
  cp "$DEPLOY_PATH/current-release.env" "$DEPLOY_PATH/previous-release.env"
fi
printf 'BACKEND_IMAGE=%s\nFRONTEND_IMAGE=%s\nRELEASE=%s\n' "$BACKEND_IMAGE" "$FRONTEND_IMAGE" "$RELEASE" > "$DEPLOY_PATH/current-release.env"
echo "Release $SHA passed web, component and durable task gates"
