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
