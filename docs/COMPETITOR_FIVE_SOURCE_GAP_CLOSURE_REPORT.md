# Five-source onboarding gap closure — 2026-10-13

COMPETITOR_MONITORING_FIVE_NEW_SOURCES_VERIFIED
WAITING_FOR_OPERATOR_REVIEW / STOP

Verification covers the three explicitly authorized onboarding gaps. It does
not assert exhaustive competitor coverage or repair the separate UI lifetime
defect. Implementation is uncommitted/unpushed for operator review.

## REQUESTED

OPERATOR-CONFIRMED: specification111d7383-cc21-4bf5-a7db-b2cae15e12e9;
START_SHA2163d7b2679545a55d7790f5f0b27cd32af6b9f0, correct feature branch,
initial checkout clean. Only three saved Harvey decode failures, seven exact
run34 deferred Amur URLs and eleven-source future schedule selection.

## DONE

REPOSITORY-VERIFIED: source-scoped Harvey decoder with strict charset/BOM/meta
attempts and an evidence-verified deterministic damaged-teaser fallback.
No errors="ignore", guessed letters or blanket replacement decoding.
Future default selected_sources updated to eleven unique registered IDs;
enabled=false unchanged. Focused regressions added; established suite preserved.

## APPLIED

LIVE-VERIFIED isolated competitor DB: shared CLI execution contract,
run35 CLI_MANUAL COMPLETED, three Harvey pages and seven Amur pages accepted.
Three primary Harvey outlet products and sixty article/context product card
rows stored. No collection from Uvicorn, parallel collection or UI restart.
Existing generic report rebuilt; existing UI read projection renders accepted data.

## VERIFIED

- HARVEY_DECODE_GAPS_BEFORE=3; HARVEY_DECODE_GAPS_AFTER=0.
- AMUR_DEFERRED_BEFORE=7; AMUR_DEFERRED_AFTER=0.
- HTTP_GETS=7: Harvey0(saved bytes), Amur7, robots0, other competitors0.
- SOURCE_COUNT=11; SCHEDULE_SOURCE_COUNT=11; SCHEDULER_ENABLED=false.
- TESTS=244 PASS /0 FAIL /0 ERROR /0 SKIP /1 existing SQL fixture DESELECTED.
- Focused decode/outlet/baseline/schedule suite20 PASS.
- Read-only localhost UI24 checks PASS, including mobile, no overflow,
  all seven Amur canonical links, NEWS/BLOG separation and Harvey outlet facts.
- NEW_FALSE_EVENTS=0; new NEWS KPI0. No changes invented from gap completion.
- Saved replay:new pages0 /new versions0 /new items0; settled DB fingerprints equal.
- EXISTING_SOURCE_FINGERPRINTS_UNCHANGED=YES for all nine protected sources.
- Every previously existing DB fact row is unchanged, including older Harvey
  and Amur pages/versions/items/observations. Only new exact-target facts/run35
  and their accepted surface evidence were appended. RUN30 remains terminal.
- After CLI all runs terminal, kernel lock free, no automatic run.
- Feature HEAD remains2163d7b; protected local develop e63eb6fe4da0a5124ae98deaa2f5f4313c4a96c3
  and production36924836bb02965e857173773cdb70f302914643 unchanged.
- git diff --check PASS. No commit/push/merge/deploy.

### Harvey root cause and decoding evidence

PACKAGED-EVIDENCE: each retained HTTP200 raw response contains exactly one
orphan byte D0 before a literal ellipsis, in `.new-detail__title-info` text.
Every other byte strictly decodes as UTF8, including Cyrillic title and prices.
This is malformed source text, not evidence of a whole-page legacy charset.
Byte offsets:73651 /75261 /74722. BOM absent; no HTML charset declaration.
HTTP headers/Content-Type were not retained by the original transport and
remain UNKNOWN; they were not invented or inferred from HTTP200.
INFERENCE: source-side teaser truncation split a multi-byte Cyrillic character.

Fallback accepts only the exact verified one-byte/ellipsis pattern in this
non-commercial DOM block, represents the byte as [INCOMPLETE_UTF8_D0], records
offset/raw hex, and preserves the original bytes/hash without alteration.
Other invalid bytes, multiple damage spans, damage outside the teaser or in a
price context fail closed. The missing letter remains UNKNOWN, not guessed.
No new Harvey GET was needed because the saved byte evidence was sufficient.

Old price proof uses the already retained, hash-checked public CSS evidence
from run33: `.base__price` has line-through. Percent spans are excluded.
All three detail prices independently retain normal!=outlet identity and
CONDITION:OUTLET context; no standard/outlet price comparison was introduced.

| Exact Harvey detail suffix | Current RUB | Explicit old RUB |
|---|---:|---:|
| jib-tbs-356-10-lentochnopilnyy-stanok-diskont-341p/ |102680|120800|
| jib-tbs-356-12-lentochnopilnyy-stanok-diskont-341p/ |120530|141800|
| jib-vsm-50-frezernyy-stanok-diskont-349p-4289-2-/ |29040|36300|

### Exact Amur target outcomes

All targets originate from run34.pending and its retained publication listing
proof. No homepage/catalogue recrawl or discovery expansion. Six BLOG articles:

- /articles/gibka-metalla/gidravlicheskiy-ili-elektromehanicheskiy-listogib/ —2026-10-07
- /articles/gibka-metalla/kak-vybrat-listogibochnyy-press-7-parametrov/ —2026-10-07
- /articles/gibka-metalla/listogibochnyy-press-chto-eto/ —2026-10-07
- /articles/gidroabrazivnaya-rezka/gidroabraziv-trebovaniya/ —2026-09-25
- /articles/gidroabrazivnaya-rezka/cena-na-gidroabraziv/ —2026-09-25
- /articles/gidroabrazivnaya-rezka/kak-vybrat-gidro/ —2026-09-25

One /articles/gidroabrazivnaya-rezka/ landing is CONTENT, not an individual
article/news. Its title is the actual generic document title. Date2026-09-25
is preserved listing-associated evidence, not claimed as a printed detail-page
publication date. Exact titles, canonical URLs, dates/proof, raw hashes and
price roles are in ACCEPTED_TARGETS.json and saved version publication_evidence.
FROM_PRICE remains non-comparable, UNKNOWN remains UNKNOWN; old/new roles
come from explicit DOM classes, not monetary position. No campaign relations
or dates were invented where absent.

## NOT APPLIED

No UI redesign, lifecycle coupling fix, AI use, scheduler activation, cron,
Windows Task/daemon installation, automatic run, other competitor HTTP or
other-system mutation. Sterbrust/ESOL/Universal Supplier/supplier KAMI/Vekprom
writes0. No commit/push, PR, merge or deployment.

## WHY

This completes initial baseline/calibration, not a fresh business change.
Existing redesigned monitoring template does not include the pre-existing
automation panel. Its visual eleven-option/OFF checks are NOT APPLICABLE;
the shared config/view proves eleven IDs,enabled=false,running=false.
An initial UI attempt incorrectly assumed the panel was rendered; that failed
attempt is preserved, and applicability was checked from the actual DOM.
No absent-panel check was converted into a fabricated visual PASS.

## REMAINING

The three requested gaps are closed. Operator review remains. Broader source
coverage and the separately recorded UI/process lifetime work are outside scope.

## RISKS / UNKNOWN

The original three teaser characters cannot be reconstructed from truncated
raw bytes and remain explicitly marked. Original HTTP charset headers unknown.
One Amur target is a category/context landing; its date is listing evidence.
Neither issue is concealed as authoritative complete prose/publication data.

## NEXT ACTION

Review the uncommitted feature diff and generic report. Commit/push requires
a separate operator step. WAITING_FOR_OPERATOR_REVIEW / STOP.

Evidence directory: reports/FIVE_SOURCE_GAPS_2026-10-13/
BEFORE_STATE.json, BEFORE_FINGERPRINTS.json, HARVEY_BYTE_AUDIT.json,
ACCEPTED_TARGETS.json, HTTP_REQUESTS.json, RESULT.json, AFTER_STATE.json,
FINAL_PROJECTION_QA.json, UI_QA.json, TEST_RESULT.xml and localhost screenshots.
Existing generic preview:competitor-data/collection/business-report/index.html.
No raw/runtime credentials or DB evidence are staged.
