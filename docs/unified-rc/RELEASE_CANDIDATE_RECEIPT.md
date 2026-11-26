# STERBRUST release candidate receipt — PARTIAL / NOT_ACCEPTED_FOR_DEVELOP

OPERATOR-CONFIRMED scope: unified local RC only. No new supplier/competitor HTTP,
source DB mutation/start/restart, push/PR/merge/deploy/cron activation/ESOL.

DONE: reviewed original F01/F03, selective feature ports, existing unified panel,
new populated PostgreSQL17.11 with competitor schema, explicit PK mapping,
history/NULL/timestamp preservation, real browser QA, manual/scheduled frozen
replay dispatch, current-data dry-run, verified portable dump and human exports.
APPLIED: only new feature and own isolated DB/staging/rehearsal targets.
VERIFIED:289focusedPython3.12WindowsPASS;96Beka/F01PASS; browser/role/DB/replay/
export readbackPASS. Full restore equals all tables/sequences/constraints/ledger;
restored UI login/readbackPASS. These overlapping test counts are not summed.
BLOCKED: mandatory Vekprom/new Intervesp data55452; Linux3.12; matching churn
review; all-site shared LIVE runners not certified. Matching gate NOT_VERIFIED.
NEXT: owner evaluates exact source55452recovery plan and remaining acceptance
work. No develop transfer recommended now.

## Git and preservation

Branch:feature/sterbrust-unified-rc-20261019.
Base remote develop58152e4bd390361d3e97ee475bae78a91295e241.
Original F01/F03 source0c0164a5ea49659a895866766731bf83adddea76;
original patch integration commitc720a3d. Database assembly commit:
f9a29bb46b70e13d9198e545edc5ac8464f0fa6e. Final UI-label/readback/receipt HEAD is provided
by final Git verification. Shared CI/auth/heartbeat/scheduler/strict-key bytes
remain protected as described in PRESERVED_SECOND_DEV_CHANGES.md.
Remote develop independently rechecked58152e4;production36924836 unchanged.
Local develop remains e63eb6f (not moved); other worktrees retain original HEADs.

## Database and export scope

12124source_products/offers;12120active,10populatedsupplier namespaces.
All11mandatory supplier source sites remain in SOURCE_RELEASE_MATRIX.csv;
Vekprom0target/6834historicalexpected, newerIntervesp blocked; oldIntervesp1642
is explicitly historical. Competitor12sources/280pages/998versions/1339observations/
20285campaign_items/33runs/815surfaces/5264seeds. Canonical catalog59427IDs retained.

Dump:database/sterbrust_unified_2026-10-19_RC1.dump
Bytes:124142732
SHA256:b6858888fd2d370d6973eb309891293e0e8c8e2da1a0d2b884fc63e935aa219e
Format:pg_dump custom --no-owner --no-acl; PostgreSQL17.11.
Restore:new empty database in same own isolated instance; not a fresh physical
cluster claim. Role provisioning explicitly rehearsed because ACL omitted.
No secrets/PGDATA/dumps in Git; manifests and receipts included separately.

Exports:373persisted/replay-consistent confirmed,11747REVIEW,12120active trace,
53competitor news.461stored links are moved to REVIEW in export only; original
canonicalID retained in persisted_sterbrust_product_id. Zero NEW/PRICE operations
are legitimate empty buckets with explicit reasons: no persisted FULLNEWapproval
or comparable canonical price delta. No fabricated canonical IDs or site import.

## F01/F03 conditional gate

Exact original engines pre/post,24240decisions each. With-links210AUTO downgrades
and102promotions; fresh207/14. Among834persistedauto:126newlydowngraded;
326already not-auto before;9additional different-candidate proposals. No
matching/identity/link writes. Manual real rows0; synthetic preservationPASS,
not proof of real manual-history replay. Beka two historical tests found and
replayed from copied inputs. Linux unavailable without activating stopped
Docker/WSL; not run. OptionalF3/docF5 safe;F4/F6deferred with reasons.

## 55452 backup prerequisite

Read-only control shows in-production state + retainedPID3320; process absent;
pg_ctl status reports no server running; source connection timesout. Not a
verified clean shutdown. Logical pg_dump cannot read offline PGDATA.
SOURCE_55452_BACKUP_PLAN.md defines a separate owner-approved cold physical
backup and isolated recovery-copy route. Recovery writes only on that copy,
source untouched; source startup/PID cleanup/WAL reset remain forbidden.
Any newly obtained dump restore requires separate explicit approval.

NEEDS_OWNER_APPROVAL: recovery-copy scope/exception and disk/port; eventual
Linux isolation; later develop transfer only after mandatory release gates.
Stage6 remains BLOCKED_IMPORT_CONTRACT_EVIDENCE.

Final display-only follow-up: Russian replay source/status/history labels; real DB page readback and timezone unit test PASS. No business data or identity decisions changed. The dump is the sealed snapshot at assembly time; later QA authentication metadata on the retained working copy is not a source-data refresh.

Runtime final: own55461cluster CLEAN_SERVER_STOPPED; clients/prepared/pending jobs0, controlstate shut down, pidabsent, portclosed. Source clusters unchanged by task actions. RUNTIME_SHUTDOWN.json records the exact owned path/system ID.
