# STERBRUST unified RC — 2026-10-19

**PARTIAL_RC / NOT_ACCEPTED_FOR_DEVELOP.** Owner: Codex.

`OPERATOR-CONFIRMED`: V2/13_CODEX_NOW; no new collection, source DB writes or
restart, merge/push/PR/deploy/cron/ESOL. Mandatory Vekprom/new Intervesp remain
in scope. Only new isolated database copies were populated.

| Stage | DONE | APPLIED | VERIFIED | BLOCKED | NEXT |
|---|---|---|---|---|---|
| C0 inputs | YES | pinned input records | remote develop58152e4, source Git HEADs, actual dump bytes |55452 portable dump missing | owner-approved source recovery plan only |
| C1 code | YES, scoped integration | own feature only | regression289PASS; GLM F01 review0blocking | live shared runners only Partner-ST/Optimum; other components not certified cron jobs | bounded runner completion after source gate |
| C2 data | available sources YES | own PG17.11/55461, one runtime DB | complete-row/FK/history/namespace/sequence preservation; role isolation PASS | Vekprom + newer Intervesp missing | finish custom dump/restore receipt |
| C3 panel | YES | existing FastAPI UI/single auth; native competitor tabs | real Edge browser PASS; shared offline dispatch/due replay PASS;22runnable+1blocked | no live crawl/scheduler certification | retain replay-only boundary |
| Matching | offline comparison DONE | original patch + safe F3/doc F5 | Beka+F01 96PASS, targeted3.12Windows289PASS, exact frozen dry-run | Linux unavailable; churn needs review | no automatic reconcile/identity apply |
| C4 delivery | IN_PROGRESS | local exports/draft docs | export readback in progress | full-release acceptance blocked | dump/restore + final receipt |

## Actual data and acceptance

`LIVE-VERIFIED`:12124source_products/12124offers,12120active. Base6supplier
namespaces plus4LTT/USS;2453LTT/USS records include4inactive editorial pages.
Historical dates/NULL prices/current selections/identity/history retained.
Competitor dedicated schema:12sources/280pages/998versions/1339observations/
20285campaign_items/33runs/815surfaces/5264historical seeds.

`REPOSITORY-VERIFIED`: original F01/F03 at0c0164a incorporated unchanged in
c720a3d (whitespace already normalized in source). GLM review source is pinned
in docs/unified-rc. Safe optional F3 removes tuple repr dependency; F5 clarifies
raw-card versus sorted matching-view semantics. F4 deferred to avoid silently
discarding raw unit evidence. F6 deferred: suggested default resolver does not
cover canonical motor_power etc; a policy-vocabulary change needs separate QA.

`REPOSITORY-VERIFIED` + copied SQL:12120active cards, two passes per engine,
24240decisions each. With-links210AUTO downgrades/102promotions; fresh207/14.
Persisted current-auto834:452not-auto after, of which126newly downgraded and
326already not-auto before.9additional stored links propose a different ID;
no proposals applied. Manual real rows0; real-manual replay NOT_PROVEN.
Synthetic manual preservation passed. Full change/reason CSVs remain external.

`LIVE-VERIFIED`: exports use373persisted/replay-consistent confirmed rows;
461stored links are moved to REVIEW in exports only, retaining prior canonical
ID in a separate field.11747review rows.53competitor-news rows. NEW/PRICE
operation buckets0 with explicit missing approval/comparable-price reasons.
No ready-for-import or refreshed-price claim.

## Known limitations and safety

- `BLOCKED_MISSING_VERIFIED_SOURCE_DATA`: Vekprom6834historical records and
  newer Intervesp state expected from55452. Their source remains mandatory.
  Old Intervesp1642rows in KAMI aggregate are explicitly historical, not a
  substitute for the newer source. Checkpoint counts are PACKAGED-EVIDENCE.
- Bounded backup search found only older01.10Intervesp dumps of
  universal_supplier_server/PG17.11, not55452final state. No restore of those.
- 55452source PGDATA control state`in production`, retainedPID3320file,
  process3320absent/read-only connection timeout. Not a proven clean shutdown.
  No PID deletion, startup/recovery or source alteration performed.
- Linux3.12 not executed: Docker Engine stopped, only docker-desktop WSL distro;
  starting it can start unrelated containers, so it was not activated.
- Historical page-role seal test is not accepted as current integration proof:
  it requires archived transition evidence and old whole-file hashes. It was
  not weakened; current adapter gates/frozen fixtures passed independently.
- Cron-ready here means bounded frozen replay dispatch, with a disabled
  schedule and explicit simulated due evaluation. Live all-site shared runners
  are NOT_VERIFIED, and this RC cannot be called fully operational.
- Stage6 BLOCKED_IMPORT_CONTRACT_EVIDENCE;develop/production unchanged.

## Evidence locations

Tracked evidence summaries: docs/unified-rc. Full private/large data receipts:
reports/integration. Versioned external delivery:
release/DELIVERY_2026-10-19_RC1. Runtime secrets only ignored reports/integration/
private-*.json. No .env/secret/dump/PGDATA is Git-tracked.

NEEDS_OWNER_APPROVAL: source55452 recovery/backup scope; future Linux isolated
test scope; final develop transfer after all mandatory gates. No transfer now.
