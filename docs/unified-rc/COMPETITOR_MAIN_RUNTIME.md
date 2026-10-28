# Permanent Unified RC / Competitor Monitoring

Primary:127.0.0.1:55461/rc_verify_vi_20261019, PostgreSQL system7698346631476390904.
Working project:this existing checkout. Supplier code and source adapters are retained.
Supplier runtime:rc_runtime/public. Competitor runtime:competitor_runtime/competitor, search_path competitor,pg_catalog.
No rc_owner in runtime credentials. Main Store validates exact DB/role/host/port/system/marker, no cross-public access/DDL/destructive rights. Legacy/restore guards remain distinct.

## Private configuration

Ignored files (not portable source or Git):
- reports/integration/private-main-supplier-runtime.json:existing supplier credential copied byte-for-byte, not rotated.
- reports/integration/private-main-session.json:existing session signing secret; users/passwords unchanged.
- competitor-data/main-config.json:new independent competitor credential.
- competitor-data/collection/:native lock/journals/captures/reports. Do not delete a lock to unblock a worker.

## Start and inspect

Run from this checkout in PowerShell, with the existing workspace Python environment:

```powershell
$env:CONTROL_PLANE_INSECURE_HTTP='YES' # explicit loopback-only UI; external source TLS remains verified
..\sku-service\.venv-audit\Scripts\python.exe scripts\rc_runtime.py
```

URL:http://127.0.0.1:58161/competitor-monitoring/overview; existing ADMIN login.
The same app serves /suppliers,/products,/runs,/review. No worker, supplier activation or automigration is started by opening the UI.

```powershell
powershell.exe -NoProfile -File scripts\run_competitor_scheduled.ps1 -PreflightOnly
```

Preflight prints exact DB/role/search_path/12sources;0sourceHTTP/0collection. PostgreSQL must already run; the wrapper waits up to the existing5minute readiness budget and does not initialize/start/restore any DB.

The single existing STERBRUST-COMPETITOR-DAILY-COLLECTION task now calls run_competitor_scheduled.ps1. Triggers/principal/settings unchanged:08:00Europe/Moscow. Native45pages/source,1800seconds, existing pacing/retries/source isolation/YouMetMANUAL_PENDING retained. No second daily task. No restart/replay of supplier LIVE11/R13.

Manual source collection uses the same CollectionService via `scripts/rc_runtime.py --collect-competitors --source <existing-source> --max-pages <approved-bound>`. It requires an operator-authorized scope. The one MetalMaster/max1 integration canary is spent; do not repeat it as part of setup. Scheduled invocation uses --scheduled-competitors and the configured twelve sources.

## STOP

```powershell
..\sku-service\.venv-audit\Scripts\python.exe scripts\rc_runtime.py --stop-competitors
```

The STOP is bound to exact run_id and worker identity. It preserves the current bounded source, skips subsequent sources, rebuilds the report and closes the run/lock normally. It is not an immediate cancellation of an in-flight request or of the current source: allow that source's native cap/deadline to finish. A stale STOP cannot cancel a future run. NO_ACTIVE_COLLECTION starts nothing. Budget expiry and mutual exclusion are also covered by native regression tests.

Ctrl+C stops the UI in its own terminal; detached competitor workers have their own lifecycle and shared lock. Stop the collector with the command above first. Forced process termination is not graceful STOP and may require the existing explicit orphan-recovery procedure after inspecting PID/creation identity/kernel lock; never delete a lock or manually mark jobs succeeded.
Supplier STOP remains its existing session-scoped supplier_system.py stop --session <owned-session>; no supplier session was started by this integration.

## History/backup/recovery

Operator record and full commands/evidence:workspace reports/COMPETITOR_UNIFIED_INTEGRATION_20261028/EXECUTION/REPORT.md.
Both affected DBs were dumped and restored into isolated copies before main mutations. Original dumps, source raw-capture archives/manifests, supplier checkpoints, table/sequence/constraint/ACL baselines are retained in that report's private-backups directory; no databases or history were removed.

merge_competitor_history.py allows only the approved main/rehearsal DB, compares existing IDs and complete immutable payloads, rejects unknown conflicts/target-only history, and inserts without silent ON CONFLICT skipping. The proven historical merge is complete; do not rerun after new native observations without a fresh comparison.
Historical competitor events are derived from preserved observations and the migrated run60 EVENT_REGISTRY;2451existingeventIDs preserved. Canary61added one observation and0newversions/0newsemantic events. The first native export in the new output directory reported a2451registry bootstrap delta; the original receipt is retained, not interpreted as2451new business changes. Imported registry plus read-only rebuild reports0new events.

For recovery, stop/inspect owned workers first, preserve any post-backup data, verify dumpSHA, create a NEW isolated DB and restore with pg_restore --no-owner --no-acl --exit-on-error. Never overwrite/drop a populated primary DB. Replay exact saved ACL/role configuration only after identity checks; backups omit ACL/owners intentionally and preserve their metadata separately. Application/role cutback requires an explicit operator decision and independent readback.

The original competitor DB55454/sterbrust_competitor_intel remains online as a read-only historical reserve, maxrun60. Its old UI58124 is stopped. The historical sterbrust_unified_rc_20261019 is retained untouched. Source capture paths remain valid, with independent hash-verified archives. Do not disable the old DB write fence or restore its daily task action without a separate rollback decision.

## Verification limits

One real native canary61:robots+MetalMasterhomepage,2GET/1savedobservation in primaryDB, read back in all7authenticated competitor pages. Supplier pages also return200. Offline STOP/lease/source-failure/budget/UI guards passed; no additional supplier HTTP, full competitor repeat, Bitrix/ESOL/import/push/merge/deploy.
IAB browser login rejected its Origin; no guard was relaxed. Real authenticated HTTP rendering/readback is verified; visual browser login in IAB remains unsupported. CSS/JS and accepted layout/navigation/filters are preserved.
