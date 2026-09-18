#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
BACKUP_DIR=${1:-"$ROOT/backups"}
mkdir -p "$BACKUP_DIR"
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
NAME="universal_supplier_${STAMP}.dump"

cd "$ROOT"
docker compose exec -T postgres sh -eu -c \
  'umask 077; pg_dump -U "$APP_DB_USER" -d "$APP_DB_NAME" -Fc -f "/backups/$1"' sh "$NAME"
test -s "$BACKUP_DIR/$NAME"
printf '%s\n' "$BACKUP_DIR/$NAME"

