# Release canary on the native runtime — 16 November 2026

OPERATOR-CONFIRMED preparation only; no LIVE without separate exact confirmation.
Base5b57564. This adds purpose release-commercial-canary/v1 to the existing
commercial refresh lifecycle: Session/advisory lock, Scheduler, PostgresJobQueue,
Orchestrator, source_runtime/host gates, SourceBackend and checkpoint/SQL receipts.
No second collector, database, scheduler or matching algorithm was added.

The old confirmed-commercial-refresh/v1 still requires exactly1270 confirmed rows.
The new purpose requires exactly20Partner-ST +20Optimum; other9have empty targets
and identical before/after settings (SAVED_ONLY, no enqueue). Both LIVE sources must
already be enabled. No continuation, recovery, source activation, robots exception,
DISCOVERY, automatic retry or automatic matching apply is allowed. Per-transport
attempts1, queue attempts1. Original matrix SHA, target, HEAD/engine, URL/ID/settings,
rate policy, original1282Sterbrust links and protected historical jobs remain pinned.
Used grants fail before settings activation. Terminal failures are never resumed.

Native ingestion checks the existing source PK/catalog link before and after ingest
inside the same transaction; an attempted link change rolls back. Canonical URL,
requested URL and final URL must agree with the signed target. Existing frozen-scope
redirect guards run before redirected requests. Matching plans may run in dry-run;
the runner sets apply=NO and the backend rejects environment drift to YES. A dry-run
receipt is not a persisted matching decision/current validation timestamp.

Selection correction:20Partner-ST with confirmed links, positive saved prices and
in_stock;20Optimum with positive saved prices/in_stock but NO confirmed links.
The latter tests ingestion/commercial extraction, not confirmed canonical updates.
No alternative Optimum confirmed cohort exists in the verified current snapshot.
Saved commercial values do not assert LIVE price/availability. After approved LIVE,
inspect SQL receipts/observed_at/price semantics/availability/matching freshness and
both current feeds. Zero admissible records means NOT_READY with explicit reasons;
do not relabel historical48/206as fresh or relax identity/TTL/price-type guards.

Preparation (SELECT-only): supplier_system.py prepare --release-canary
--confirmed-commercial-targets TARGETS.json --not-before UTC --execution-until UTC
--output NEW_APPROVAL.json. Despite the inherited option name, only the new purpose
admits stable unlinked Optimum rows; the legacy confirmed-only contract is unchanged.
Inspect with supplier_system.py inspect --approval FILE --sha256 EXACT_SHA.
Only after operator confirms exact SHA/window: supplier_system.py run --approval FILE
--sha256 EXACT_SHA --confirm EXACT_SHA. Full literal command/window/URL list belong
to the external signed receipt, not an executable default in documentation.
STOP via supplier_system.py stop --session the printed session directory; wait for
FINAL/SETTINGS_RESTORED and zero active jobs. No force kill or automatic restart.

Requests:40signed product URLs, up to2initial robots.txt URLs; no discovery or retries.
All redirect starts are subject to the existing frozen URL guard, TLS/DNS/rate/STOP
controls. Robots policy mismatch must stop; approval does not bypass site policy.
The20limit is specific to release QA and does not cap normal working operation.

VERIFIED scope: offline regression includes queue/worker/receipts/STOP, legacy grants,
matching policy and competitor scheduled CLI/locks/detector/report fixtures. This
does not prove LIVE transport or activate Windows Task Scheduler/cron. Existing
daily task configuration is unchanged; successful real daily collection is pending.

Linux restore verification now uses the actual final157881574byte dump, SHA256
746d868a77e8c905c7288dbcabb1e61f68a3e3287f098ce78a7abc12b989d43c:
68table hashes,50sequences including is_called, both final ADMIN password logins
and forced change in isolated PostgreSQL17.11. Primary DB writes/source HTTP0.
No real server deployment or Bitrix/ESOL operation is authorized.
