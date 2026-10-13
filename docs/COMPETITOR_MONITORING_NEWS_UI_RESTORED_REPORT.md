# Competitor monitoring: NEWS UI restored

2026-10-13 · OPERATOR-CONFIRMED UI regression fix.

**COMPETITOR_MONITORING_NEWS_UI_RESTORED**

**WAITING_FOR_OPERATOR_REVIEW — STOP**

## DONE / APPLIED

Native «Новости» tab restored between «Акции» and «Ассортимент».
Uses existing MonitoringService/normalized saved snapshots and the unchanged
change detector. Read projection and template wiring extended; no new backend
subsystem, SQL schema, collection or data import.

Cards show source, published_at, title, excerpt, canonical URL, NEWS vs
BLOG / ARTICLE, original observed_at, and NEW/CHANGED when the detector supplies
an actual corresponding event. Unknown publication dates are labelled; no date
is inferred from file time or URL. Blog never becomes a news item or NEW_NEWS KPI.

Internal «Все / Новости / Статьи-блог» filters retain other query parameters.
Source capability controls visible materials and source options, using existing
capability declarations rather than source-name conditions.
Filters: source, publication date range (observed date fallback), type, only-new,
case-insensitive title/full accepted text search, reset and existing 50-row paging.
Search includes text beyond the abbreviated excerpt. Default view is all saved
accepted materials, including historical records; not a silent seven-day window.
Sort: publication DESC; observed DESC when publication unknown; stable ties.

Overview adds seventh KPI «Новых новостей», counting actual NEW_NEWS events.
Current value 0 — no invented first-observation events. «Последние новости
конкурентов» shows eight current latest saved materials with date/source/title/
type/canonical links; BLOG / ARTICLE is visibly distinguished.

## VERIFIED

REPOSITORY-VERIFIED:

- 218 PASS / 0 FAIL / 0 ERROR / 0 SKIP; one previously excluded SQL mutation
  fixture remains deselected. Prior209 remain passing; nine new NEWS regressions.
- News navigation/order and existing NEWS/BLOG cards; semantic source/type/date
  filters; historical defaults; search beyond excerpt; unknown-date ordering;
  actual NEW / CHANGED and only-new; baseline has no fabricated NEW_NEWS.
- Capability gate, latest Overview block and news KPI.
- Nine protected runner/scheduler/config/CLI/change/price/report/source-capability/
  launcher/core-app files unchanged against published HEAD.
- git diff --check PASS; no code commits or publication.

LIVE-VERIFIED only localhost and read-only competitor PostgreSQL:

- 12 saved materials render immediately: 10 NEWS + 2 BLOG, including an older historical record previously hidden by the business date window.
- NEW_NEWS KPI is 0. No source data changed; all table/source fingerprints equal.
- 67 browser checks PASS: 1920×1080, 1366×768 and mobile375×812, 100% and native
  125% browser zoom; no document overflow or JS errors; all other tabs render.
- Browser blocks every non-local request and every POST. Source HTTP=0,
  collection POST=0. External URLs verified against stored canonical links and
  safe href/target/rel attributes; competitor website availability not re-fetched.
- Existing scheduler remains disabled; no automatic LIVE run.

Legacy supplier suites:41 PASS / same31 FAIL. Exact comparison with previous
baseline, new failures0; missing old fixtures/config expectations not fixed.
Existing Starlette/httpx deprecation warning remains.

## Evidence and screenshots

`work/sterbrust-competitive-intelligence/reports/COMPETITOR_NEWS_UI_2026-10-13/`

TEST_RESULT.xml, NEWS_UI_QA.json, PROTECTED_FILES.json,
LEGACY_TEST_RESULT.xml/.log and LEGACY_COMPARE.json.
Screenshots NEWS_1920_100/125.png, NEWS_1366_100/125.png,
NEWS_375_100/125.png. Browser zoom is native devicePixelRatio1.25, not CSS zoom.

## Scope and unchanged protections

Modified only the existing read projection monitoring.py, native competitor_ui.py,
competitor_monitoring.html and module refinement CSS. Added competitor_news.html,
nine UI regression tests and local QA helper. No collection runner, scheduler,
price/promotion/change engine, matching or supplier integration changes.
Control-plane UI files are the existing host of this module; other Universal
Supplier logic/storage/state, KAMI/Vekprom/Sterbrust/ESOL are untouched.
No production/develop changes, new LIVE crawl, scheduler activation, merge/deploy,
commit/push/PR. HEAD remains e5c3a654dc8f716975961fd338e00664666cfb46.

NEW/CHANGED for BLOG is absent where the current detector supplies no blog events;
no fake BLOG event types were invented. Material detail consists of existing
excerpt plus external canonical link; this task does not crawl full articles.
Operator visual review remains pending.

## Review URL

[Новости](http://127.0.0.1:58124/competitor-monitoring/news).
Local changes await operator review; no next-stage work is performed.
