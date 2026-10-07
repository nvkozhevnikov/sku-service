# Effective manual overlay guard

Bounded operator scope a37a2364, 2026-10-06. No RC matching/application write.

Migration 013 has append-only review_decisions with previous_decision_id;
the latest decision is ordered by created_at DESC,id DESC. There is one
review_cases row per source. AdminStore.decide locks that case, appends the
decision and changes queue resolution; only MANUAL_CONFIRMED sets the physical
source link. It refuses a conflicting existing physical target.

AdminStore.sync_reviews selects the latest FINAL_MANUAL_DECISIONS entry,
ignoring POSTPONED. plan_review_sync preserves that manual disposition even
when identity fingerprint changes; fingerprint is provenance, not revocation.
POSTPONED changes queue scheduling, not the physical mapping. Candidate
rejections are retrieval exclusions, not mapping revocations. Therefore the
guard uses the latest non-POSTPONED decision (exact current schema enum), not
any historical MANUAL_CONFIRMED, queue status alone, or fingerprint equality.
A newer final MANUAL_ACCESSORY/MANUAL_CONFIRMED_NEW/MARKED_REVIEWED supersedes
the previous confirmation. No new rejection/revocation semantics are invented.

Quarantine still requires prior automatic acceptance, a real nonaccepted
persisted transition, and the exact prior target currently on that source.
It additionally excludes a latest effective MANUAL_CONFIRMED for that exact
source and catalog target. Different-target/different-source history does not
protect the link. Existing-link provenance uses the same latest-final rule.

Transaction boundary: persist_match locks the review case before source, the
same order as AdminStore.decide, then current match and appends matching history.
The guarded UPDATE and manual ledger check execute inside that transaction.
Admin decisions for an existing case serialize on its row lock. If a case is
newly inserted concurrently, confirmation must acquire the source lock and
can only confirm/set the link before or after the matcher transaction; it
cannot interleave a confirmation between the guard and UPDATE. No manual
ledger/history rows are edited. VALIDATED_UNCHANGED behavior is unchanged.

Scope does not solve future mapping-revocation UX or implement a new manual
decision class. It does not grant operational authority to advisory matching.
Protected physical links remain eligible under existing canonical selection;
automatic REVIEW history and manual confirmation remain distinct evidence.

Tests: existing scratch pack plus same-target REVIEW/CONFLICT/repeated review,
different-target, latest-final supersession, POSTPONED scheduling, rollback,
automatic NEW_CANDIDATE, selection apply twice. No live supplier requests.

## Effective identity consumers — bounded scope 2026-10-07

OPERATOR-CONFIRMED attachment14bc6b7f: effective accepted identity is current
persisted accepted match for the exact physical source/catalog relation OR
latest-final same-source/same-target MANUAL_CONFIRMED. Without either it is
not accepted. Physical link alone/non-auto flag alone/history alone are not
accepted identity. Quarantine protection semantics above remain intact.

Repository module effective_identity.py owns manual-ledger SQL, current-match
SQL and the pure accepted-identity predicate. PostgresRepository quarantine
and existing-link contexts and ControlPlaneStore confirmed export identity
use these reusable concepts. Contexts no longer infer human authority merely
from a missing/non-auto current match when no accepted relation exists.

PostgresFeedStore supplies manual source/catalog anchors from that same SQL;
FeedRow retains original automatic current status/target separately. A manual
confirmation does not rewrite REVIEW/CONFLICT as automatic EXACT. Feed
validation accepts its exact effective manual authority, including when the
automatic candidate target differs. Supplementary automatic identity-decision
state cannot revoke a proved manual target. All commercial/active/graph guards
remain unchanged. The operator's manual decision does not activate supplier,
offer or scheduler and does not assign invented canonical IDs.

XML canonical preview calls the same selected_row_violations/build_yml
validation. Supplier diagnostic XML uses confirmed identity from the shared
ControlPlaneStore query. ESOL format/write contract remains unconfirmed.
Release exporter remains a frozen advisory-input serializer, not a SQL/manual
identity resolver; if explicit persisted effective_identity_evidence is given,
it is validated by the same predicate. No operational F-P1 export assembly
is implemented or published by this bounded scope. Offline NEW readiness
still uses its independent strict proposal gates; no SQL human-state inference
or automatic promotion is introduced there. Admin review history/queue
semantics remain unchanged; only confirmed-link retrieval is unified.

Selection ranking/policy untouched. Existing freshness policy + canonical
evaluate/apply removes stale offers; feed still rejects disabled/inactive or
ineligible rows. Known false-match XML hard guards remain unchanged. No
fuzzy/alias/product-kind guard is relaxed.

Final scratch37PASS twice; focused287PASS+6subtests. Eight historical
artifact-dependent checks are explicitly not run in the final bounded runtime
suite (missing baseline/OPTIMUM_IDENTITY_AUDIT.csv); this is not full-suite
green or release acceptance. Exact dispositions/evidence in
MANUAL_IDENTITY_CONTRACT_2026-10-07. RC read-only only; stop after verification.
