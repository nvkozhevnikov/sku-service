# Portable partial RC restore

Scope: owner/administrator preparation, not production deployment permission.
The dump contains all schemas/data in this RC, not PostgreSQL global roles or
ACLs. It includes the local QA account's password hash; create/revoke target
accounts through the normal admin workflow before any separately approved use.
Private runtime passwords are not part of Git or this delivery.

1. Use PostgreSQL17.11 and a NEW empty isolated database. Never use --clean,
   restore over an existing source/user database or copy original PGDATA.
2. Verify124142732bytes and
   SHA256b6858888fd2d370d6973eb309891293e0e8c8e2da1a0d2b884fc63e935aa219e.
   `pg_restore --list` must show public+competitor schemas and expected tables.
3. As the isolated migration owner, run `pg_restore --no-owner --no-acl
   --exit-on-error -d TARGET_DB sterbrust_unified_2026-10-19_RC1.dump`.
   Target must already be verified empty; no source credentials are needed.
4. Provision new application credentials out of band. Revoke PUBLIC schema
   permissions. app_supplier owns no schema and uses public+pg_catalog,
   supplier-tableDML/sequenceUSAGE only; app_competitor uses competitor+
   pg_catalog with competitor-tableDML/sequenceUSAGE only and no public table,
   sequence or CREATE privileges. reporting_ro reads approved schemas only.
   app_dispatch reads the two schemas and writes only rc_replay_jobs. Keep
   migration ownership separate. Never give app roles superuser/CREATEDB/
   CREATEROLE/BYPASSRLS. RequireSCRAM and private loopback binding in local QA.
5. Compare full fingerprints/sequences/constraints/migration ledger from
   database/DB_MANIFEST.json and source namespace counts. Then verify shared
   login and supplier+competitor read paths. RESTORE_RECEIPT.json records the
   actual successful clean logical restore performed for this delivery.

Current local runtime is deliberately pinned to RC identity/role/schema/port;
it is not an arbitrary server DSN. Its two explicit accepted DB identities are
the local RC and its restore rehearsal on55461. Portability of the data dump
does not waive the future server runtime/role/deployment gate.

Local retained cluster is cleanly stopped afterQA. Starting that specific own
cluster and the review UI is a separate deliberate local action using the
retained private runtime settings; no original cluster should be started by
the review launcher. Schedulers and collection remain disabled.

Do not restore newly found55452source dumps: the operator separately requires
exact permission for that next source restore. Full release remains blocked.
