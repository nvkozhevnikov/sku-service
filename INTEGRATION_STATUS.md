# STERBRUST unified RC — 2026-10-19

## Latest: remaining release gates — offline bindings and churn containment

DONE/APPLIED/VERIFIED: saved churn dispositions+identity hold;11supplier queue
bindings,9new bounded wrappers reusing existing parsers;135uniqueWindows3.12
testsPASS/1preexisting evidenceSKIP. Newsource54testsPASS; no SQL/HTTP/matching
apply,DBcreation or dump rebuild. Existing RC1_VI30artifact seals retained.
BLOCKED: actual Linux3.12 runtime unavailable (only stopped docker-desktop;
Linux Engine pipe missing). Matching semantic adoption remains held; no fake
AUTO acceptance. Live operation not verified by offline tests. Full RC remains
NOT_ACCEPTED_FOR_DEVELOP. [Gate report](docs/unified-rc/RELEASE_GATES_2026-10-19.md).

Older entries below retain their snapshot-specific context.

## Latest: Vekprom/Intervesp INCLUDED — operator-approved continuation

DONE/APPLIED/VERIFIED: VI source restore, pre-change rollback restore, scoped
migration and idempotency PASS;18958products/18954active/19816history. New full
dump+empty-DB restore PASS,133823099bytes/SHA256
`a2c9e3fec764714f00648485dee7c72d7b158439a9863803a722b3080de083c2`.
Source-data blocker below is now historical; both mandatory namespaces included.
New delivery release/DELIVERY_2026-10-19_RC1_VI; older delivery remains sealed.
Existing links/IDs/manual authority/other sources unchanged; no matching applied.
OwnRC stopped. Full release NOT_ACCEPTED_FOR_DEVELOP: matching churn/Linux/live
runner gates remain. [Details](docs/unified-rc/VI_INCLUSION_2026-10-19.md).

The earlier partial-RC record below describes the previous sealed snapshot.

**PARTIAL_RC / NOT_ACCEPTED_FOR_DEVELOP.** Owner: Codex.

`OPERATOR-CONFIRMED`: V2/13_CODEX_NOW; no new collection, source DB writes or
restart, merge/push/PR/deploy/cron/ESOL. Mandatory Vekprom/new Intervesp remain
in scope. Only new isolated database copies were populated.

| Stage | DONE | APPLIED | VERIFIED | BLOCKED | NEXT |
|---|---|---|---|---|---|
| C0 inputs | YES | pinned input records | remote develop58152e4, source Git HEADs, actual dump bytes |55452 portable dump missing | owner-approved source recovery plan only |
| C1 code | YES, scoped integration | own feature only | regression289PASS; GLM F01 review0blocking | live shared runners only Partner-ST/Optimum; other components not certified cron jobs | bounded runner completion after source gate |
| C2 data | available sources YES | own PG17.11/55461, one runtime DB | complete-row/FK/history/namespace/sequence preservation; role isolation PASS | Vekprom + newer Intervesp missing | dump/restore PASS; see receipt |
| C3 panel | YES | existing FastAPI UI/single auth; native competitor tabs | real Edge browser PASS; shared offline dispatch/due replay PASS;22runnable+1blocked | no live crawl/scheduler certification | retain replay-only boundary |
| Matching | offline comparison DONE | original patch + safe F3/doc F5 | Beka+F01 96PASS, targeted3.12Windows289PASS, exact frozen dry-run | Linux unavailable; churn needs review | no automatic reconcile/identity apply |
| C4 delivery | DONE for available-data partial RC | versioned dump/exports/receipts | full logical restore + application readback + export reconciliation PASS | full-release acceptance blocked | owner reviews blockers and recovery scope |

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

## Final portable artifact

LIVE-VERIFIED custom dump:124142732bytes, SHA256`b6858888fd2d370d6973eb309891293e0e8c8e2da1a0d2b884fc63e935aa219e`. Full logical restore into new empty `sterbrust_unified_restore_20261019` in the same isolatedPG17.11 instance: everytable/sequence/constraint/migration-ledger equal. Restored shared-login and supplier/competitor/replay application readbackPASS. No source dumps were restored during this final own-RC dump rehearsal.

Database assembly commit`f9a29bb46b70e13d9198e545edc5ac8464f0fa6e`; final UI-label/readback/receipt commit identified by Git HEAD. New runtime/UI shutdown receipt will state final process status; no source cluster shutdown authorized.

Final display-only follow-up: Russian replay source/status/history labels; real DB page readback and timezone unit test PASS. No business data or identity decisions changed. The dump is the sealed snapshot at assembly time; later QA authentication metadata on the retained working copy is not a source-data refresh.

Runtime final: own55461cluster CLEAN_SERVER_STOPPED; clients/prepared/pending jobs0, controlstate shut down, pidabsent, portclosed. Source clusters unchanged by task actions. RUNTIME_SHUTDOWN.json records the exact owned path/system ID.
