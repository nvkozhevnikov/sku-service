# Competitor intelligence / 2026-10-09 checkpoint

Status: **COMPETITOR_INTELLIGENCE_SOURCE_BLOCKED**.

REQUESTED: separate cumulative Kuvalda NN / MetalMaster public competitor
promotions/news/current blocks/product prices subsystem; September 2026 scope.

DONE: standalone package/schema/CLI, raw captures, URL identity, meaningful
versions, observations, price roles/Decimal, deterministic priority/reports,
optional saved-version Analyzer protocol and NullAnalyzer. All six Kuvalda winter
tabs and two explicitly linked machine sale categories supported. Near-period
August campaign checked and included by proven overlap, not publication cutoff.

APPLIED: isolated local PG17.11 database sterbrust_competitor_intel, public schema,
competitor_dev, loopback55454, new worktree-owned data directory only. Eight
competitor tables: competitor_meta, competitor_sources, competitor_runs,
competitor_pages, competitor_page_versions, competitor_observations,
competitor_campaign_items, competitor_ai_analysis. Schema version2 includes
QA acceptance metadata; no supplier tables/migrations. Feature-only Git delivery.

VERIFIED:
- REPOSITORY-VERIFIED: 49 PASS / 0 FAIL / 0 ERROR / 0 SKIP scoped tests; standalone
  CLI and compilation pass. Regression covers old/current/personal/auth/monthly,
  dates/campaign overlap, tracking/region, unchanged versions and provider errors.
- LIVE-VERIFIED isolated dev and retained public DOM: 9 current pages, 305 current
  campaign-context items; all checked independently against source DOM. 154 rows
  have old+new, all checked (83 Kuvalda,71 MetalMaster). No known price/title/date
  corruption in accepted current versions. 98 HIGH-priority current items.
- Final repeat run15: semantic delta pages0 / versions0 / items0, observations+9;
  raw captures/last_seen may differ without semantic duplication. Duplicate pages,
  versions and per-version item keys0. Source dataset unchanged in final repeat.
- AI provider none; network calls0; persisted NOT_REQUESTED rows for saved versions;
  summary NULL. Fake successful/failing providers tested on rollback-only fixture
  rows without recrawl. Rejected trial versions excluded from later analysis.

| Source | Discovered | Fetched final pass | Stored pages | Promotions | News | Campaign/block/sale pages | Current items | Old+new | HIGH | HTTP errors | Robots excluded |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| kuvalda_nnov |15|4|4|0|0|4|234|83|32|0|11|
| metalmaster |6|6|5|1|2|2|71|71|66|0|0|

Totals include distinct campaign/page contexts; the same product can appear in
both homepage and sale campaign. Unique public SALE discounted products102;
calculated mean17.09% / median16.52% / max36.40%. Special prices excluded from this
public statistic. Other LOW observations retained. Model NULL unless separate
source field proves it; full equipment titles retained.

History includes 34 semantic versions and 1234 item rows across trial/current
extractors, not 1234 unique live products. Trial1.0 had old-price/personal marker
bugs; trial1.1/1.2 were superseded for DOM coverage/hash corrections. Older25
versions marked REJECTED_EXTRACTOR; accepted current9 versions hold305 rows.
Original trials retained diagnostically. Earlier actual homepage3 appeared/3
disappeared retained as observed transitions; cross-extractor transitions excluded
from PRICE_CHANGES. These are observed card changes, not inferred new model launches.

NOT APPLIED: develop merge, deployment, production changes, Sterbrust/ESOL writes,
Universal Supplier business mutations, KAMI or Vekprom/Intervesp changes,
scheduler/cron, catalog-wide crawl, competitor→Sterbrust matching, AI network calls.
All corresponding mutation/call counters0, based on feature diff/connection and
operation scope; no unrelated system was contacted to manufacture extra proof.

WHY: Kuvalda robots rules explicitly exclude /promo/ and /news/, including
promotion details. No excluded detail requests or fallback bypass in collector.
Initial bounded audit captured the listing before enforcement; those audit pages
are discovery evidence only and are not promoted to completed collector coverage.

REMAINING: legally/operationally permitted source route or operator-supplied
approved captures for excluded Kuvalda promotion/news scope; recheck acceptance.
Other homepage tabbed groups beyond winter retain only initially displayed tab.
No full sale/catalog pagination. September prices are not reconstructed from
October observations. Future source changes may need DOM adapter maintenance.

DEVELOPER REQUIRED: none for running accepted available-source V1 locally; future
host/platform configuration, AI provider implementation, broader source coverage
or scheduler require their own scope. Persistent runtime uses existing PG17.11
binary location, which must remain available; private config/runtime are not Git.

RISKS / UNKNOWN: optional publication/campaign dates, brand/model/category or
availability absent from source remain NULL. NN prices never replaced by another
region. Public amount accompanied by auth marker is retained as special context,
not a proven final personal price. No production or live supplier acceptance.

EVIDENCE: local reports/COMPETITOR_INTELLIGENCE_2026-10-09 contains REPORT.md,
SOURCE_MAP.md, CRAWL_RESULT.json, QA_RESULT.json, PAGES.csv, CAMPAIGN_ITEMS.csv,
PRICE_CHANGES.csv, MACHINE_PRIORITY.csv, TEST_RESULT.xml, IDEMPOTENCY_RESULT.json,
raw HTTP/browser/tab captures and screenshots. Detailed final Git SHAs/remote
equality live in FINAL_REPORT.md and GIT_RESULT.json after feature push.

Branch: feature/competitive-intelligence-kuvalda-metalmaster.
Start/base SHA: c0135eef1ad1a2bd502fdc1ade62887974e7c222 (fresh remote verified).
Protected develop: same base SHA; production36924836bb02965e857173773cdb70f302914643.
Final SHA: commit containing this checkpoint, exact post-push receipt recorded
locally to avoid a self-referential Git hash.

NEXT ACTION: STOP after feature push/report. No develop merge. Keep source blocker
explicit; full COMPETITOR_INTELLIGENCE_KUVALDA_METALMASTER_V1_VERIFIED not claimed.
