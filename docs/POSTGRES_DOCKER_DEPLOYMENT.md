# Docker PostgreSQL deployment

## First start

1. Copy `.env.example` to `.env` and replace both `CHANGE_ME_*` password placeholders with different strong local secrets. Never commit or package `.env`.
2. Run `docker compose config` and confirm the rendered port starts with `127.0.0.1:`.
3. Run `docker compose up -d postgres`.
4. Run `docker compose ps`; PostgreSQL must become `healthy`.
5. Set the same `DB_*` values in the host shell and set `STAGE3D_DB_CONFIRM=YES`.
6. Run `python scripts/docker_postgres_migrate.py`, then `python scripts/run_stage3d_docker_verification.py`.

Migrations are applied by the application role, which owns only its dedicated database and is explicitly `NOSUPERUSER NOCREATEDB NOCREATEROLE`. The Docker bootstrap/admin role is never the runtime application user.

## Network and TLS

Containers connect on the internal network with `DB_HOST=postgres`, `DB_PORT=5432`, and `DB_SSLMODE=disable`. Host Python connects through `127.0.0.1:${POSTGRES_HOST_PORT}`. The default Compose file never binds PostgreSQL to `0.0.0.0`.

The stock local PostgreSQL container has no trusted TLS certificate, so `disable` is the correct local/internal default. If a future production design sends traffic over an untrusted or remote network, configure PostgreSQL TLS and use `verify-full` with a trusted CA; do not merely change `DB_SSLMODE` without provisioning certificates.

## Persistence proof

After loading data, record table counts, run `docker compose down` without `-v`, then `docker compose up -d postgres` and recheck counts. Also run `docker compose up -d --force-recreate postgres` and verify again. The named volume is `universal_supplier_pgdata` mounted at `/var/lib/postgresql/data`.

No routine script removes the volume. A destructive reset is intentionally not automated. If one is ever needed, it requires a separately reviewed command and explicit confirmation of the exact project/volume target.

## CLI application container

The optional `application` service is in the `tools` profile. Examples:

```text
docker compose --profile tools run --rm application -m pytest -q
docker compose --profile tools run --rm application scripts/docker_postgres_migrate.py
docker compose --profile tools run --rm application scripts/inspect_product.py --help
```

Normal Windows commands such as `python scripts\run_partner_st_live_crawl.py ...` remain supported.

