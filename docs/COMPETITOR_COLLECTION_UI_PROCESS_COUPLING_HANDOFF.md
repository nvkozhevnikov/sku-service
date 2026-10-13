# Developer handoff: collection lifetime coupled to UI process

## PROBLEM

REPOSITORY-VERIFIED control-plane defect: restarting the Uvicorn UI can terminate
an active competitor collection, leaving RUNNING metadata and partial committed data.

## CURRENT BEHAVIOR

`universal_supplier/control_plane/competitor_ui.py` awaits
`run_in_threadpool(collection.run, 'OPERATOR_UI', ...)` inside the serving process.
`CollectionService.run` calls the synchronous shared runner directly.
Runner records `os.getpid()`, owns the kernel file lock and performs collection/
report finalization in that same process. No independent worker/supervisor lifetime.
The journal is checkpointed after sources, not after each page; database observations
may be more recent than the file checkpoint. No independent heartbeat is recorded.

## EXPECTED BEHAVIOR

UI submits/controls a durable collection job whose lifetime survives a UI restart.
Manual and scheduled entrypoints retain the same CollectionService/use-case,
lock, source restrictions, change detector and report builder. No automatic
collection is started merely by opening UI, restarting it or recovering metadata.

## WHAT WAS CHECKED

Run30 DB/journal PID26676 absent; historical Uvicorn log identifies that PID.
Replacement UI PID31760 is distinct. Source observations stopped at11:35:36+03.
RUNNING persisted despite83 accepted committed observations. After explicit recovery,
run30 is PARTIAL with worker disappearance and source outcomes recorded separately.
Kernel probe succeeded without deleting/changing the one-byte lock file.

## ROOT CAUSE

Architecture/lifetime coupling: **REPOSITORY-VERIFIED**.
Exact run30 termination cause/sequence: **SUSPECTED_UI_PROCESS_RESTART**.
Prior NEWS UI work did restart the local UI; no exact historical termination audit
is available to promote the run-specific cause to VERIFIED.

## REQUIRED CHANGES — SEPARATE TASK

- Introduce reviewed job execution independent of the Uvicorn serving process.
- Persist actual worker PID, process start identity, selected source scope,
  page/source progress and bounded heartbeat/checkpoint timestamps.
- UI GET observes jobs; POST submits through the same execution contract.
- Handle UI restart/reconnection without duplicate collection or stealing locks.
- Distinguish confirmed process absence from waiting/stalled/unknown states.
- Recovery must be explicit/idempotent and preserve committed facts; do not start
  a replacement crawl simply because a journal is stale.
- Review Windows lifetime semantics/job objects, shutdown and cancellation.

## MUST NOT BREAK

Dedicated competitor storage boundary; source pacing/public-access constraints;
single shared cross-process lock; origins; scheduler enabled=false by default;
partial failure isolation; no false absence events; exact KAMI historical context;
manual YouMet operator evidence; original observations/version provenance; no
supplier DB, production, Sterbrust/ESOL/Vekprom writes.

## ACCEPTANCE CRITERIA

During a fixture collection, restart only UI: the same worker/run continues and
finishes, with one run identity and no duplicate observations or events.
Parallel UI/CLI/scheduled requests cannot acquire a second execution lock.
UI reads honest liveness/terminal states from persisted metadata.
If the worker disappears, the system identifies it conservatively; approved
recovery is idempotent and preserves all committed partial observations.
No timer/daemon activation, deployment or new LIVE QA without explicit scope.

## VERIFICATION STEPS

Use isolated fixture workers/DB with blocked competitor networking. Test UI
restart mid-page and mid-source, worker failure, lock conflict, PID reuse, stale
heartbeat, report failure, cancellation and duplicate recovery. Run the existing
competitor suite. Schedule preparation remains inactive. Plan LIVE validation
separately after operator approval; do not include a full recrawl by default.

No architectural rewrite implemented in the run30 recovery task.
