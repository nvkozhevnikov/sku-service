# UI / collection worker lifetime decoupling — 2026-10-14

COMPETITOR_MONITORING_UI_WORKER_DECOUPLED
WAITING_FOR_OPERATOR_REVIEW / STOP

## REQUESTED

OPERATOR-CONFIRMED: specificationf5ad5f1f-ace7-493f-bb50-1260d6fc5eed.
Separate manual collection lifetime from Uvicorn without a second collector,
daemon, actual competitor crawl, business-data mutation or automatic publication.

## DONE

REPOSITORY-VERIFIED: UI POST now calls CollectionService.submit, not collection.run.
Submission validates scope/bounds/source enablement and checks kernel lock,
persistent dispatch reservation and DB RUNNING records. It creates a private
request record, starts a one-shot process and returns after at most a short
one-second acknowledgement wait, not after a crawl. HTML keeps its redirect;
JSON callers receive202 plus request/run identity. Duplicate requests409;
bootstrap spawn failure503; a completed-but-failed run remains a real run.

The independent entrypoint calls CollectionService.run → run_competitor_collection
→ the original adapters/change/report/repository path. CLI remains synchronous
in its own CLI process; future scheduled submit can use this same worker.
No source parser, price logic, event detector or report generation was duplicated.

Windows launch uses the same base interpreter and exact active environment
package paths, CREATE_NO_WINDOW and CREATE_BREAKAWAY_FROM_JOB, closed inherited
descriptors and independent log output. Avoids the venv redirector as worker
parent. If spawn/job policy prevents launch, fail honestly; no in-process crawl
fallback. A worker may belong to a host-wide job, but fixture evidence proves
it is outside the UI-owned job and survives the UI process disappearing.

## APPLIED

Local feature code only. Dedicated existing PostgreSQL restored after reboot,
expected DB verified read-only before local UI start. Correct branch/head and
remote equality at start:21748905371ea8faab0aabf1b6532be5b52602de, clean checkout.
Local UI reloaded after fixture acceptance; no business collection started.
No migrations or business DB writes. All original competitor DB fingerprints
remain equal;11 source identities and eleven-source disabled schedule preserved.

UI status comes from last-run.json / worker-dispatch.json and existing DB run
bookkeeping, with kernel-lock state and process PID+creation FILETIME+executable.
It does not depend on a Uvicorn thread, asyncio Task, global Future or Popen
object. An immutable per-request JSON preserves submission provenance.
STARTING reservation blocks CLI/UI duplicates before worker acquires the same
collection lock. No second collection lock or permanent service was introduced.

UI exposes current run/status/origin/start time/PID, reads status via guarded
GET API and polls only while a run is active. Existing manual form is reused on
Sources; eleven choices and disabled schedule remain explicit. No major redesign.
Terminal polling clears only the read projection cache; no data mutation.

## VERIFIED

LIVE-VERIFIED Windows OS fixture acceptance (not mocks in one process):

- Start from an actual browser through the existing UI POST route.
- Persist worker PID17976, creation134364370027189524 and run1/OPERATOR_UI.
- Close browser; worker remains alive.
- Stop actual old fixture UI PID30896; worker remains alive and outside its job.
- Start new fixture UI PID25652; it sees the same worker/run from persisted state.
- Release deterministic fake workload; worker finalizes COMPLETED and exits.
- Reconnected UI sees terminal status, same run1 and exactly one fixture run.
- Second UI POST409; actual CLI and scheduled-fixture subprocesses return
  SKIP_ALREADY_RUNNING/exit3 through the same kernel-lock use-case.
- Fresh service instance reads the same run with no old UI memory.
- Separate real fixture worker termination is identified as ORPHANED after
  Windows releases kernel handles; persisted RUNNING is not rewritten, and
  another UI submission is blocked pending explicit review/recovery.
- PID reuse/unknown identity, STARTING reservation, failed run finalization,
  bootstrap spawn failure, disabled scheduler and fixture isolation are tested.

PACKAGED-EVIDENCE:254 testsPASS /0 FAIL /0 ERROR /0 SKIP /1 prior SQL fixture
DESELECTED. Previous244 baseline assertions preserved; the two old UI-controller
fixtures now model submit rather than synchronous run, retaining their origin,
scope, CSRF/origin and duplicate assertions. Added ten control/process tests.
Windows atomic journal replacement has bounded retries for temporary reader
sharing conflicts; no swallowed errors or unbounded waiting.
After removing an inadvertently duplicated existing form include,42 focused
UI/execution tests passed. Main local UI50 GET-only checks passed across all
seven tabs at1366/375 widths: no overflow/JS errors,one eleven-source manual
form,correct persistent run35 terminal state and schedulerOFF. No actual POST.

UI_WORKER_DECOUPLED=YES
WORKER_SURVIVED_UI_RESTART=YES
UI_RECONNECTED_TO_SAME_RUN=YES
BROWSER_CLOSE_SURVIVAL=YES
DUPLICATE_RUN_BLOCKED=YES
ACTIVE_RUN_SOURCE_OF_TRUTH=JOURNAL / PERSISTENT_DISPATCH / DB_RUN_RECORD;
liveness=KERNEL_LOCK + PID_CREATION + EXECUTABLE.
NETWORK_GETS=0 competitor/external; localhost control-plane requests only.
SCHEDULER_ENABLED=false; no cron/Windows Task/daemon activation.

## NOT APPLIED

GIT_COMMIT=NO / GIT_PUSH=NO / PR=NO / MERGE=NO / DEPLOY=NO.
No real collection or real run-record creation. No Sterbrust/ESOL/Universal
Supplier/supplier KAMI/Vekprom writes. No develop/production changes.
No actual worker crash/recovery experiment against business data.

## WHY

The original defect was process ownership: UI awaited the shared synchronous
runner inside its serving process. That coupling is REPOSITORY-VERIFIED and
fixed by independent one-shot ownership. The exact historical run30 PID
termination trigger remains SUSPECTED; current fixture QA does not prove it.

## REMAINING

Operator review of the local diff. Deployment/commit/push and any actual tiny
competitor collection require separate operator scope. No further work started.

## RISKS / UNKNOWN

PC shutdown, termination of a host-wide job or an actual worker crash can still
interrupt collection; this change protects against web-process/browser lifetime.
Restricted Windows job policies may reject breakaway launch; the UI fails closed.
If a submitter disappears before spawning a worker or recording its identity,
an ambiguous STARTING reservation requires review, not an automatic replacement.
Interrupted real-worker recovery remains explicit and uses existing semantics.
Current code was validated on local Windows, not deployed production.

## NEXT ACTION

WAITING_FOR_OPERATOR_REVIEW / STOP. Review code, successful OS acceptance and
updated handoff. No implicit publish or LIVE run.

Evidence:reports/UI_WORKER_DECOUPLING_2026-10-14/
BASELINE.xml,TEST_RESULT.xml,OS_ACCEPTANCE.json,LOCAL_UI_QA.json,
BUSINESS_FINGERPRINTS_BEFORE.json,BUSINESS_SAFETY_AFTER.json and isolated fixture
artifacts. Failed interim experiments are retained, not relabelled as PASS.
Handoff:docs/COMPETITOR_COLLECTION_UI_PROCESS_COUPLING_HANDOFF.md.
