#!/usr/bin/env bash
set -Eeuo pipefail

required=(DB_HOST DB_PORT DB_NAME DB_USER DB_PASSWORD DB_SSLMODE)
for name in "${required[@]}"; do
  if [[ -z "${!name:-}" ]]; then
    printf 'Required environment variable is missing: %s\n' "$name" >&2
    exit 2
  fi
done

if [[ "${INTEGRITY_TEST_CONFIRM:-}" != "YES" ]]; then
  printf 'Refusing to modify a database without INTEGRITY_TEST_CONFIRM=YES.\n' >&2
  exit 2
fi

case "${DB_NAME,,}" in
  *test*|*qa*|*ci*) ;;
  *)
    printf 'DB_NAME must visibly identify a disposable test/qa/ci database.\n' >&2
    exit 2
    ;;
esac

command -v psql >/dev/null 2>&1 || {
  printf 'psql is required on the host running this test.\n' >&2
  exit 127
}

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
package_dir="$(cd -- "$script_dir/.." && pwd)"

export PGPASSWORD="$DB_PASSWORD"
export PGSSLMODE="$DB_SSLMODE"
psql_args=(
  --host "$DB_HOST"
  --port "$DB_PORT"
  --dbname "$DB_NAME"
  --username "$DB_USER"
  --set ON_ERROR_STOP=1
  --no-password
)

is_superuser="$(psql "${psql_args[@]}" --tuples-only --no-align --command \
  "SELECT rolsuper FROM pg_roles WHERE rolname = current_user")"
if [[ "$is_superuser" != "f" ]]; then
  printf 'Integrity tests must run as an application-specific non-superuser owner/migration role.\n' >&2
  exit 2
fi

existing_tables="$(psql "${psql_args[@]}" --tuples-only --no-align --command \
  "SELECT count(*) FROM pg_catalog.pg_tables WHERE schemaname = current_schema()")"
if [[ "$existing_tables" != "0" ]]; then
  printf 'The selected schema is not empty; use a fresh disposable database/schema.\n' >&2
  exit 2
fi

for migration in "$package_dir"/migrations/[0-9][0-9][0-9]_*.sql; do
  psql "${psql_args[@]}" --file "$migration"
done

psql "${psql_args[@]}" --file "$package_dir/tests/postgres_integrity_tests.sql"
psql "${psql_args[@]}" --file "$package_dir/tests/postgres_stage3a3_integrity_tests.sql"
psql "${psql_args[@]}" --file "$package_dir/tests/postgres_stage3b_integrity_tests.sql"
printf 'PostgreSQL referential-integrity QA passed.\n'
