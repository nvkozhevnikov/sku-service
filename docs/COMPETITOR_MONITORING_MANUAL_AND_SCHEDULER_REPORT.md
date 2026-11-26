# COMPETITOR_MONITORING_MANUAL_AND_SCHEDULER_REPORT

2026-10-13 · OPERATOR-CONFIRMED preparation-only specification ad32b38b.

**COMPETITOR_MONITORING_MANUAL_AND_SCHEDULER_READY**

**WAITING_FOR_OPERATOR_REVIEW — STOP**

## Delivery status

| Scope | Status and evidence |
|---|---|
| MANUAL RUN | DONE / APPLIED / LIVE-VERIFIED, bounded MetalMaster UI run |
| SCHEDULER CODE | DONE / REPOSITORY-VERIFIED, fixtures and shared path |
| SCHEDULER CONFIG | DONE / REPOSITORY-VERIFIED, enabled=false |
| SCHEDULER ACTIVATION | NOT APPLIED |
| CRON INSTALLED | NO |
| WINDOWS TASK CREATED | NO |
| BACKGROUND COLLECTION DAEMON | NO |
| AUTO LIVE RUN EXECUTED | NO |
| Scheduled fixture CLI | VERIFIED, isolated temporary configuration only |

Reason: operator requested preparation only. No production schedule or deployment.
The local UI web server is running for review; it contains no collection polling
loop, timer or automatic startup job. Manual collection starts only on POST.

## Execution contract

UI OPERATOR_UI, CLI_MANUAL and SCHEDULED use CollectionService →
run_competitor_collection → the same collection_adapter and report rebuild.
Legacy CLI crawl dispatch now joins this service; its old independent dispatch
test was updated to the shared contract, with an additional explicit regression
test proving public Kuvalda remains accessible despite robots Disallow.
No source extractor or business event/KPI calculation was rewritten.

Single competitor run record uses existing result JSON for origin and outcomes;
no DB schema migration. Existing normalized_dataset/build_facts/change detector
and business HTML rebuild are reused. Source failures preserve committed results.
Shared kernel lock prevents duplicate processes; stale RUNNING journal recovery
does not delete or steal the lock. Origin history is atomically saved under lock.

CLI: `python -m competitor_intel --dev-config competitor-data/dev-config.json run-scheduled`.
Default invocation VERIFIED returns CONFIG_ERROR / SCHEDULER_DISABLED / exit 4,
without DB initialization, HTTP or a scheduled run record.
Manual CLI: `collect`; explicit local UI mode: launcher `--allow-collection`.
Default launcher retains read-only behavior. Supplier/settings writes remain blocked.

Exit codes: 0 COMPLETED; 1 PARTIAL; 2 FAILED; 3 SKIP_ALREADY_RUNNING;
4 CONFIG_ERROR. No aggressive run retries.

Config: enabled=false, Europe/Moscow, daily 08:00, six selected sources,
skip_if_running=true, 1800-second cooperative collection deadline,
45 pages/source, failure isolation. HTTP timeouts and pacing use remaining budget.
Report finalization is allowed after the collection deadline; no OS hard-kill claim.
Logs rotate at 2 MiB, four backups, bounded journals; raw capture evidence remains
referenced by DB and is not deleted blindly. Secret values are never logged or
passed on the command line. Configuration/environment paths are documented.

## Manual LIVE QA

LIVE-VERIFIED on loopback UI 58124, database 55454 only.
Scope: MetalMaster robots preflight + one homepage; no product-detail fetch,
no full catalog, no other competitor HTTP, no YouMet capture changes.

Two minimal UI attempts: first loaded server still held the old overall-status
classification; its accepted source result was PARTIAL but overall displayed FAILED.
After loading the corrected code, the repeat run is honestly PARTIAL / exit 1,
because five known surfaces remain beyond the explicit one-page QA limit.
Evidence of both attempts is retained; no failure receipt was concealed.

Total source GETs: 4 (two robots, two homepage). Total accepted observations: 2.
New pages: 0; new versions: 1; items in new version: 16. Repeat version duplication: 0.
Final run record contains origin OPERATOR_UI and PARTIAL status; report rebuilt.
Shared-lock duplicate POST returns 409 «Сбор уже выполняется».
Other five source fingerprints and historical KAMI cache unchanged.
Duplicate pages=0; duplicate versions=0. Universal Supplier KAMI writes=0.

One genuine PRICE_INCREASE is now visible for MetalMaster MML 1830 BV:
73,042 → 77,296 RUB, +4,254 / +5.82%, from 2026-10-09 to 2026-10-13 observations.
Both SALE prices are directly present in saved visible card HTML, with distinct
accepted versions of the same extractor lineage. This is a repeated LIVE change,
not a fabricated first-observation event. Evidence: PRICE_TRANSITION_EVIDENCE.json.

The previous QA helper's fixed expectation of zero events was invalidated by this
authorized repeat LIVE capture. It now asserts zero while baseline-only, or verifies
accepted before/after versions, equal extractor lineage, timestamps and public
price semantics when comparable data exists. Original baseline receipt is retained.

## Verification

REPOSITORY-VERIFIED:

- 209 tests PASS, one previously excluded SQL mutation fixture remains excluded.
- Includes real scheduled CLI fixture integration through the same service/lock,
  actual change detector and HTML builder; no real source/DB used by that test.
- Origin, config gate, deterministic exits, source/report failure isolation,
  cross-process lock/release, interruption recovery, runtime limit and log rotation.
- Legacy supplier suite: 41 PASS / same 31 FAIL, exact set equality, new regressions 0.
- Existing fixture visual QA: 12 checks; local scheduler UI: 1920/1600/1366,
  no document overflow, disabled auto control, no invented next-run date.
- Read-only PostgreSQL proof: 6 PASS, table fingerprints equal before/after reads.
- git diff --check: no whitespace errors; existing LF/CRLF notices only.

QA receipts directory:
`work/sterbrust-competitive-intelligence/reports/COMPETITOR_SCHEDULER_2026-10-13/`.

Includes TEST_RESULT.xml, MANUAL_LIVE_QA.json, INITIAL_MANUAL_QA.json,
PRICE_TRANSITION_EVIDENCE.json, POSTGRES_READONLY_QA.json,
UI_READINESS_QA.json, LEGACY_COMPARE.json, GIT_STATUS.txt, GIT_REFS.txt,
MANUAL_AND_SCHEDULER_UI.png and SCHEDULER_UI_1920/1600/1366.png.

## Installation preparation and limits

[Repository scheduler guide](../work/sterbrust-competitive-intelligence/docs/COMPETITOR_MONITORING_SCHEDULER.md)
contains Windows Program / Arguments / Start in, timezone mapping, cron template
and systemd service/timer templates. Examples were not executed. Linux paths are
placeholders; target platform syntax/runtime must be verified before installation.
Windows action paths are verified against this checkout, not a guessed production path.

YouMet remains MANUAL_PENDING / no HTTP. KAMI historical seed is lookup-only;
refresh candidates are deferred and targeted product-detail GETs remain zero.
Render-required pages produce honest incomplete status; no unattended browser
fallback or anti-bot evasion was added. Page-cap exhaustion remains PARTIAL.
Full six-source LIVE collection was not rerun just to test schedule infrastructure.
No schema, seed rebuilding, supplier DB, scheduler installation or production change.

## Git and runtime safety

Branch: feature/competitive-intelligence-kuvalda-metalmaster.
HEAD unchanged: b24a6aab14b4b861645db849564d62c2bed76b9d.
Develop local/tracking: e63eb6fe4da0a5124ae98deaa2f5f4313c4a96c3.
Production local/tracking: 36924836bb02965e857173773cdb70f302914643.
Remote not mutated or polled; tracking refs unchanged. No claim about unrelated
remote changes by third parties. Commit/push/merge/PR/deploy=0.

New modules execution.py / collection_adapter.py / schedule.json, automation
template, scheduler docs, tests and bounded QA script. Existing CLI, optional UI
service wiring, launcher opt-in, projection cache invalidation and QA helper updated.
Pre-existing uncommitted visual work is preserved. Full status: GIT_STATUS.txt.

UI: http://127.0.0.1:58124/competitor-monitoring/sources
AI OFF; scheduler_enabled=false. Await operator review; no further collection or
activation authorized by completion of this task.
