# Five-source onboarding checkpoint — 2026-10-13

COMPETITOR_MONITORING_FIVE_NEW_SOURCES_PARTIAL
WAITING_FOR_OPERATOR_REVIEW / STOP

## REQUESTED

OPERATOR-CONFIRMED: freeze run30 recovery separately and onboard five bounded public competitor identities; preserve the existing six sources. Specification: specs/incoming/2026-10-13_FIVE_NEW_COMPETITOR_SOURCES.txt.

## DONE

REPOSITORY-VERIFIED: recovery-only four-file commit `56d2038b0d4381e77e3bd39cc4f277ce349317f0`, parent `263d7cb6324d3e4d70cd5cdff3cce5deef8319d4`, pushed only feature/competitive-intelligence-kuvalda-metalmaster. Final local/remote feature HEAD equals this recovery SHA. New-source implementation remains uncommitted.

Public source audit precedes onboarding. Five identities and capabilities were added to the existing system, with separate JETTools, jet_online and RuStan identities. No cross-source matching. No separate ingestion subsystem.

## APPLIED

LIVE-VERIFIED: dedicated competitor DB only, 11 registered sources. Existing CollectionService CLI runs 31–34 are terminal; no UI collection or parallel execution. Initial captures and nonfinal parser lineage are retained. Accepted extractor five-sources-1.2 uses saved public DOM and explicit price evidence.

One generic 11-source business preview regenerated at `competitor-data/collection/business-report/index.html`. Existing generic native UI displays all eleven sources and the five new source filters, current facts and source novelty labels. UI restarted only after all CLI runs were terminal and shared kernel lock was free.

## VERIFIED

PACKAGED-EVIDENCE: 236 offline tests PASS, 0 FAIL / ERROR / SKIP, one pre-existing SQL mutation fixture deselected. 50 report tests also passed after subtype-caption refinement. Initial sandbox test attempts failed on Windows temporary-directory access; final test run used the appropriate access without changing assertions.

LIVE-VERIFIED localhost read-only UI: 96 checks PASS, mobile and native 125% zoom, no overflow, eleven source identities, NEWS/BLOG separation. Rendered materials: 13 NEWS + 30 BLOG/other content; new NEWS KPI 0. External HTTP and collection POST during UI QA: 0. Database fingerprints before/after UI QA equal.

PACKAGED-EVIDENCE: all 925 current page/product records checked against hashed saved raw DOM (not just twenty per source). QA checks URL, title, type, date proof, campaign dates, unit-price roles, old-price evidence, discount facts, availability, priority, context identity and capture provenance. Unpublished fields remain NULL. Brand/model tokens extracted from visible titles are context, not cross-source matching evidence.

Saved replay: new pages 0 / new versions 0 / new items 0; all settled DB fingerprints equal. All six original source fingerprints remain equal to before onboarding. New-source nonbaseline events 0. Original run30 remains PARTIAL with finished_at set and 83 observation references retained.

| Source | Status | Pages | Campaign roots | NEWS | BLOG/content | Source novelties | Campaign products | Prices | Explicit old+new | HIGH | QA records |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| jettools_ru | PUBLIC_BASELINE_VERIFIED | 8 | 0 | 0 | 0 | 99 | 0 | 147 | 18 | 96 | 155 |
| rustan | PUBLIC_BASELINE_WITH_GAPS | 23 | 1 | 0 | 20 | 0 | 3 | 3 | 0 | 3 | 26 |
| belmash | PUBLIC_BASELINE_VERIFIED | 58 | 4 | 0 | 0 | 87 | 454 | 484 | 434 | 199 | 544 |
| harvey_rus | PUBLIC_BASELINE_WITH_GAPS | 6 | 0 | 0 | 0 | 18 | 0 | 48 | 11 | 46 | 69 |
| amur_stan | PUBLIC_BASELINE_WITH_GAPS | 19 | 7 | 3 | 7 | 0 | 64 | 64 | 47 | 85 | 131 |

Counts are current accepted pages and unique `(product URL, condition/tab)` product contexts within each source; pagination pages are separate captures, campaign roots are queryless identities. Different source identities are never merged. Prices include explicit FROM_PRICE displayed facts; these are excluded from comparable unit-price changes.

### Source-specific outcomes and gaps

- JETTools: mandatory /catalog/new/ captured with current pagination, 99 explicit novelty labels, SKU/title/model context and original capture times. Old archived news outside the business window was not imported. News 0 means no qualifying captured material, not an HTTP access failure.
- RuStan: /articles stores twenty BLOG articles, not NEWS. Live homepage gift campaign `https://rustan.ru/komplekt-sverl-kobalt-v-podarok` stores its mechanics and three priced products. /actions/ returned 404 during audit; the exact observed /actions works. No gift value used as a price decrease.
- BELMASH: taxonomy preserves PROMOTION, NEWS, NEW_PRODUCT_ANNOUNCEMENT, EVENT, MODERNIZATION and CONTENT. Current accepted content contains one NEW_PRODUCT_ANNOUNCEMENT and four campaign roots; no eligible NEWS/EVENT/MODERNIZATION details captured in the date window. Current autumn campaign has 37 public pagination captures, public_pagination COMPLETE, but comparable-membership/removal coverage remains false. Product scope is not treated as a complete catalogue.
- BELMASH scope incident: 41 general parent-offer pagination URLs were followed in the bounded first CLI pass. Their raw evidence is retained, but those extra pagination pages are excluded from current accepted business facts. This exceeded the intended campaign-only boundary; a regression now prevents campaign pagination from linking into a parent catalogue. No further LIVE crawl was used for calibration.
- Harvey: standard context and outlet instance identities are separate. Explicit old-price styling proven from saved public CSS (`.base__price` line-through), current price excludes old/percent spans. Three audit detail URLs returned HTTP200 but failed strict decoding (UnicodeDecodeError), and remain unresolved extraction gaps. This is not HTTP404. Early CLI runs31/32 failed old-price CSS provenance guard; run33 completed after observed stylesheet selection was corrected. Failed attempts are retained, not relabelled as successful.
- Amur-Stan: 64 explicit FROM_PRICE facts and 48 UNKNOWN/ambiguous commercial contexts; unknown multi-value/commercial-offer amounts never become unit prices. 47 explicit old/current displays belong to FROM_PRICE and are not comparable product price drops. Three dated NEWS and seven BLOG material pages render. Targeted run34 stopped at ten pages with seven dated detail candidates deferred; coverage PARTIAL.

Price-type counts:
- jettools_ru: {'SALE': 18, 'PUBLIC': 129}
- rustan: {'PUBLIC': 3}
- belmash: {'SALE': 434, 'PUBLIC': 50, 'UNKNOWN': 2}
- harvey_rus: {'PUBLIC': 37, 'SALE': 11, 'UNKNOWN': 15}
- amur_stan: {'UNKNOWN': 48, 'FROM_PRICE': 64}

All captured availability values are source-visible. Missing SKU/model/category/availability/dates remain unknown. Source audit HTTP, robots and fetch status are independent; error and not-fetched surfaces are preserved explicitly.

## NOT APPLIED

No new-source commit/push, PR, merge or deployment. No UI/Uvicorn worker-lifetime fix. No scheduler enablement, cron, Windows Task or daemon. Future source-selection capability accepts eleven IDs; the saved default schedule list remains its original six sources and enabled=false. AI remains disabled; no new analysis.

No writes to Sterbrust, ESOL, Universal Supplier, supplier KAMI or Vekprom; only own competitor DB and local review artifacts. No existing competitor recrawl. Protected local and remote develop `e63eb6fe4da0a5124ae98deaa2f5f4313c4a96c3` and production `36924836bb02965e857173773cdb70f302914643` unchanged.

## WHY

Overall status is PARTIAL because Harvey has unresolved decoding gaps and Amur has deferred content. Initial baseline is useful but bounded; source-label novelty and displayed discounts do not authorize invented change events.

Automatic approval review rejected an intermediate proposal to delete run31 observations and create a calibration run because exact authorization for those irreversible mutations was missing. That rejected command was not executed. A safer replay retained every observation, reused original run IDs and created no calibration run. Own nonfinal versions are explicitly marked REJECTED_EXTRACTOR; raw evidence is preserved.

## REMAINING

Operator review of the local five-source implementation, captured facts and coverage gaps. Any requested additional bounded discovery or decoding repair requires a separate next step; no collection remains running. Future comparable LIVE snapshots must use compatible parser and price semantics; recalibration itself is not a daily change.

## DEVELOPER REQUIRED

Separate UI/process lifetime work remains in handoffs/COMPETITOR_COLLECTION_UI_PROCESS_COUPLING.md and docs/COMPETITOR_COLLECTION_UI_PROCESS_COUPLING_HANDOFF.md. Code coupling is REPOSITORY-VERIFIED; exact reason run30 process disappeared remains SUSPECTED. This task did not fix it.

## RISKS / UNKNOWN

Partial source coverage is not proof of absence. Public prices are source facts, not order quotes. Field NULL and decode failures remain explicit. Pagination captures count as pages, not independent campaign launches. No complete membership assertion or false removals. Future runs use fresh transport; initial audit captures cannot be indefinitely reused as current LIVE evidence (regression covered).

## NEXT ACTION

WAITING_FOR_OPERATOR_REVIEW. Review one generic report and the local Sources/News tabs; approve a separate commit/push or a tightly bounded follow-up. STOP.

RUN30_TERMINAL_BEFORE_START = YES; PARALLEL_COLLECTION = NO; SOURCE_COUNT = 11; TESTS = 236 PASS; SCHEDULER_ENABLED = false; PRODUCTION_WRITES = 0.

### Evidence

- reports/FIVE_NEW_SOURCES_2026-10-13/FINAL_QA.json
- reports/FIVE_NEW_SOURCES_2026-10-13/RECORD_QA.json
- reports/FIVE_NEW_SOURCES_2026-10-13/FINAL_SOURCE_MAPS.json
- reports/FIVE_NEW_SOURCES_2026-10-13/CALIBRATION_REPLAY_PROOF.json
- reports/FIVE_NEW_SOURCES_2026-10-13/FINAL_CAPTURE_MANIFEST.json
- reports/FIVE_NEW_SOURCES_2026-10-13/FINAL_REPORT_REBUILD.json
- reports/FIVE_NEW_SOURCES_2026-10-13/TEST_RESULT.xml
- reports/FIVE_NEW_SOURCES_2026-10-13/UI/NEWS_UI_QA.json
- reports/FIVE_NEW_SOURCES_2026-10-13/EXISTING_FACTS_BEFORE.json
- reports/RECOVERY_FREEZE_2026-10-13/TEST_RESULT.xml
- competitor-data/collection/run-31 through run-34 saved source maps/captures/results; no secrets should be exported.
