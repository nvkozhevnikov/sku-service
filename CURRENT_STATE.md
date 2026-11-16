# Current unified feature state — 2026-10-30 (Intervesp 5 s preparation)

## 16Nov — continuity restored locally; RELEASE_READY=NO

LIVE-VERIFIED LOCAL SELECT:59427catalog/18959source products/18959offers/
23475observations/23475captures;1282original IDs exact. PACKAGED-EVIDENCE: actual
117Stage5 and48/206preview bytes recovered;371cohort rows=323unique sources,
all resolved.56Stage5latest receipts synthetic/non-HTTP200;61stale.48updates stale;
206NEWcontent stale, retained for enrichment/review. No current import-ready fields
fabricated. DONE/APPLIED LOCAL publisher/route fix retains48/206 when current0/0;
history117/48/206 and per-record reasons available in loopback panel58161.
NO_UNEXPLAINED_DATA_OR_EXPORT_REGRESSION protects saved identities and previous XML.
PrimarySQLwrites/sourceHTTP/matchingapply0; production/scheduler unchanged.
[Canonical continuity details](docs/unified-rc/XML_CONTINUITY.md), final QA/Git/package
receipt in workspace reports/XML_CONTINUITY_20261116. Previous canary grant consumed;
no new LIVE authorized. Prior entries below are dated historical checkpoints.

## Approved canary completed16Nov — NOT_READY, consumed grant

LIVE-VERIFIED: exact SHA e75cb4d6615e4ce18f9bf1a5b3ea98f25d798db9be27bdb5d68989210bb637ce
on executed HEADeb4adf0, UTC11:02–11:03. Native jobs172Optimum/173Partner partial;
10HTTPstarts(2robots+8unique products),retry0. Optimum6HTTP500; Partner1product200,
next readTimeout UNKNOWN. Captured Partner PP-U3 id293:106083RUB/InStock/qty17,
price_type unknown; NOT SQL committed because safety gate did not complete. No
matching apply. FINAL UNKNOWN_HTTP_OUTCOME;0activejobs;CAS settings/protected old
jobs/1282links SELECT-verified. Commercial/source/catalog/matching/capture tables
unchanged. Only native runtime tracking/temporary settings activation/restoration
were written. Current XML after canary0updates/0new, no historical substitution.

New post-canary dump157884700bytes SHA256
b2f16d08e9c0bbee3fe76dba228bdf24463a86a34e90817ee0ad68b52f370bd0 restored on Windows
and Linux17.11:68table hashes/50sequence states/both ADMIN logins and forced change
PASS. No further HTTP/retries authorized; old grant is consumed regardless of window.
Evidence/report/packages: workspace reports/UNIFIED_RC_CANARY_20261116/post-canary.
SQL-only revised cohort proposal excludes every dispatched URL and adds10machine
Optimum rows; it has NO execution approval. Await operator recovery/source-availability
review, not automatic rerun. Studio integration/scheduler activation remain separate.

## Native canary continuation16Nov — WAITING_EXACT_LIVE_CONFIRMATION

OPERATOR-CONFIRMED integrate native canary, offline QA only until exact LIVE approval.
DONE/APPLIED code: release-commercial-canary/v1 reuses refresh Session/Scheduler/
queue/worker/receipts/STOP.20Partner linked +20Optimum unlinked, positive saved prices/
stock; other9empty/SAVED_ONLY. No retries/resume/activation/matching apply. Legacy1270
guard remains exact.480offline PASS; isolated SQL link-change rollback PASS.
LIVE-VERIFIED LOCAL: original1282 including Sterbrust IDs exact,0active jobs, primary
writes0/sourceHTTP0. Actual final dump now restored on Linux17.11:68table hashes,
50sequence value/is_called, both final ADMIN logins/forced change PASS. Scheduled
monitoring CLI/detector/report/locks verified in isolated fixtures; real schedule not
activated or changed. Content Studio remains separate SQLite/one-time auth/DPAPI;
specific remainder in docs/unified-rc/CONTENT_STUDIO_INTEGRATION_REMAINDER.md.
RELEASE_READY=NO pending exact LIVE permission, actual commercial/XML proof and operator
acceptance. Old proposal-only artifacts remain historical, not runnable grants.
New execution contract/run command: docs/unified-rc/CANARY_EXECUTION.md and external
reports/UNIFIED_RC_CANARY_20261116 receipt. After LIVE regenerate dump/restore/packages.

## Release continuation 2026-11-16

OPERATOR-CONFIRMED existing feature only, ordinary push permitted; Docker only
isolated QA. DONE/APPLIED LOCAL migration019, same auth and five/custom profiles;
stbr_admin_a/id2 and stbr_admin_b/id3 ADMIN, temporary passwords forced change.
Existing rc-review retained. LIVE-VERIFIED LOCAL own-password/mutual-reset/session
revocation; original1282link rows exact. Final dump tested in NEW QA database:
68table hashes and50sequence last_value/is_called identical,0restore errors.
Linux3.12:781PASS/2SKIP plus90guard and57canary/account/deployment tests;
real Linux PG17.11 runtime LOGIN and7application pages PASS. Review8.5/8.5 local.

Current SELECT-only feeds: updates0/new_products0; stale captures were not made
fresh. Sources18959, twelve competitor sources,286pages/1579versions/2948observations/
36781campaign items; latest run61 PARTIAL (28Oct). Daily task last result50 while DB
was stopped: daily success is NOT verified. No supplier HTTP/matching apply/site writes.
Separate <=20/source canary proposal authorized for preparation only; it is not a
scheduler grant or permission to replay historical1270/Intervesp UNCERTAIN receipts.
Content Studio shared auth/PG integration NOT implemented; Applications shows boundary.
Release readiness remains PENDING current commercial canary and portable evidence
completion. Instructions: docs/unified-rc/RELEASE_20261116.md.

Later verification on16Nov: fixed historical coverage=null read-projection crash
(no DB changes), RED1/3 then24Windows+24LinuxGREEN. Actual monitoring read model:
13news/41blog/89promotions/4604products/2165events. Saved220URL smoke now resolves
200HTML/.capture stable IDs and20Optimum pinned Git ID/URL records; no raw Optimum
HTML reparse or LIVE freshness claim. Explicit historical-time generator scenario
48updates/206new products is separate from current0/0 feeds. All4XML well formed.
Both Dockerfile base digests pinned to the already tested Python3.12.16 image.
Final portable evidence:23099/23099references resolved,20475archive members SHA256
verified, no DB reference rewrites.63business tables identical to pre-change restore.
Historical raw-attachment web-link integration still requires target verification.

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

## Owner decision 5 s vs Crawl-delay 20 — DECIDED 2026-11-05: variant A

The owner chose the 5 s interval WITH the signed `robots_policy_exception`.
Successor window drafted and fully validated offline (see
`../../reports/RC_INTERVESP_5S_ROBOTS_EXCEPTION_20261105/`): intervesp rule
re-signed to `min_request_interval_seconds: 5.0` + the exception block bound
to the current robots.txt SHA; the other 10 sources byte-frozen; receipts
597 COMMITTED + 353 quarantined proven reusable; 320 URLs NOT_REQUESTED only.
Execution remains BLOCKED until the owner signs the exact approval SHA and
the window time (RUNBOOK.md §3). Re-sign guards:
commercial_continuation.validate_live, commercial_refresh.matrix_pins,
tests/test_robots_exception_resign.py.

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
