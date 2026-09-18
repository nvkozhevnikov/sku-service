#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
BACKUP_PATH=${1:?usage: docker_postgres_restore_test.sh backups/file.dump}
case "$BACKUP_PATH" in
  "$ROOT"/backups/*) ;;
  backups/*) BACKUP_PATH="$ROOT/$BACKUP_PATH" ;;
  *) echo "dump must be under $ROOT/backups" >&2; exit 2 ;;
esac
test -s "$BACKUP_PATH"
NAME=$(basename -- "$BACKUP_PATH")
RESTORE_DB="universal_supplier_restore_$(date -u +%Y%m%d%H%M%S)"

cd "$ROOT"
cleanup() {
  docker compose exec -T -e RESTORE_DB="$RESTORE_DB" postgres sh -eu -c \
    'dropdb -U "$POSTGRES_USER" --if-exists "$RESTORE_DB"' >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM
docker compose exec -T -e RESTORE_DB="$RESTORE_DB" postgres sh -eu -c \
  'createdb -U "$POSTGRES_USER" "$RESTORE_DB"; pg_restore -U "$POSTGRES_USER" -d "$RESTORE_DB" "/backups/$1"' sh "$NAME"
docker compose exec -T -e RESTORE_DB="$RESTORE_DB" postgres sh -eu -c \
  'psql -U "$POSTGRES_USER" -d "$RESTORE_DB" -At -F, -c "SELECT (SELECT count(*) FROM source_products),(SELECT count(*) FROM offers),(SELECT count(*) FROM product_matches),(SELECT count(*) FROM catalog_products)"'
printf '%s\n' 'PG_RESTORE_TEST = PASS'
