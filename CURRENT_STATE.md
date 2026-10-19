# Universal Supplier KAMI current feature state

OPERATOR-CONFIRMED 2026-10-08, task bf5d1d67; verified data milestone
KAMI_MATCHING_POLICY_V2_1_APPLIED_AND_VERIFIED. Final feature Git/checkpoint status
is recorded after its gates in reports/KAMI_MATCHING_POLICY_V2_1_FINALIZATION_2026-10-08/
REPORT.md,CHECKPOINT.json,GIT_FINAL_STATE.json (not inferred from this document).

DONE: token-aware plus and role-aligned material characteristic corrections,
supplier-neutral V2.1 library, frozen proposal/regression/QA artifacts.
APPLIED:717 accepted KAMI matching links only through normal lifecycle.
VERIFIED:5264 source identities/offers,717Existing/4547Review/0Conflict; exact
canonical IDs/PK/namespace hashes; second matching semantic delta0, expected
validation timestamps only. Numeric-public1333, matched90 (88exact+2FROM_PRICE),
POR617/missing10. QA234, confirmed false positives0 (Codex evidence inspection,
not independent human ground truth).35calibrations/233focused tests previously PASS;
current finalization rerun/dump/restore results must be read from receipts.

Original isolated DB127.0.0.1:55451/universal_supplier_kami_rc/PG17.11/system
7693098812512405428. Supplier disabled,5264offers inactive. Stored selection
unchanged121rows:117selected/4noeligible. No selection reevaluation in finalization.
Source facts/price/availability/old5namespaces preserved; old506Existing/2FULL/
3640Review/259Conflict proposal remains separate advisory, not fabricated SQL links.

Feature-only commit/push now authorized after fresh preflight/tests/new dump/new
restore/all business hashes/secret+diff gates. Starting feature c6e26ec242899535dcef06f90a789a0a5c0110d0;
exact final SHA is external final Git receipt (avoids self-referential commit hash).
Developdeefb595868d19ba471b9b943ff779194e231c26 and production36924836bb02965e857173773cdb70f302914643
must remain untouched. No release tag, merge, supplier HTTP, live prices/availability
update, NEW create, XML publication/import, ESOL, scheduler or deployment.
Stage6 remains BLOCKED_IMPORT_CONTRACT_EVIDENCE. Prior dumps are historical,
never overwrite them or restore over current RC. This is not production readiness.

# Local F01/F03 code correction — 2026-10-19

OPERATOR-CONFIRMED isolated feature:
`feature/sterbrust-matching-f01-f03-20261019`, exact base
`58152e4bd390361d3e97ee475bae78a91295e241`.
REPOSITORY-VERIFIED: owned raw-title model retained for strict checks in both
constructors; property duplicates/units retained with explicit critical ambiguity.
See docs/STRICT_IDENTITY_KEYS.md for compatibility and FALSE_MATCH_CASES.md for
regression scope. Local code only; no DB writes/matching apply/push/deployment.
F02/R04/R05 remain separate. Exact feature HEAD and test receipts are in the
operator handoff; previous checkpoints remain historical evidence.
