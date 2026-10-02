# Universal Supplier FINAL RC server handoff

Code delivery: develop at fixed SHA in docs/DEVELOPER_HANDOFF.md. Database,
accepted final artifact package and secrets are separate from Git. Read
docs/FINAL_RC_CURRENT_STATE.md, docs/DB_RESTORE.md and
docs/SUPPLIER_NEUTRAL_V2_CONTRACT.md. Real Linux restore/deployment is NOT verified.

## Explicitly approved installation (not executed in assembly)

1. Checkout fixed SHA. Image target is Python3.12 Linux. Windows paths/host uv
   are not required.
2. Copy .env.server.example to .env.server, mode0600. Supply independent
   admin/app/session secrets separately. DB_HOST/PORT/NAME/USER/PASSWORD/SSLMODE
   configure application. Never commit/log populated .env.
3. Receive separate dump/SHA/metadata. Use fresh approved persistent PG17.11
   storage, never a copied Windows data-directory.
4. Validate config:
   `docker compose --env-file .env.server -f docker-compose.server.yml config --quiet`.
5. Only with target/start authorization:
   `docker compose --env-file .env.server -f docker-compose.server.yml up -d postgres`.
6. Follow docs/DB_RESTORE.md empty-target restore. Do NOT initialize migrations
   before restore. Dump contains15 recorded migrations. Verify their checksums;
   explicit init/migrate profile only after operator approval if needed.
7. Put external final package in server-data/final-rc/, including accepted
   MATCHING_ACCEPTED.json and SHA256_MANIFEST.json. Set exact manifest file digest
   as FINAL_RC_MANIFEST_SHA256. /rc-final shows accepted506/2/3640/259 proposals
   without modifying SQL links.
8. Future supplier collection needs approved manifests/registry/sections in
   server-data/manifests/, SHA evidence and separate manual-write enable/target
   confirmation. Default COMMERCIAL_SERVER_WRITE_ENABLE=NO. Missing manifests
   do not authorize crawl. Never run matching to install this frozen release.
9. Record TARGET cluster ID in DB_SYSTEM_IDENTIFIER (not Windows source ID).
   After approved restore/count checks:
   `docker compose --env-file .env.server -f docker-compose.server.yml up -d web`.
10. Verify /health,/ready and authenticated /rc-final, five suppliers and counts.
    Create admin interactively only if necessary:
    `docker compose --env-file .env.server -f docker-compose.server.yml run --rm web python scripts/create_admin.py`.
    Do not rotate restored users blindly or put admin password in CLI arguments.

## Safety and distinct views

Ordinary start does not migrate/crawl/match/select/import. Worker and scheduler
are opt-in profiles, not default startup. Scheduler defaults NO in code/config.
Sterbrust/ESOL write flags are hard OFF in server compose. Restart:no by default.
Editing compose does NOT retroactively change old container restart policies.
Do not start old Docker installations with uninspected restart hooks.

Fresh registry59500 !=59427 historical database catalog rows. Dump has4407 source
products/offers,2890 persisted matches,121 selections,5104 captures/observations.
Latest accepted506Existing/2FULL proposals are filesystem artifacts, not new SQL
links. Existing506 rows refer to458 unique canonical IDs. /review is persisted
SQL operator state, /rc-final is accepted frozen release. No identity fabrication
or automatic reconciliation between these views.

Neutral XML is data/proposal contract, NOT ESOL payload. Review/Conflict are
non-actionable. Stage6 BLOCKED_IMPORT_CONTRACT_EVIDENCE remains.

## Operations and reproduction

Stop without deleting volumes:
`docker compose --env-file .env.server -f docker-compose.server.yml stop web postgres`.
Never down -v on real runtime. Installed-server backup: scripts/server_backup.py.
Final local backup: scripts/backup_final_rc.py, read-only with libpq passfile.
Dumps/secrets never enter Git. Local disposable PG17.11 restore rehearsal on55450
is VERIFIED (15migrationSHAs,109FK checks,broken0,five namespaces4407rows).
Any new server restore/deployment still requires its own target approval.
Use annotated tag universal-supplier-rc-2026-10-02; runtime base d2a0f0c is unchanged
by final documentation closure. Browser QA=DEFERRED_BY_OPERATOR locally, not PASS;
developer must perform browser smoke QA after authorized server deployment.

`python scripts/assemble_final_rc.py --source <received-accepted-checkpoint> --output <new-final-directory>`
verifies frozen input hashes/counts, emits neutral XML/CSV without HTTP/SQL/
matching. Desktop workbook builder uses bundled Artifact Tool, not app startup.
XML/CSV/accepted JSON are independently consumable on Linux.
