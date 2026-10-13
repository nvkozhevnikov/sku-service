# Five-source partial publication checkpoint — 2026-10-13

OPERATOR-CONFIRMED: publish only the already verified five-source implementation.
No development, gap repair, LIVE HTTP, collection, scheduler activation, PR,
merge or deployment is authorized by this publication task.

START_SHA: `56d2038b0d4381e77e3bd39cc4f277ce349317f0` (already published recovery).
Branch: `feature/competitive-intelligence-kuvalda-metalmaster`.
Commit title: `feat(competitor-monitoring): add five competitor sources`.
The commit carrying this document is the recoverable five-source checkpoint.

## DONE / APPLIED / VERIFIED

REPOSITORY-VERIFIED: reviewed actual tracked diff and every new source/test/UI
file; only intended implementation, tests and documents enter the commit.
Existing source capability/config entries remain semantically unchanged.
Generic projections/templates reuse the existing competitor-monitoring system.

PACKAGED-EVIDENCE: publication verification again passed **236 tests**, zero
failures/errors/skips and one pre-existing SQL mutation fixture deselected.
No assertions were weakened. A first test attempt failed because the new QA
output parent directory did not yet exist; the final rerun passed unchanged.
`git diff --check` passed. Original 96 UI checks, 925 record QA and identical
saved replay (0 pages/versions/items) remain checkpoint evidence; no new LIVE
or UI collection was run for publication.

LIVE-VERIFIED read-only local safety check: full competitor DB fingerprints
equal the verified partial checkpoint; zero RUNNING records. Existing kernel
collection lock is free, its bytes and timestamp unchanged. RUN30 remains
terminal PARTIAL. Default scheduler enabled=false and selected_sources remains
the original six. Runtime source capability supports eleven identities.
No competitor Windows Task, native cron command, collector process or daemon
service was found on this Windows host. Existing review UI is not a collector.

No writes to Sterbrust, ESOL, Universal Supplier, supplier KAMI or Vekprom.
Publication reads only the isolated competitor DB and writes Git/local documents.
AI remains disabled. Protected develop and production are not publication targets:

- develop: `e63eb6fe4da0a5124ae98deaa2f5f4313c4a96c3`
- production: `36924836bb02965e857173773cdb70f302914643`

## Classification and recoverability

A — intended implementation: competitor source definitions/capabilities,
parsers and adapter dispatch, generic item metadata/read projections, shared
CLI supported IDs and generic HTML/UI templates; public audit helper.

B — intended tests and documentation: `tests/test_five_new_sources.py`,
`docs/COMPETITOR_FIVE_NEW_SOURCES_REPORT.md` and this publication record.

C — excluded local evidence/runtime: ignored `reports/`, `competitor-data/`,
raw HTML/CSS captures, database data, browser caches, logs, virtual environment,
dev-config credentials and generated reports. No force-add is used.

D — unrelated tracked/untracked files: none in the feature checkout.
Workspace knowledge-base updates outside this Git checkout are separate records.

The Git checkpoint recovers code/tests/documented provenance. Actual competitor
DB/raw captures remain local and are intentionally not embedded in Git. The
onboarding report is the historical pre-publication result; its statements about
uncommitted code describe that earlier snapshot, not this publication step.

## Known gaps — unchanged

- Harvey: three HTTP200 decoding failures, unresolved.
- Amur-Stan: seven deferred dated materials, unresolved.
- Saved default schedule selection: original six sources, unchanged.

Result remains **FIVE_SOURCE_BASELINE_VERIFIED_WITH_GAPS**, not full coverage.
The UI/worker lifetime defect remains separate developer handoff work.

After feature-only push, verify local feature HEAD equals remote feature HEAD,
clean checkout, protected refs unchanged and scheduler disabled. Record exact
commit/remote SHAs in the workspace publication report (outside this commit,
avoiding a self-referential second commit).

Expected publication status: COMPETITOR_MONITORING_FIVE_SOURCE_CHECKPOINT_PUSHED.
NO_NEW_COLLECTION / SCHEDULER_DISABLED / NO_MERGE / NO_DEPLOY.
WAITING_FOR_OPERATOR_NEXT_STEP / STOP.
