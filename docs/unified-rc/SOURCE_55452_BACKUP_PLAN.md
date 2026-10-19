# 55452 backup/recovery decision — not executed

`LIVE-VERIFIED` read-only filesystem/control observation: PGDATA
`work/universal_supplier_vekprom_intervesp_20261008_pg17_data` exists;
system_identifier7694223408076967768, PostgreSQL17.11, control state
`in production`, retained postmaster.pid3320. PID3320 not found at the check;
normal read-only source connection times out. Therefore clean shutdown and
recoverability are NOT_VERIFIED. No PID-file removal or source alteration.
Read-only `pg_ctl status` also reports `no server running`; this does not
convert the retained `in production` control state into a clean shutdown.

`PACKAGED-EVIDENCE`: latest available integration checkpoint13.10 includes
6834Vekprom/1642Intervesp,16505source_products,17283captures and observations,
59427canonical products,121selection rows. These are historical comparison
controls, not a replacement for fresh SQL of recovered data.

One bounded read-only search scanned6474directories under work/reports/tmp/
project archives/LOCALAPPDATA UniversalSupplier.89inaccessible directories are
declared in reports/integration/MISSING_SOURCE_SEARCH.json. Found old01.10
Intervesp dump files are for universal_supplier_server/PG17.11, with exact SHA
and TOC retained in OLD_INTERVESP_DUMP_CANDIDATES.json. No identified portable
dump of the final55452state. No newly found dump was restored.

## Can a safe logical dump be obtained?

`INFERENCE`: potentially, after recovery readiness is established. pg_dump
requires a functioning PostgreSQL server; it cannot create a logical dump from
stopped/unclean PGDATA. Starting the original would perform physical startup/
recovery writes even if all later SQL is read-only. That conflicts with the
current no-existing-DB-change authorization and is not proposed as read-only.

Preferred separately approved recovery scope:

1. Verify no live postmaster owns this exact PGDATA; validate PostgreSQL version,
   filesystem access, free space, full WAL/tablespaces and external references.
   Do not infer these from a missing old PID alone.
2. Seal a cold physical backup of the complete original cluster, WAL and any
   tablespaces, with file/size/hash manifest. Keep original source untouched.
3. Create a separately named recovery copy, isolated port and directory,
   PostgreSQL17.11; disable external network, archive/restore commands and all
   application workers/schedulers. Any recovery or PID cleanup occurs only in
   that copy after path/system-identity validation. Never copy source PGDATA
   into the unified runtime or reinterpret it as a migration.
4. Start only the recovery copy; permit server recovery writes there. Verify
   system identity and mandatory control counts/history/manual decisions.
   Use read-only pg_dump -Fc --no-owner --no-acl to a new versioned artifact;
   seal bytes/SHA/version/provenance and stop only the recovery copy cleanly.
5. Present backup receipt and comparison. Restoring the newly obtained dump
   into staging/unified RC requires separate explicit permission under the
   operator's follow-up instruction. No restore is included in steps1–4.

NEEDS_OWNER_APPROVAL: the exact cold-backup + isolated physical recovery-copy
scope above, including a narrow exception to V2's PGDATA-copy prohibition for
recovery only, a new chosen directory/port and permitted disk budget. Source
startup, source repair, WAL reset/deletion and source-volume mutation remain
forbidden. If complete WAL/tablespaces or identity evidence is absent, STOP.

No recovery/backup/startup/copy from this plan has been executed.
