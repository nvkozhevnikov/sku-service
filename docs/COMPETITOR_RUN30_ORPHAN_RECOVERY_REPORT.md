# Run30 orphan recovery — 2026-10-13

**COMPETITOR_MONITORING_RUN30_ORPHAN_RECOVERED**

**PARTIAL_DATA_VERIFIED · NO_NEW_COLLECTION**

**WAITING_FOR_OPERATOR_REVIEW — STOP**

## REQUESTED

OPERATOR-CONFIRMED attachment a8206ed6-5b52-4574-b6c0-c4780e18f1e6:
recover only orphaned run30 metadata, preserve accepted partial facts, verify
derived changes and rebuild the existing report without another collection.

## DONE

Inspected existing status contract: competitor_runs.status is text, with existing
PARTIAL and FAILED terminal states; runner exit contract PARTIAL=1. Chosen PARTIAL,
never COMPLETED. No new database status, enum or schema migration.
Source outcomes live in existing JSON; NOT_REACHED is descriptive source metadata,
not a new database run status or an invented source failure.

Pre-write snapshot retained run30 row, original journals, source counts, all83
observation IDs / referenced accepted version IDs, event/report state, capture
hash checks, lock metadata and full table fingerprints. Original report files
retained under REPORT_BEFORE. Confirmed recorded worker PID26676 absent.
Nonblocking shared kernel lock acquisition succeeded on the existing file.
No file deletion or ownership inference from mtime; lock content/mtime preserved.

## APPLIED

Only run30 DB control metadata and last-run/manual-last projections were finalized.
Run-specific report rebuild result was then linked to that same metadata, without
changing finished_at. Observations, versions, pages, items, source registry,
surface evidence, seed cache, capture ledger and other run rows were not rewritten.

| Field | Result |
|---|---|
| RUN30_FINAL_STATUS | PARTIAL |
| RUN30_FINISHED_AT | 2026-10-13 12:37:50.163255 +03 |
| Finished time meaning | Actual recovery/finalization time; not inferred worker exit |
| Origin / recorded worker PID | OPERATOR_UI / 26676, preserved |
| Last persisted activity | 2026-10-13 11:35:36.628074 +03 |
| OBSERVATIONS_PRESERVED | 83 |
| VERSIONS_PRESERVED | 83 distinct accepted referenced version IDs |
| Versions originally captured since run30 start | 54 |
| LOCK_STATE | Nonblocking probe free; held only during recovery; released normally; file unchanged |

| Source | Terminal outcome | Accepted pages | New versions |
|---|---|---:|---:|
| Kuvalda | PARTIAL; existing homepage error/page-cap gap retained | 44 | 44 |
| MetalMaster | COMPLETED | 3 | 2 |
| JET | COMPLETED | 30 | 3 |
| stanki.ru | PARTIAL / execution_state INTERRUPTED, WORKER_PROCESS_GONE | 6 | 5 |
| Titanium | NOT_REACHED / NOT_EXECUTED | 0 | 0 |
| YouMet | NOT_REACHED / NOT_EXECUTED | 0 | 0 |

Exact originally submitted source selection was not recorded; recovery metadata
states that limitation. Not-reached outcomes use operator-confirmed run30 scope.
Known Kuvalda error remains ValueError; its detailed stack/reason was not retained.
339 queued URLs were not fetched at its bound. No attempt to fill these gaps.

## VERIFIED

All83 observations reference ACCEPTED versions with valid page/source identities,
payload source/canonical URL, timestamps, extractor lineage, raw capture SHA256,
no broken references, duplicate page keys or duplicate version keys.
Pre/post fingerprints of every source-fact table equal. Other run rows equal.

Recovery repeated against the real DB twice at verification phases: no extra
UPDATE, finished_at change, journal rewrite or source mutation. Fixture tests
also verify active-lock/live-worker refusal and idempotent metadata recovery.

### REAL_EVENTS

| Event | Attributed to run30 | Entire rebuilt report |
|---|---:|---:|
| PRICE_DECREASE | 0 | 0 |
| PRICE_INCREASE | 2 | 3 |
| NEW_PROMOTION | 0 | 0 |
| PROMOTION_CHANGED | 1 | 1 |
| PROMOTION_ENDED | 0 | 0 |
| NEW_NEWS | 0 | 0 |
| CAMPAIGN_PRODUCT_ADDED / existing NEW_CAMPAIGN_PRODUCT | 0 | 0 |
| CAMPAIGN_PRODUCT_REMOVED | 0 | 0 |
| PRODUCT_DISAPPEARED_FROM_CURRENT_CAMPAIGN | 0 | 0 |

125 BASELINE_OBSERVATION entries are retained by the existing export but excluded
from business changes. REJECTED_EVENTS=0 after full DOM/context verification.
No absence-based events emitted; no complete discovery coverage exists for this
partial run, and its observed snapshots do not assert complete item coverage.

### PRICE_TRANSITIONS_VERIFIED

| Source / context | Product | Before → after RUB | Change | Run30 |
|---|---|---:|---:|---|
| JET special_offers page23 | Шлифовальная лента 432 х 1372 мм P80 | 2040 → 2125 | +85 / +4.17% | YES |
| MetalMaster /top_action/ | MetalMaster MML1830BV | 73042 → 77296 | +4254 / +5.82% | YES |
| MetalMaster homepage | Same MML1830BV | 73042 → 77296 | +4254 / +5.82% | NO, earlier run28 transition |

These are three entity/context-specific transitions, not three distinct products.
Each has exact before/after observation IDs, accepted version IDs, same source
entity/page context and extractor lineage, same currency/region/campaign/tab,
PUBLIC/SALE semantics (actual SALE→SALE), ordered observation timestamps and
exact current-price DOM values from saved evidence. Old-price del nodes are
removed only in verification DOM copies to identify unit price; source facts
are never altered. Personal/from/unknown/lease/installment labels excluded.
PROMOTION_CHANGED is verified against corresponding before/after accepted snapshots
and actual changed field hashes; it is not inferred from missing products.

### REPORT_REBUILT / UI

Existing `competitor-data/collection/business-report` rebuilt via unchanged
normalized_dataset/build_facts/export_preview. Recovered PARTIAL run30 is explicitly
shown in a generated banner and report notes; JSON result is linked to run metadata.
Report event IDs are deterministic and unique; accepted real changes only.
All source facts remain equal during report generation.
Current UI supports PARTIAL already: localhost GET confirms Run30 PARTIAL,
«Выполнен частично», WORKER_PROCESS_GONE, NOT_REACHED and no Run30 RUNNING.
No UI redesign/restart or browser navigation was performed.

Tests: **224 PASS / 0 FAIL / 0 ERROR / 0 SKIP / 1 prior SQL fixture DESELECTED**
(existing218 +6 recovery tests). Tests cover orphan/lock/idempotency/terminal/
partial coverage/report rebuild; existing price comparability regressions remain.
No tests weakened. Existing Starlette/httpx deprecation warning retained.

## NOT APPLIED / WHY

No new collection, source HTTP, browser fetch/navigation, collection POST,
scheduler run, timer/cron/Windows task/daemon activation, commit/push/merge/deploy.
No major runner rewrite: task authorizes recovery and analysis only.
Scheduler config remains enabled=false. Universal Supplier/KAMI supplier state/
Vekprom/Sterbrust/ESOL/develop/production untouched.

## REMAINING / DEVELOPER REQUIRED

Separate architectural fix required: UI currently executes collection inside its
Uvicorn process. The coupling is REPOSITORY-VERIFIED from run_in_threadpool →
CollectionService.run → synchronous runner/os.getpid/kernel lock.
Do not restart UI during an active collection until this is corrected.
[Developer handoff](../handoffs/COMPETITOR_COLLECTION_UI_PROCESS_COUPLING.md)
defines the independent-worker lifetime, same contract/lock, heartbeat and
restart/duplication/cancellation acceptance tests. No fix is deployed here.

## RISKS / UNKNOWN

ROOT_CAUSE_VERIFIED_OR_SUSPECTED: architecture coupling VERIFIED; exact run30
termination sequence and UI restart cause SUSPECTED, not promoted to VERIFIED.
Worker exit timestamp, interrupted next URL and exact original submitted source
selection remain unknown. Kuvalda bound/error and unvisited source gaps remain.
Recovery closes metadata honestly; it does not make the partial crawl complete.

The first snapshot attempt failed because a second handle cannot read the byte
while the recovery's exclusive kernel lock is held. It failed before DML; the
successful snapshot used the byte read before acquisition and verified unchanged
mtime. A final timestamp check initially compared JSON text to datetime; corrected
serialization comparison confirmed the original finished_at remained unchanged.
No failed attempt triggered a new collection or changed valid facts.

## NEXT ACTION

Operator review of recovery/evidence and developer handoff. No automatic restart,
recrawl, source refresh, publication or architectural implementation.

Evidence folder: work/sterbrust-competitive-intelligence/reports/
COMPETITOR_RUN30_RECOVERY_2026-10-13/:
PRE_WRITE_SNAPSHOT.json, STATE_BEFORE_FINALIZATION.json, RECOVERY_RESULT.json,
PRICE_TRANSITION_EVIDENCE.json, REAL_EVENT_EVIDENCE.json, REJECTED_EVENTS.json,
REPORT_REBUILD_RESULT.json, FINAL_VERIFICATION.json, TEST_RESULT.xml, REPORT_BEFORE/.
Existing generated report: competitor-data/collection/business-report/index.html.

HEAD stays263d7cb6324d3e4d70cd5cdff3cce5deef8319d4; recovery helper/tests remain
local for review, no automatic publication.
