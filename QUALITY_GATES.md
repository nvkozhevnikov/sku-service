# Universal Supplier Matching V2.1 quality gates

OPERATOR-CONFIRMED 2026-10-08. Scoped supplement to workspace qa/QUALITY_GATES.md.

- Exact full model/execution + compatible brand/manufacturer + compatible sold
  product kind may prove Existing without a mandatory characteristic quota.
- Characteristics corroborate/disambiguate and block when same semantic property,
  normalized unit and role materially contradict. Missing/unmapped fields and
  different counts are not a contradiction. Never collapse ranges/power roles.
- Exact own variant in a series/family title is allowed. A true parent card is not.
- Significant +,PRO,NEW,V/suffixes survive. Accessory/component cannot become machine.
- Scoped IDENTITY_ALIAS requires proven/operator evidence. SEARCH_SYNONYM retrieves
  only; fuzzy/base-model similarity never authorizes an execution change.
- Fresh target/system/migration/hash/identity gate; unique real canonical mapping;
  manual mapping protections; >=200 stratified QA (all if fewer); all plus and
  material-contradiction candidates; positive/negative regressions before apply.
- Finalization is business read-only: dump/restore into NEW DB, exact all-table/
  sequence/accepted-set equality, sanitized diff, feature-only Git. No selection
  evaluation/apply, import, deployment, protected branch or release-tag change.

Live / repository / packaged / operator evidence are distinct. Codex QA is not
independent human ground truth. Semantic replay may have audit timestamp writes.
Stage6 remains BLOCKED_IMPORT_CONTRACT_EVIDENCE; this checkpoint does not lift it.


## Per-URL isolation and bounded continuation (2026-10-23)

Operator-authorized implementation over the existing runtime/checkpoint/queue.
A pre-ingest parser/identity failure appends QUARANTINED with original capture,
fingerprint, category and reason. No model is guessed; no SQL ingest or matching
occurs for that URL. A non-200 HTTP response uses separate archived transport
evidence: never construct a fake HTTP200 ProductCard/HttpCapture. Approved host
retry policies are unchanged; 403/429, interstitial, Retry-After and unknown
transport retain global STOP. Consecutive card failures use the existing host
consecutive_error_threshold. SQL exceptions are outside the extraction catch.

Quarantine remains in the frozen scope and checkpoint history. RESUME probes
SQL and validates evidence; missing evidence or unexpected committed SQL is
UNCERTAIN. Matching uses only SQL COMMITTED without durable matching receipts.
The initial SQL safety gate is still required for eligible cards; quarantined
cards never count as successful SQL proof. HTTP/SQL/matching/quality totals stay
separate. Native execution may finish with quarantine, but collection_complete
is false, report says "завершено с карантином", and operator action remains visible.
Normal R→D advancement remains the queue completion transaction.

`prepare-continuation --previous FILE --previous-sha256 SHA --quarantine-url URL
--output FILE` is SELECT-only. It freezes exact current parents, checkpoints,
source hashes, manual locks and1282mappings, all22original scopes, time/host/rate
budgets. Each phase gets no more than four additional attempts, never above8.
This is an explicit separately approved exception, not a new default retry rule.
The ordinary three-attempt guard and old exact post-stop approval remain intact.
Known BMDO120/130 historic FAILED can become quarantine only with the signed
failed row and archived200capture/history; no manual status edits are used.
Unknown FAILED/UNCERTAIN are not silently classified as parser failures.

The existing recover entry point and session manage continuation. It permits
native RESUME of approved initial partial jobs; subsequent automatic continuation
is limited to safe time/page/product-budget boundaries and the signed ceiling.
Systemic errors still stop the batch. Existing settings activation/restore and
max2different-host leases remain mandatory. New HEAD/engine/approval SHA requires
explicit operator confirmation. Permission to implement is not that confirmation.
Do not run old RESUME_APPROVED.ps1 or an already consumed post-stop command.

After reboot: inspect actual target PG17.11/system7698346631476390904/55461,
original checkpoint/SQL receipts/active leases/settings before a confirmed recover.
Never initdb, overwrite PGDATA, switch cluster, or silently clear writer/pid locks.
Check settings restored and0active after session shutdown; preserve all journals.
Saved actual BMDO and59COMMITTED checkpoint fixtures are offline evidence only.


## Semantic quarantine is not a consecutive structural error — 2026-10-23

LIVE session37491ed0… demonstrated a classification defect: the three captured
BMSO C-NC/CS, GLS-NC/GLS and GS-NC/GS contradictions after59COMMITTED were counted
as consecutive source/parser failures. The native safety STOP and settings restore
worked; nothing was force-resumed. The4semantic quarantines among63attempted cards
are valid evidence, not permission to select a model or strip NC.

Correction: semantic identity quarantines do not increment the structural/HTTP
error counter. Structural parse failures retain the source consecutive threshold;
HTTP breaker/403/429/Retry-After remain unchanged. A semantic source-quality STOP
requires at least safety_gate_size identity quarantines AND at least half of all
actually attempted cards in that phase (including its earlier COMMITTED). This
is a source-quality check, never a product processing cap. Quarantine still does
not count as SQL proof, does not enter matching, and never implies full coverage.
The actual59good+4quarantine replay must continue without SOURCE_CARD_ERROR_THRESHOLD;
a source dominated by identity failures must still STOP. F01/F03/parser/identity
rules are unchanged; all three real followup captures remain rejected by parser.

After a technical STOP, prepare-continuation accepts the previous signed
continuation as its proof-chain input. It resnapshots current native parent/job/
checkpoint/SQL evidence, preserving or reducing the previously signed phase
attempt ceilings, original22scopes/T0/deadline/matrix/host/rate. It cannot replenish
attempts. New exact HEAD/engine/manifest SHA confirmation remains mandatory.


## Proven semantic update rejection — 2026-10-23

Session3a57fc4b… reached422COMMITTED/319unique,3native phases succeeded,19TR
quarantines; then KAMI variant:9833201 / source7188 / protected ProductID146135
triggered SOURCE_UPDATE_SEMANTIC_CONFLICT:EXISTING_LINK_CONFLICT. Current captured
G5-42Y standard conflicts with its retained catalogue family G5-Y/G5-42Y. The
source_update guard and all1282mappings remain unchanged. Native transaction
rolled back, exact capture/observation absent; no model/target override allowed.

SourceBackend now exposes only the unique native SOURCE_UPDATE_SEMANTIC_CONFLICT:
rejection as an isolatable matching quarantine AFTER TransactionStatus.IDLE,
exact SQL capture NOT_FOUND, and one preserved mapped source/target readback.
Non-idle rollback, UNKNOWN/COMMITTED receipt, missing mapping, permissions,
URL/namespace/target/mapping errors remain global STOP. Matching rules/F01/F03
and source_update.py are unchanged. No alternative writer/ingest path exists.
The leased runtime appends QUARANTINED with rejection proof and mapping evidence;
no durable ingest, canonical rewrite or AUTO occurs for the rejected card.
Matching quarantines are not source-layout failures and do not dilute SQL proof.
Incomplete/invalid matching rejection proof classifies UNCERTAIN and blocks
completion/automatic RESUME; final reconciliation cannot advance an uncertainphase.
Existing accepted IDs/manual locks remain operator authority; quarantine requires
REVIEW before any correction of that mapping. Native matching only includes
actually COMMITTED cards. Old historical buckets are not fresh matching.

Real captured KAMI regression fixture retains bytes/hash/IDs; offline tests cover
rollback/status/receipt/mapping failures, unknown SQL and preservation of146135.
After new exact HEAD/SHA confirmation the same batch resumes from current owners,
without re-GET of COMMITTED or the saved KAMI/Vek captures. Prior attempt ceilings
remain R8/D4; proof renewal never replenishes attempts, T0/deadline or scopes.
# 2026-10-23 — explicit operator decision for scope-blocked UNCERTAIN

A scope-blocked transport with no archived response remains UNCERTAIN by default.
Only a signed exact case with an operator authorization hash, unchanged source
row, original checkpoint/owner/input and proven absence of capture, observation
and run history can be proposed as `operator_scope` quarantine. Native runtime
appends the decision through SourceCheckpoint and preserves prior UNCERTAIN.
No response/capture/COMMITTED/matching success is invented. Any SQL ambiguity,
source edit, unknown role/target, unapproved case or HEAD/SHA mismatch remains STOP.
The blocked URL is also denied before HTTP in subsequent phases; no redirect
allowlist expansion or automatic UNKNOWN retry is implied by this decision.

Scope-decision focused QA:95 PASS; runtime/readback follow-up52 PASS (overlap).
Extended regression:210 PASS including2200 saved cards/22phases and F01/F03;
five obsolete four-parameter SQL assertions were updated to the existing typed
nullable-ID contract and pass in the52-case follow-up. Three separate UI-auth
test failures reproduce on prior HEAD016ab79; they are not marked PASS and no
web/security implementation was changed by this recovery task.

The later explicit operator authorization permits a signed class policy for
frozen REFRESH scope refusals and R9 only for parents92–100. Required: exact
frozen entry/identity, preapproved unchanged source-row hash, real job/run/time,
absence of capture/observation/history, immutable refusal evidence, no blocked
target dispatch, no HTTP retry/ingest, preserved UNCERTAIN history. Subsequent
DISCOVERY reuses proof without fetching the same detail. SQL/identity/unsafe
host/other UNCERTAIN failures remain STOP; D4, original budgets and exact HEAD/SHA
guards remain. This policy is not authority to follow redirects or expand scope.

Unified policy/R9 final offline QA:132 focused PASS;253 runtime/batch/F01F03/canary
PASS including2200 saved-model cards/22phases. The3 previously reproduced UI-auth
baseline failures were explicitly deselected from the latter set and remain
documented, not converted to PASS. Safe same-host HTTPS target refusals, including
root redirects, never dispatch; cross-host, credentials, HTTP downgrade, non443
port, fragment, source drift and any SQL ambiguity remain global STOP. Native
DISCOVERY reuse adds no new transport/UNCERTAIN and performs no detail GET.

## 2026-10-26 — explicit R10 / repeated dispatch / execution window

OPERATOR-CONFIRMED: one further REFRESH attempt for the stopped/cancelled parents101–109 (R9→R10), DISCOVERY ceiling remainsD4. The separate operator-extension artifact binds this same batch, exact originalT0/deadline, matrix,22scope/input hashes, previous approvalSHA and execution_until2026-10-27T11:27:36Z. Original SQL snapshots/envelopes/deadline are never edited; without this signed artifact and exact new HEAD/SHA the old window still expires normally. Attempt11, another batch/scope/window or unsigned authorization remains STOP.

A duplicate dispatch denial is distinct from a catalogue size cap: KAMI102/run43 had101 distinct admitted URLs out of5264 and rejected a repeat of KAMI7536/site10154292. Its response is UNKNOWN, not HTTP200 or proof of no first GET. Historic quarantine requires exact stopped job/run/checkpoint, original source hash,0new captures/observations/history and the explicit operator decision. Native leased recovery appends quarantine preserving UNCERTAIN history, never repeats HTTP/ingest and never marks the partial phase complete by hand.

Future signed local scope/duplicate refusals use typed RequestAborted outcomes: no network retry or host-breaker failure is invented for a local denial. Remaining role/SQL/identity/host/HTTP403/429/unsafe-target/UNCERTAIN guards retain STOP behavior. Frozen per-URL attempts, catalogue scopes and host/rate/max2 are unchanged.

Matching-only884 COMMITTED (Vek549/KAMI335) was durably applied with no HTTP/ingest, eight new product_matches events, no other business-table changes,1282original mappings/manual locks preserved. Signed before/durable artifacts and native receipt fingerprints support reuse inside normal leased checkpoint reconciliation; no checkpoint is edited by the matching helper. Verified receipts are reconciled through one existing atomic record_terminal batch rather than N full-checkpoint rewrites. No new scheduler, API, schema or matcher.

QA:185 focused PASS after final edits,347 broader regression PASS (overlap), including saved-model2200/22phase replay. Three known pre-existing UI-auth failures are explicitly deselected, not PASS; no UI/auth code changes. Artifacts under workspace reports/RC_LIVE11_FULL_COMPLETION_20261023/qa-monday-r10*.xml. Default Windows sandbox pytest-temp ACL errors were handled by running offline QA outside that sandbox in project-local temp folders, without changing ACLs or guards. Live execution of this new code still requires exact HEAD/SHA confirmation.


## 2026-10-26 — authorized timeout isolation / single R11

OPERATOR-CONFIRMED: quarantine exactly KAMI8429/site9226549,8430/site9226571,8431/site9227335 from job111/run46; isolated future TimeoutError may be quarantined only after frozen source-row and zero new capture/observation/history proofs. No response exists for the three historic timeouts: HTTP outcome remains UNKNOWN and UNCERTAIN history is retained. Native checkpoint reconciliation applies the signed decision only inside a leased recovery child. Preparation is SELECT-only; partial jobs are never manually completed.

A separate exact authorization binds the previous approval6 SHA, original batch/T0/deadline/22scope/input hashes, historic IDs, parents110–118 and one R11. D4 and execution_until2026-10-27T11:27:36Z stay unchanged. R12, another parent, D5, changed identity/scopes/window or absent exact HEAD/SHA remain STOP. CLI accepts three distinct operator decision files in one prepare-continuation operation; existing matching-only proof is reused without HTTP/ingest or repeated matching for COMMITTED.

Signed read-timeout transport outcomes do not retry an UNKNOWN request even when the frozen profile permits multiple attempts. They still record a real network error and invoke the SAME native circuit breaker. KAMI's third consecutive failure keeps the trip and cooldown60 and stops the host/session; concurrent success cannot erase the stop event. Other frozen profiles retain their own threshold/cooldown (Partner-ST/Optimum5/30). HTTP403/429, retry-after, SQL/identity/mapping/source drift and unknown exception guards remain intact. Local scope/duplicate refusals still are not counted as network errors. No scheduler/parser/matching/schema/UI/ACL changes.

LIVE-VERIFIED before execution:2948phase COMMITTED/2786unique,1282original mappings exact,0active and11supplier settings restored. Matching-only1733 is durable: four new product_matches events, all other protected business-table counts unchanged; persisted buckets Vek853NEW, KAMI12AUTO/304REVIEW/27CONFLICT/537NEW. All12 durableAUTO preserve original confirmed links; engine proposals8AUTO/308REVIEW differ because accepted decisions stay locked. CP matching-proof metadata for these1733 is applied only by the next native child; helpers do not edit CP files. Existing884 matching proof already stamped during R10.

REPOSITORY-VERIFIED QA:279 focused PASS (3 known pre-existing UI-auth checks explicitly deselected);154 batch/isolation/F01-F03 regression PASS, including2200 saved-model cards over22native phases. Three real saved UNCERTAIN timeout rows replayed; no fabricated HTTP response, no repeated dispatch/ingest, SQL drift rejects, absent authorization/R12/D5/other parent/window reject. Initial two test failures used an incorrect test-only default profile5/30; corrected tests explicitly use approved3/60, without changing production profiles. All final tests PASS; no B0 SQL QA or Partner-ST LIVE50 repeated. R11 native execution remains pending exact new HEAD/SHA.


## 2026-10-26 — one-use autonomous LIVE11 preparation (not execution)

REPOSITORY-VERIFIED: see docs/LIVE11_AUTONOMOUS_24H.md. Existing native queue/Orchestrator/checkpoints/matching only. New local Controller controls clocks, disk/SQL health and60-minute readonly reports; one Windows task starts the same process independently of Codex. No triggers/restarts, InteractiveToken/LeastPrivilege, IgnoreNew, hard limitP1D. Exclusive claim prevents replay even after crash. Idle-sleep request is thread-local and released in finally; screen/power plan/security unchanged.

Proposed exact approval adds only R12 for paused119–127, D4unchanged, one24h process start before2026-10-27T11:27:36Z, absolute ceiling2026-10-28T11:27:36Z. Actual work stops5minutes before the24h cap, with monotonic+UTC checks and a bound session receipt; originalT0/deadline/22scopes/host/rate/max2 remain unchanged. This is an explicit new window proposal, not an inferred permission.

Card-local errors stay in existing durable quarantine/REVIEW/CONFLICT. Critical identity/SQL/checkpoint/approval/robots/host failures, native circuit breaker/mass source errors, lowdisk5GiB or failed reports stop safely. No arbitrary exception suppression or UNKNOWN retry. Pending matching for already COMMITTED and cached responses is applied in native RESUME before fresh HTTP, after approval only. No parser/matching-engine/schema/ACL changes.

Hourly JSON/CSV validates SQL receipts,1282mappings,manual locks and PostgreSQL durability on/on/on. Counts distinguish phase COMMITTED/unique/freshmatching/pending/quarantine, remaining known URLs vs uncovered discovery and inferred ETA. Failure emits UNVERIFIED in both JSON/CSV; never a stale success. No LLM or hourly backup. Initial/final report and10-second heartbeat supplement, not replace CP.

Normal cleanup waits for existing workers then SELECT-verifies exact temporary settings restoration. Evidence-save failure after activation also executes restoration. Sudden kill/power/DB outage may leave RESTORATION_PENDING.json; next action is manual verification, no uncontrolled restart or assurance of cleanup when DB is unavailable.

Windows native smoke probe: independent3second pythonw process, temporary task removed, power request acquired/released,0HTTP/SQL. Read-only sample:3009COMMITTED/2847unique/1282exact/61matchingpending/2cached/0active/settingsbaseline. Code/offline tests do not authorize execution. Current LIVE remains PAUSED; new exact HEAD/SHA plus R12/window/task/power/reporting consent required.


REPOSITORY-VERIFIED final QA:312focusedPASS (3 known pre-existing UI-auth cases explicitly deselected),154broaderPASS including2200saved-model/22phase native replay. PowerShell AST PASS. Native Windows offline probe task exited0, used/released system-required idle request, had no triggers/restarts and was removed; no supplier HTTP/SQL/LLM. Readonly real report validates11sources,3009COMMITTED/2847unique/1282exact/61matchingpending/2cached/0active/settingsrestored. PostgreSQL durability on/on/on; diskD~34GiB/C~60GiB. No full24h LIVE performed.

Intermediate QA failures retained as evidence: historic fixtures initially hit their real expired calendar deadline; tests now freeze only the recorded replay calendar without editing frozen evidence or real guards. An intermediate large replay also correctly rejected an engine fingerprint change while code was being edited; final312/154 runs used stable engine and passed. No B0 SQL QA/Partner-STLIVE50 rerun. New exact operator HEAD/SHA, R12/window and one-shot launch approval remain pending.

Final hourly guard also binds each SQL capture fingerprint to its original external identity; mismatches stop with HOURLY_SQL_RECEIPT_MISMATCH. Real readonly3009receipt/sample and added offline identity-drift case PASS.

Autonomous admission additionally requires the active same-SHA Controller and bound one-use session before enqueue/admission/runtime, including alternate legacy recover paths. A manifest/context alone outside that controlled process cannot enqueue R12. Guard regression PASS before any SQL.
