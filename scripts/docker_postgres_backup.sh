#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
# The postgres container mounts ./backups as /backups (docker-compose.yml), so the
# host directory is fixed; a custom directory argument could never be honoured.
if [ "$#" -gt 0 ]; then
  echo "usage: $0 (backups are always written to $ROOT/backups)" >&2
  exit 2
fi
BACKUP_DIR="$ROOT/backups"
mkdir -p "$BACKUP_DIR"
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
NAME="universal_supplier_${STAMP}.dump"

cd "$ROOT"
# Dump to a .partial file and rename only after pg_dump succeeded, so an
# interrupted dump never looks like a complete backup.
docker compose exec -T postgres sh -eu -c \
  'umask 077; rm -f "/backups/$1.partial"; pg_dump -U "$APP_DB_USER" -d "$APP_DB_NAME" -Fc -f "/backups/$1.partial"; test -s "/backups/$1.partial"; mv "/backups/$1.partial" "/backups/$1"' sh "$NAME"
test -s "$BACKUP_DIR/$NAME"
printf '%s\n' "$BACKUP_DIR/$NAME"
