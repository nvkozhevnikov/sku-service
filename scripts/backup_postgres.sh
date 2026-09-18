#!/usr/bin/env bash
set -Eeuo pipefail

umask 077

required_vars=(DB_HOST DB_PORT DB_NAME DB_USER DB_PASSWORD DB_SSLMODE)
for var_name in "${required_vars[@]}"; do
  if [[ -z "${!var_name:-}" ]]; then
    echo "Required environment variable is not set: ${var_name}" >&2
    exit 2
  fi
done

command -v pg_dump >/dev/null 2>&1 || { echo "pg_dump is not installed on the host" >&2; exit 3; }
command -v sha256sum >/dev/null 2>&1 || { echo "sha256sum is not installed on the host" >&2; exit 3; }

backup_dir="${BACKUP_DIR:-/var/backups/supplier-catalog}"
retention_days="${BACKUP_RETENTION_DAYS:-14}"

if [[ ! "${retention_days}" =~ ^[0-9]+$ ]] || (( retention_days < 1 )); then
  echo "BACKUP_RETENTION_DAYS must be a positive integer" >&2
  exit 2
fi

install -d -m 0700 -- "${backup_dir}"

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
safe_db_name="$(printf '%s' "${DB_NAME}" | tr -c 'A-Za-z0-9_.-' '_')"
final_path="${backup_dir}/${safe_db_name}_${timestamp}.dump"
temporary_path="${final_path}.partial"

cleanup() {
  rm -f -- "${temporary_path}"
}
trap cleanup EXIT

export PGPASSWORD="${DB_PASSWORD}"
export PGSSLMODE="${DB_SSLMODE}"

pg_dump \
  --host="${DB_HOST}" \
  --port="${DB_PORT}" \
  --username="${DB_USER}" \
  --dbname="${DB_NAME}" \
  --format=custom \
  --compress=9 \
  --no-owner \
  --no-acl \
  --file="${temporary_path}"

chmod 0600 "${temporary_path}"
mv -- "${temporary_path}" "${final_path}"
(cd "${backup_dir}" && sha256sum -- "$(basename "${final_path}")" > "$(basename "${final_path}").sha256")
chmod 0600 "${final_path}.sha256"

find "${backup_dir}" -maxdepth 1 -type f \
  \( -name "${safe_db_name}_*.dump" -o -name "${safe_db_name}_*.dump.sha256" \) \
  -mtime "+${retention_days}" -delete

echo "Backup created: ${final_path}"
