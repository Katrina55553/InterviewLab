#!/usr/bin/env bash
set -euo pipefail
umask 077

# Uploads are release artifacts from the successful CI run, never a floating branch.
SHA="${1:?commit SHA required}"
DEPLOY_PATH="${2:-/root/resume-agent}"
[[ "$SHA" =~ ^[0-9a-f]{40}$ ]] || { echo 'Invalid release SHA' >&2; exit 1; }
[[ -f "$DEPLOY_PATH/backend/.env" ]] || { echo 'Production backend/.env is required' >&2; exit 1; }
DEPLOY_PATH="$(cd "$DEPLOY_PATH" && pwd -P)"
exec 9>"$DEPLOY_PATH/.release.lock"
flock -n 9 || { echo 'Another release operation is running' >&2; exit 1; }
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
trap '"${COMPOSE[@]}" ps; echo "Release failed; writers may remain stopped. Inspect the private service logs and backup before recovery." >&2; exit 1' ERR
# Single-host maintenance window: stop writers before migrating their schema.
"${COMPOSE[@]}" stop frontend backend celery celery-report celery-beat outbox inspector metrics
# A stopped database can still contain existing data: start it before every backup.
"${COMPOSE[@]}" up -d --no-build --wait --wait-timeout 120 postgres redis
"${COMPOSE[@]}" exec -T postgres pg_isready -U postgres
BACKUP="$(mktemp -d "$DEPLOY_PATH/backups/$(date -u +%Y%m%dT%H%M%SZ)-$SHA-XXXXXX")"
touch "$BACKUP/.incomplete"
"${COMPOSE[@]}" exec -T postgres pg_dump -U postgres -Fc resume_agent > "$BACKUP/database.dump"
"${COMPOSE[@]}" exec -T postgres psql -X -v ON_ERROR_STOP=1 -U postgres -d resume_agent -At -F $'\t' \
  < "$RELEASE/scripts/backup-inventory.sql" > "$BACKUP/database-counts.tsv"
"${COMPOSE[@]}" exec -T postgres pg_dump -U postgres --schema-only --no-owner --no-privileges resume_agent \
  | sed '/^--/d; /^$/d; /^\\restrict /d; /^\\unrestrict /d' | sha256sum | cut -d ' ' -f1 > "$BACKUP/database-schema.sha256"
"${COMPOSE[@]}" run --rm -T --no-deps --entrypoint python backend - archive /app/uploads \
  < "$RELEASE/scripts/backup-files.py" > "$BACKUP/uploads.tar.gz"
"${COMPOSE[@]}" run --rm -T --no-deps --entrypoint python backend - manifest /app/uploads \
  < "$RELEASE/scripts/backup-files.py" > "$BACKUP/uploads-files.json"
cp "$DEPLOY_PATH/backend/.env" "$BACKUP/backend.env"
if [[ -f "$DEPLOY_PATH/current-release.env" ]]; then
  cp "$DEPLOY_PATH/current-release.env" "$BACKUP/source-release.env"
fi
(cd "$BACKUP" && sha256sum database.dump database-counts.tsv database-schema.sha256 uploads.tar.gz uploads-files.json backend.env > SHA256SUMS)
[[ ! -f "$BACKUP/source-release.env" ]] || (cd "$BACKUP" && sha256sum source-release.env >> SHA256SUMS)
rm "$BACKUP/.incomplete"
echo "Complete private backup: $BACKUP"
"${COMPOSE[@]}" run --rm migrate
"${COMPOSE[@]}" up -d --no-build --wait --wait-timeout 180
"${COMPOSE[@]}" exec -T backend python -m app.platform.cli probe-jobs
if [[ -f "$DEPLOY_PATH/current-release.env" ]]; then
  cp "$DEPLOY_PATH/current-release.env" "$DEPLOY_PATH/previous-release.env"
fi
printf 'BACKEND_IMAGE=%s\nFRONTEND_IMAGE=%s\nRELEASE=%s\n' "$BACKEND_IMAGE" "$FRONTEND_IMAGE" "$RELEASE" > "$DEPLOY_PATH/current-release.env.tmp"
mv "$DEPLOY_PATH/current-release.env.tmp" "$DEPLOY_PATH/current-release.env"
echo "Release $SHA passed web, component and durable task gates"
