# Current unified feature state — 2026-10-30 (Intervesp 5 s preparation)

Base HEAD at session start: `59b3f563c851ec93364740dfd236392aa94ed8ed`
(feature/sterbrust-autonomous-system-20261026, clean tree). New HEAD after the
robots-exception change: see `git rev-parse HEAD`; engine fingerprint re-pinned
by any new approval.

## Intervesp status (fact base, verified 2026-10-30)

- Scope of the current commercial REFRESH window for intervesp: exactly 320
  pinned URLs (job-170 `input.json`), checkpoint frontier 320, terminal 0.
- job-169 (2026-10-28) stopped `SOURCE_ROBOTS_UNAVAILABLE` — pre-TLS-fix.
- job-170 (2026-10-29) fetched robots.txt over the fixed TLS stack
  (1 request, HTTP 200, capture SHA256 `a6862161f2c07c36f0ecc789bf7d31cd48ef41b02c142c41d7f7c4e861d53f51`)
  and stopped штатно: `ROBOTS_REQUIRES_SLOWER_PROFILE`, `stop_scope=supplier`.
  Cause: intervesp.ru/robots.txt declares a single `User-Agent: *` section with
  `Crawl-delay: 20`; the job profile had `min_request_interval_seconds: 1.0`.
- Window accounting (last approved commercial window, SQL/session verified):
  1282 confirmed mappings intact; 597 COMMITTED (beka_mak 14, kami 503,
  ltt_msk 2, ltt_sib 1, partner_st 48, uss_stanko 1, vekprom 28);
  353 quarantined (kami 280, partner_st 69, vekprom 4); 320 intervesp URLs
  proven NOT_REQUESTED; 0 active jobs; 597+353+320=1270 exact scope.
- PostgreSQL 127.0.0.1:55461/rc_verify_vi_20261019 (PG 17.11,
  system 7698346631476390904) restarted after Windows reboot via pg_ctl with
  stale `postmaster.pid` preserved as `postmaster.pid.original.*`; clean WAL
  redo, identity re-verified. Procedure: RUNBOOK.md.

## Owner decision 5 s vs Crawl-delay 20 — NOT YET GIVEN

The desired 5 s interval is FASTER than the robots-declared 20 s. The runtime
robots guard (source_runtime.robots) stops any profile faster than the declared
minimum. This conflict is not hidden and not bypassed: a 5 s run REQUIRES an
explicit operator-signed `robots_policy_exception` block in the approval
document. The mechanism is implemented and offline-tested (this commit);
it is INERT unless the owner signs an approval containing it. See
ACCEPTANCE_MATRIX.md (2026-10-30 section) and RUNBOOK.md for the exact LIVE
conditions. The owner may instead choose the robots-compliant 20 s profile
(320 URLs ≈ 107 min pure pacing) or obtaining source permission.

## Historical state below is retained as history

---

# Unified readiness — 2026-10-20 (historical)

REPOSITORY-VERIFIED continuation after c4424f28. Current report: workspace reports/RC_UNIFIED_READINESS_CODE_20261020/REPORT.md; committed implementation summary docs/UNIFIED_READINESS_20261020.md. A/B/C fixed/verified offline and read-only golden; D/E prepared components, not full operational discovery. No new SQL write or HTTP. MainRC/source databases unchanged, own read-only55461 smart-stopped. Current TEST_AFTER preserved; SQL strict-idempotency re-QA remains PENDING. Linux3.12 and live/full11 gates remain open. Final HEAD and test-specific HEAD in report/GIT_RECEIPT. No develop transfer.

Historical state below is retained as history; it is not a newer KAMI-only checkpoint.

---

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
