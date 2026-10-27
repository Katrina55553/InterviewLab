#!/usr/bin/env bash
set -euo pipefail
umask 077

BACKUP="${1:?private backup directory required}"
BACKEND_IMAGE="${2:?tested backend image required}"
POSTGRES_IMAGE="${3:-postgres:16-alpine}"
BACKUP="$(cd "$BACKUP" && pwd -P)"
SCRIPTS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
BACKUP_MOUNT="$BACKUP"
# Git Bash drives Docker Desktop for local rehearsals; retain container paths.
if [[ "$OSTYPE" == msys* ]]; then
  BACKUP_MOUNT="$(cygpath -m "$BACKUP")"
  export MSYS_NO_PATHCONV=1
fi
[[ ! -e "$BACKUP/.incomplete" ]] || { echo 'Incomplete backup refused' >&2; exit 1; }
for file in database.dump database-counts.tsv database-schema.sha256 uploads.tar.gz uploads-files.json backend.env SHA256SUMS; do
  [[ -s "$BACKUP/$file" ]] || { echo "Missing backup component: $file" >&2; exit 1; }
done
for file in database.dump database-counts.tsv database-schema.sha256 uploads.tar.gz uploads-files.json backend.env; do
  awk -v wanted="$file" '$1 ~ /^[0-9a-f]+$/ && length($1) == 64 && substr($0, 67) == wanted {found=1} END {exit !found}' "$BACKUP/SHA256SUMS" \
    || { echo "Backup checksum missing or invalid: $file" >&2; exit 1; }
done
(cd "$BACKUP" && sha256sum -c SHA256SUMS)
# Never attach production networks/volumes or expose a port. All names are unique.
TOKEN="$(date -u +%Y%m%d%H%M%S)-$$-$RANDOM"
NAME="resumeagent-restore-$TOKEN"
DB_VOLUME="$NAME-pg"
UPLOAD_VOLUME="$NAME-uploads"
CREATED_CONTAINER=false
CREATED_DB=false
CREATED_UPLOAD=false
cleanup() {
  result=$?
  trap - EXIT
  # Remove only resources whose creation completed in this invocation.
  if [[ "$CREATED_CONTAINER" == true ]]; then docker rm -f "$NAME" >/dev/null || result=1; fi
  if [[ "$CREATED_UPLOAD" == true ]]; then docker volume rm "$UPLOAD_VOLUME" >/dev/null || result=1; fi
  if [[ "$CREATED_DB" == true ]]; then docker volume rm "$DB_VOLUME" >/dev/null || result=1; fi
  exit "$result"
}
trap cleanup EXIT
docker volume create --label resumeagent.restore-drill=true "$DB_VOLUME" >/dev/null
CREATED_DB=true
docker volume create --label resumeagent.restore-drill=true "$UPLOAD_VOLUME" >/dev/null
CREATED_UPLOAD=true
docker run -d --name "$NAME" --label resumeagent.restore-drill=true --network none \
  -e POSTGRES_PASSWORD=disposable-restore-only -e POSTGRES_DB=resume_agent \
  --mount "type=volume,src=$DB_VOLUME,dst=/var/lib/postgresql/data" "$POSTGRES_IMAGE" >/dev/null
CREATED_CONTAINER=true
ready=false
for ((attempt=0; attempt<60; attempt++)); do
  if docker exec "$NAME" pg_isready -U postgres -d resume_agent >/dev/null 2>&1; then ready=true; break; fi
  sleep 1
done
[[ "$ready" == true ]] || { echo 'Isolated restore PostgreSQL did not become ready' >&2; exit 1; }
docker exec -i "$NAME" pg_restore --exit-on-error --no-owner --no-privileges -U postgres -d resume_agent < "$BACKUP/database.dump"
RESTORED_COUNTS="$(docker exec -i "$NAME" psql -X -v ON_ERROR_STOP=1 -U postgres -d resume_agent -At -F $'\t' < "$SCRIPTS/backup-inventory.sql")"
[[ "$RESTORED_COUNTS" == "$(cat "$BACKUP/database-counts.tsv")" ]] || { echo 'Restored row counts or migration heads differ' >&2; exit 1; }
RESTORED_SCHEMA="$(docker exec "$NAME" pg_dump -U postgres --schema-only --no-owner --no-privileges resume_agent \
  | sed '/^--/d; /^$/d; /^\\restrict /d; /^\\unrestrict /d' | sha256sum | cut -d ' ' -f1)"
[[ "$RESTORED_SCHEMA" == "$(cat "$BACKUP/database-schema.sha256")" ]] || { echo 'Restored database schema differs' >&2; exit 1; }
docker run --rm -i --network none \
  --mount "type=bind,src=$BACKUP_MOUNT,dst=/backup,readonly" \
  --mount "type=volume,src=$UPLOAD_VOLUME,dst=/restore" \
  --entrypoint python "$BACKEND_IMAGE" - restore /restore /backup/uploads.tar.gz /backup/uploads-files.json /backup/backend.env \
  < "$SCRIPTS/backup-files.py"
# Only ciphertext/configuration crosses this pipe; plaintext is never emitted.
docker exec -i "$NAME" psql -X -v ON_ERROR_STOP=1 -U postgres -d resume_agent -At \
  < "$SCRIPTS/backup-credentials.sql" \
  | docker run --rm -i --network none --mount "type=bind,src=$BACKUP_MOUNT,dst=/backup,readonly" \
    --entrypoint python "$BACKEND_IMAGE" -c "$(cat "$SCRIPTS/backup-files.py")" credentials /backup /backup/backend.env
echo 'Isolated restore passed: schema, migration heads, row counts, uploads, private configuration and encrypted credentials'
