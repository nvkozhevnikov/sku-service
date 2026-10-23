# Safe automatic matching — initial application pending

OPERATOR-CONFIRMED: automatic matching is required in the final system;
permanent MATCHING HOLD is NOT_ACCEPTED. This change reuses F01/F03 unchanged.

## Normal execution

All 11 registered supplier adapters use `automatic_matching.run_post_ingest`.
Partner-ST/Optimum retain their existing post-ingest hook. The other nine call
it once for the successfully persisted batch, including idempotent source
replays. Offline parser-only checks do not connect to SQL. Source capture,
network authorization, rate limits and scheduler policies remain separate.

The planner consumes normalized property rows plus retained source_content
observations. It does not duplicate a raw observation as a unitless copy of
its normalized row. Only an explicitly declared unit is carried from a label.
Explicit saved brand fields, or a literal brand in the title corroborated by
exact model/article retrieval, can supply brand evidence. Supplier/domain names
are never brand proof. Explicit manufacturer articles have a retrieval index;
the existing matcher still decides identity and guards duplicates/executions.

Existing IDs are directly checked against that same target, without fuzzy
retargeting. Their mappings and product_matches history receive no writes.
New unlinked exact matches can AUTO ACCEPT; ambiguous/weak evidence is REVIEW;
proven contradictions are CONFLICT; NEW is a proposal, never automatic product
creation. Intra-card critical ambiguity is REVIEW with the original F01/F03
blocking evidence retained. Reference-only accessory identity never AUTO.

The writer rechecks the source row under the existing case/source locks. If
another actor has already assigned a catalog ID it returns
PRESERVED_EXISTING_MAPPING before any mutation. Existing operator rejection and
final NEW/ACCESSORY guards still execute for unlinked proposals. Only actual
guarded persisted decisions are materialized for the panel, and review sync is
limited to affected source IDs. No offer selection or canonical creation runs.

## RC development boundary

Pinned RC1 at localhost:55461 and STERBRUST_UNIFIED_RC=YES calculate automatically
without SQL writes until separate initial-application authorization is given.
The operator-controlled launch flag STERBRUST_RC_MATCHING_APPLY_APPROVED=YES
permits the guarded writer; it cannot change any matching rule. It has **not**
been enabled in this task. There is no panel endpoint for bypassing safeguards.
This temporary development boundary is not an acceptable permanent final mode.

The existing authenticated panel now has /automatic-matching. Optional
AUTOMATIC_MATCHING_REPORT points to the generated SUMMARY.json; the page labels
its numbers as saved offline results, separately from actual SQL operation.

## Evidence and remaining checks

Workspace evidence: reports/RC_AUTOMATIC_MATCHING_20261019. Complete dry-run:
18,958 cards, 798 AUTO / 6,246 REVIEW / 1,402 CONFLICT / 10,508 NEW / 4 inactive.
Of AUTO, 350 are existing IDs; 448 are proposed new links. All 834 existing IDs
remain preserved, including 160 REVIEW and 324 CONFLICT warnings.

370 offline Python3.12/Windows regressions passed. These tests, saved-data
guard rechecks and deterministic ledger simulation do not prove SQL idempotency
or Linux operation. Separate authorization is required for an isolated SQL test
against the exact proposed scope. No matching apply, PostgreSQL startup, live
HTTP, migrations, dump rebuild, production/develop/push/deploy/cron/ESOL occurred.


## LIVE11 URL isolation boundary — 2026-10-23

The matching engine, F01/F03 and matching rules are unchanged. A conflicting
page identity (BMDO120 URL/SKU versus BMDO130 H1/name) is quarantined BEFORE ingest;
neither value is selected and it is never AUTO/NEW canonical creation. Only
actual COMMITTED external IDs reach the existing scoped matching engine. Durable
matching receipts prevent repeat work on already completed cards; pending113
Vekprom cards must receive fresh native matching and SQL readback after approval.
Review locks, accepted Product IDs, supplier namespaces and original1282mappings
remain protected. REVIEW/CONFLICT totals are actual durable buckets, not parser
errors or quarantine counts. Existing historical buckets are not fresh results.


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
