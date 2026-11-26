# Portable RC restore — current17Nov2026

PACKAGED-EVIDENCE / LIVE-VERIFIED ISOLATED LINUX17.11: latest post-Studio/post-ledger
dump is STERBRUST_UNIFIED_RC_WITH_CONTENT_STUDIO_20261117.dump,157947428bytes,
SHA256b11f45bcc4aa24891c49548bd15babbbde0a88be158dd15429c086515200efdc.
Current metadata:81tables in public/competitor/content_studio,52sequence states,
1282original mappings and20migration ledger entries, including020. Actual restore
checks and existing-user login receipts are in the current handoff manifest.

1. Verify the NEW database package manifest/hash; use a new empty PostgreSQL17.11
   database. Never restore over primary, use --clean, or copy its PGDATA.
2. Restore the new custom dump with --no-owner --no-acl --exit-on-error. Preserve
   users/password hashes/history/IDs; no matching or VK replay.
3. Restore the eight immutable Studio media files into the new configured persistent
   media directory and verify SHA values. There is no SQLite fallback.
4. Reapply the reviewed current runtime grants from DATABASE_METADATA.json using
   separate owner/runtime roles (rc_runtime/competitor_runtime). Runtime must remain
   NOSUPERUSER/NOCREATEDB/NOCREATEROLE and without CREATE in content_studio. Provision
   actual credentials privately; passwords/master/API keys are excluded from code
   and evidence ZIPs. API refs require separate reconnect; external calls stayNO.
5. Existing supplier capture archives remain an immutable dependency. The current
   data package identifies the previous capture/database ZIP by exact SHA; extract
   its capture archives/relocation manifests only. Do NOT apply that ZIP's old dump
   over the current dump. Current SQL source/capture rows remain unchanged.
6. Compare all81table hashes/52sequence values+is_called,1282,20ledger hashes and
   Studio6/25/4/8/ADMIN-only policy. Then verify shared login/media/history/preview/
   ZIP/ACL. Preserve forced-change flags; do not reset passwords for a smoke test.

Current primary020ledger was separately reconciled under exact operator approval;
do not replay its DDL/import. For a NEW bootstrap, run the general migration runner
before native data import and prepare a new plan with apply_schema=false.
See [CONTENT_STUDIO.md](CONTENT_STUDIO.md) and [RELEASE_20261116.md](RELEASE_20261116.md).
This is restore preparation/isolated QA evidence, not real-server deployment or
fresh supplier-commerce proof. Schedulers/transport/Bitrix/ESOL remain unauthorized.

## Historical19Oct partial RC guide — old dump/roles, not current delivery

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
