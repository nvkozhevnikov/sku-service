# Separate RC database handoff

Status: custom dump created/read-only inventory verified; **real restore rehearsal
NOT EXECUTED**. No production or source database is a permissible restore target.
Required PostgreSQL17.x, source and pg_dump/pg_restore verified17.11. Use17.11 for
the first rehearsal. Physical Windows data-directory is never the delivery.

## Inputs and roles

Receive separately `universal_supplier_rc_final.dump`, `.dump.sha256`, `.dump.json`
and final accepted artifacts. Verify SHA with `sha256sum -c
universal_supplier_rc_final.dump.sha256`, then `pg_restore --list` (read-only).
The dump uses --no-owner/--no-privileges. It does not export roles/passwords.
Keep `.env.server` separate from Git, mode0600, independent admin/app/session
secrets. Connect via libpq env or passfile, never password CLI arguments.

The new PostgreSQL persistent volume must be empty/explicitly approved. Server
compose's initialization script creates an app login NOSUPERUSER/NOCREATEDB/
NOCREATEROLE, owns the new app DB, revokes PUBLIC schema create. Do not reuse or
remove an existing volume. No scheduler/worker service starts during this step.
Target cluster system ID will be **different**: record its own ID for server
DB_SYSTEM_IDENTIFIER, not7691270601420084116 copied from the Windows source.

## Proposed Linux restore procedure — approval required, not performed

1. Checkout fixed develop SHA in DEVELOPER_HANDOFF.md.
2. Configure `.env.server` and validate compose, start **only** postgres on the
   new approved persistent volume. This is deployment preparation, not a command
   performed in this task. The init script creates the empty app database.
3. Verify app database is empty, expected host/port/database/system ID and roles.
4. Stream the separate verified dump into the approved empty target:

```sh
docker compose --env-file .env.server -f docker-compose.server.yml exec -T postgres \
  sh -c 'exec pg_restore --username "$POSTGRES_USER" --dbname "$APP_DB_NAME" --role "$APP_DB_USER" --no-owner --no-privileges --exit-on-error --single-transaction' \
  < universal_supplier_rc_final.dump
```

Do not add --clean or restore over existing runtime. Roll back a failed rehearsal
by abandoning its disposable target, never by altering the source RC.

The backup has15 schema_migrations (001–015). Checkout includes the exact SQL and
immutable baseline manifests. After restore, compare recorded SHA to checked-out
SQL. Ordinary web start does not migrate. Explicit `--profile init run --rm
migrate` is only necessary after approval when checks indicate unapplied changes;
it is not permission to replay/modify the source RC. Existing15 must verify no-op.

## Read-only verification

```sql
BEGIN READ ONLY;
SELECT version(), current_database(), inet_server_port();
SELECT system_identifier FROM pg_control_system();
SELECT filename,sha256 FROM schema_migrations ORDER BY filename;
SELECT s.code,count(p.id) FROM suppliers s LEFT JOIN source_products p
 ON p.supplier_id=s.id GROUP BY s.code ORDER BY s.code;
SELECT 'source_products',count(*) FROM source_products UNION ALL
SELECT 'offers',count(*) FROM offers UNION ALL
SELECT 'product_matches',count(*) FROM product_matches UNION ALL
SELECT 'catalog_products',count(*) FROM catalog_products UNION ALL
SELECT 'catalog_offer_selection',count(*) FROM catalog_offer_selection UNION ALL
SELECT 'supplier_http_captures',count(*) FROM supplier_http_captures UNION ALL
SELECT 'offer_commercial_observations',count(*) FROM offer_commercial_observations;
ROLLBACK;
```

Expected source products/offers4407 each; supplier namespaces Partner1225,
Optimum1353,Intervesp1642,BekaRU109,BekaTR78. Product_matches2890,
catalog_products59427,catalog_offer_selection121,captures/observations5104 each.
Suppliers5. These are snapshot-consistent dump counts, not the accepted matching
classification counts. Fresh pinned registry59500 is a separate saved artifact,
not59427 DB catalog rows. No canonical creation/link promotion is in this dump.

Mount final accepted package at server-data/final-rc, pin SHA256_MANIFEST file
digest in FINAL_RC_MANIFEST_SHA256. `/rc-final` must reconcile506/2/3640/259,
unique canonical458. Legacy `/review` shows persisted SQL, not latest proposal
promotions. Do not silently run matching/crawl to make these views agree.

Only then, with deployment authorization, start web. Verify `/health` and `/ready`,
login/create admin interactively only if needed; no password logging. Scheduler,
worker, manual supplier writes and Sterbrust/ESOL write remain OFF. No XML import.

## Exact rehearsal command prepared, not executed

After an operator explicitly authorizes creation of **only** a fresh disposable
`universal_supplier_rc_rehearsal_20261002` on a specified isolated PG17.11 target
host/port/system ID, with a supplied owner role, use:

```sh
# Set PGHOST/PGPORT/PGUSER/PGPASSFILE to that separately approved isolated target.
createdb --owner="$RC_RESTORE_OWNER" universal_supplier_rc_rehearsal_20261002
pg_restore --dbname=universal_supplier_rc_rehearsal_20261002 \
  --role="$RC_RESTORE_OWNER" --no-owner --no-privileges \
  --exit-on-error --single-transaction universal_supplier_rc_final.dump
```

STOP if that DB already exists, target identity differs, dump SHA fails or any
source/production target is selected. The operator must name the isolated target
and permit exactly create/restore/verify; this task does not authorize execution.
