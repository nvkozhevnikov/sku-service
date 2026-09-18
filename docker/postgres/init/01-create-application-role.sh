#!/bin/sh
set -eu

: "${APP_DB_NAME:?APP_DB_NAME is required}"
: "${APP_DB_USER:?APP_DB_USER is required}"
: "${APP_DB_PASSWORD:?APP_DB_PASSWORD is required}"

psql --set=ON_ERROR_STOP=1 \
  --username "$POSTGRES_USER" \
  --dbname postgres \
  --set=app_db_name="$APP_DB_NAME" \
  --set=app_db_user="$APP_DB_USER" \
  --set=app_db_password="$APP_DB_PASSWORD" <<'SQL'
SELECT format(
  'CREATE ROLE %I LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION',
  :'app_db_user', :'app_db_password'
) WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'app_db_user') \gexec

SELECT format(
  'ALTER ROLE %I WITH LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION',
  :'app_db_user', :'app_db_password'
) \gexec

SELECT format('CREATE DATABASE %I OWNER %I ENCODING %L', :'app_db_name', :'app_db_user', 'UTF8')
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'app_db_name') \gexec

SELECT format('REVOKE ALL ON DATABASE %I FROM PUBLIC', :'app_db_name') \gexec
SELECT format('GRANT CONNECT, TEMPORARY, CREATE ON DATABASE %I TO %I', :'app_db_name', :'app_db_user') \gexec
SQL

psql --set=ON_ERROR_STOP=1 \
  --username "$POSTGRES_USER" \
  --dbname "$APP_DB_NAME" \
  --set=app_db_user="$APP_DB_USER" <<'SQL'
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
SELECT format('GRANT USAGE, CREATE ON SCHEMA public TO %I', :'app_db_user') \gexec
SQL

