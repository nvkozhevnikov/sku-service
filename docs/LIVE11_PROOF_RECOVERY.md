# Existing LIVE11: proof-aware recovery

This change does not execute LIVE. Operator confirmation of the newly generated
approval SHA is required before any recovery enqueue or temporary activation.
The old partial/cancelled jobs and original checkpoint are never repaired by
manual SQL/status/flag edits.

## Exact scope

- Same replacement batch3a69ca0d-b9d0-41f9-8f2a-49c4b43bc57d.
- Job17/run18: existing109 COMMITTED; durable matching AUTO14(existing links),
  REVIEW21, CONFLICT48, NEW26; original1282 mappings retained.
- Technical RESUME child through existing PostgresJobQueue.enqueue. Admission
  freshly verifies the receipt, file hashes, checkpoint checksum/bytes, frozen
  input/scope, SQL captures/observations, actual matching/identity/REVIEW rows,
  source data and manual decisions/rejections. Existing native verify_resume
  remains mandatory. Failure leaves the old runtime flags/status unchanged.
- The worker verifies the proof again before start, HTTP or checkpoint writes.
  It reuses the completed matching result, reconciles109 COMMITTED and performs
  zero GET/robots, ingest or matching persistence for these cards. It creates a
  genuine technical child run, whose native successful queue completion calls
  source_batch.advance with the original frozen Beka DISCOVERY snapshot.
- Cancelled-before-start roots18–27 can retry once, with exact old REFRESH scopes
  and original chained DISCOVERY snapshots in the same batch. A run ID, changed
  parent, conflicting active job or changed attempt blocks retry. Ten root inserts
  are atomic and idempotent. Beka admission and root retry are separate native
  transactions; if the second fails, repeat the same operation to reuse the
  admitted child. The worker is not started after a failed admission.

## Compatibility is explicitly approved, not ignored

Normal SOURCE_ENGINE_CHANGED/BATCH_HEAD_CHANGED behavior remains unchanged
without exact operator confirmation. A SELECT-only preparation command binds:

- new HEAD and exact runtime engine fingerprint;
- old HEAD/input fingerprint, all22 exact source input digests and scope hashes;
- all original envelopes, T0/deadline, matrix, caps/policies and next scopes;
- exact recovery evidence file hashes and target supplier settings baseline.

Only source_recovery, source_batch, source_runtime and queue may differ in the
checked engine. Parsers, matching/F01/F03 and all other engine files must match
the old commit. Windows LF/CRLF variation is reconstructed only for those three
old edited engine files; the original immutable engine digest must select a
unique exact combination. No newline normalization weakens the old fingerprint.
The new engine must be Git-clean, have no untracked Python engine files and
match the confirmed digest. A further HEAD/file/scope change needs new approval.

The manifest alone activates nothing. Recovery requires its byte SHA passed
twice (`--sha256` and `--confirm`). Existing role/target, host policy, enabled,
attempt, deadline, two-host/queue leases and Beka isolation guards still apply.

## Operator entry point

From the feature checkout, after the implementation commit:

```powershell
& ..\sku-service\.venv-audit\Scripts\python.exe scripts\source_batch.py prepare-recovery `
  --batch-id 3a69ca0d-b9d0-41f9-8f2a-49c4b43bc57d `
  --evidence ..\..\reports\RC_LIVE11_MATCHING_RECOVERY_20261022\AUTHORIZED_COMPLETION `
  --output ..\..\reports\RC_LIVE11_PROOF_RECOVERY_20261022\APPROVAL.json
```

This is read-only SQL plus local approval-file output, not execution permission.
Use the SHA printed by preparation and independently review the generated file.
The task report supplies its exact new HEAD, SHA and a concrete next command.

Only after separate operator permission for the manifest, temporary settings,
enqueue and testDB LIVE HTTP/SQL, the **one operation** is:

```powershell
& ..\sku-service\.venv-audit\Scripts\python.exe scripts\source_batch.py recover `
  --approval ..\..\reports\RC_LIVE11_PROOF_RECOVERY_20261022\APPROVAL.json `
  --sha256 <exact-approved-SHA> --confirm <exact-approved-SHA> `
  --run --activate-approved-settings
```

The explicit run uses the existing Orchestrator (max_parallel_supplier_jobs=2),
not a new scheduler. It temporarily applies only the manifest's existing LIVE11
matrix to the exact recorded supplier settings; all changed settings and audit
events are journaled. It refuses running/foreign queued jobs or settings drift.
It stops on a real phase failure/mandatory decision, expiry or interruption,
uses existing batch STOP, waits for the pool, then restores exact baseline
settings with SELECT verification. On unexpected concurrent settings drift it
refuses to overwrite and reports the restore blocker.

The first session normally stops at the **existing Beka isolation gate**, after
technical REFRESH and actual DISCOVERY complete. It does not auto-confirm that
gate. After a separately verified/confirmed native isolation receipt, the same
approved recovery operation can reuse its children and run remaining jobs.
There is no agreement request per five cards. Incomplete discovery remains an
operator decision, never full coverage. Original T0/deadline never reset.

## Offline evidence

tests/fixtures/live11_recovery/beka_job17_run18.json.gz contains actual saved
input/checkpoint/manifest bytes, original SQL/evidence snapshots and matching
receipt, without credentials. Tests prohibit socket and psycopg connections.
They prove zero transport/client construction, ingest and rematching for the
real109 URLs, immutable Beka R→D, native queue admission, stale/tampered proof
rejection, root retry atomicity/idempotence and temporary-setting restoration.
Replay is OFFLINE evidence; it does not add LIVE goods or SQL receipts.

No PostgreSQL schema, role, ACL, catalog identity, UI, collectors, cron or
production changes. Stage6 import remains BLOCKED_IMPORT_CONTRACT_EVIDENCE.
