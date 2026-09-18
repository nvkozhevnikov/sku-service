#!/usr/bin/env bash
set -Eeuo pipefail

umask 077

if [[ $# -ne 1 ]]; then
  echo "Usage: RESTORE_CONFIRM=<DB_NAME> $0 /absolute/path/backup.dump" >&2
  exit 2
fi

required_vars=(DB_HOST DB_PORT DB_NAME DB_USER DB_PASSWORD DB_SSLMODE)
for var_name in "${required_vars[@]}"; do
  if [[ -z "${!var_name:-}" ]]; then
    echo "Required environment variable is not set: ${var_name}" >&2
    exit 2
  fi
done

if [[ "${RESTORE_CONFIRM:-}" != "${DB_NAME}" ]]; then
  echo "Restore refused. Set RESTORE_CONFIRM exactly to the target DB_NAME (${DB_NAME})." >&2
  exit 4
fi

command -v pg_restore >/dev/null 2>&1 || { echo "pg_restore is not installed on the host" >&2; exit 3; }
command -v sha256sum >/dev/null 2>&1 || { echo "sha256sum is not installed on the host" >&2; exit 3; }

backup_path="$1"
if [[ "${backup_path}" != /* ]] || [[ ! -f "${backup_path}" ]]; then
  echo "Backup must be an existing absolute file path" >&2
  exit 2
fi

checksum_path="${backup_path}.sha256"
if [[ -f "${checksum_path}" ]]; then
  (cd "$(dirname "${backup_path}")" && sha256sum --check "$(basename "${checksum_path}")")
else
  echo "Warning: checksum file not found: ${checksum_path}" >&2
fi

export PGPASSWORD="${DB_PASSWORD}"
export PGSSLMODE="${DB_SSLMODE}"

# This restores database contents only. PostgreSQL service/database lifecycle
# remains a host-administrator responsibility.
pg_restore \
  --host="${DB_HOST}" \
  --port="${DB_PORT}" \
  --username="${DB_USER}" \
  --dbname="${DB_NAME}" \
  --exit-on-error \
  --clean \
  --if-exists \
  --no-owner \
  --no-acl \
  "${backup_path}"

echo "Restore completed into database: ${DB_NAME}"
