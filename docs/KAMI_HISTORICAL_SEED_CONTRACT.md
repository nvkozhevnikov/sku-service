# KAMI historical metadata + bounded LIVE / 2026-10-12

OPERATOR-CONFIRMED flow: existing KAMI corpus READ ONLY → URL/name/brand/model/
category/original capture time → HISTORICAL_SEED in competitor intelligence.
Separate LIVE scope: current sale/actions/news/homepage, then only participating
products or explicitly justified refresh targets. No full catalogue re-crawl.

## Verified input

Pinned saved corpus logical path:
../sku-service-kami/reports/KAMI_INTEGRATION_2026-10-05/RECONCILED/CARDS.jsonl.
The current filesystem resolves that alias to the sku-service workspace; do not
infer a separate source copy or write through the alias. Verified SHA256:
365fce78658e3cb692be48cda3633c4b8ec7642c200ce47e7ed4f67850345138.
5264records/5264unique canonical product URLs, original captures2026-10-05.
Brand present4625, saved model3908, category5264, capture time known5264.
These are historical source metadata, not current publication/model-launch facts.

The standalone older desktop parser is not silently substituted for this pinned
reconciled corpus. No supplier DB credentials/runtime are required to read it.
No KAMI worktree, checkpoint, database, matching or selection is changed.

## Seed contract

competitor_intel.historical_seed reads the original JSONL bytes and writes a
deterministic metadata-only HISTORICAL_SEEDS.jsonl. The output lives only in
ignored competitor reports. It is a prepared seed bundle, not a DB-applied source.

- source record ID + canonical URL identity; brand is not defaulted to KAMI.
- Preserve reported full_model, source identity status and card_kind. Group/series
  metadata is not promoted to an exact execution or a Sterbrust match.
- Original capture time comes only from capture.observed_at. Missing/naive dates
  remain UNKNOWN, never file mtime or today's import time.
- price/old_price/availability=NULL; is_live=false; live_price_eligible=false;
  new_product_event_eligible=false. No historic price becomes today's public offer.
- historical_context enriches a live record only under a separate context field,
  by exact URL. It never replaces live title/brand/price/observed_at.
- The current daily business preview/price/event data is unchanged. Historical
  catalogue metadata must remain outside current promotions, prices and new-model
  metrics. A first LIVE observation is a live baseline, not5264new products today.

## Refresh gate

Pure plan_refresh selects/deduplicates only:
CURRENT_CAMPAIGN_MEMBER / EXPLICIT_SOURCE_CHANGE / OPERATOR_REQUESTED_REFRESH.
Age-based selection requires an explicit timezone-aware threshold and a tracked
URL subset. Historical corpus age alone never selects the entire corpus.
Default max20product URLs, configurable0–100. Overflow is deferred, not silently
run. All candidates still require normal public-source access/policy validation.
The operator's 2026-10-12 onboarding scope now explicitly makes robots
informational for these four competitors, including KAMI. Record robots
separately from actual public fetch status; all access-control prohibitions remain.
No private/auth/API/CAPTCHA/evasion, scheduler or full catalogue worker activation.

Current artifact REFRESH_PLAN.json has0selected, because no actual current KAMI
campaign memberships/changed/tracked URLs have been supplied/collected in this
offline preparation. No live URLs or present prices are fabricated from history.

## DONE / APPLIED / VERIFIED

DONE: independent seed normalizer, exact URL historical context, bounded refresh
planner, source hash checks, positive/negative fixtures and real corpus bundle.
APPLIED: prepared local metadata bundle only; competitor DB import/live adapter
and new KAMI source registration are NOT APPLIED. Source/KAMI/US/other data writes0.
VERIFIED: pinned corpus before/after identical; output deterministic; metadata-only
eligibility guard tests. No HTTP/AI/DB operation from this preparation.
Next implementation step: an explicit historical-seed store/cache consumed as
lookup only, then normal current public source discovery and targeted LIVE records.
Keep this checkpoint distinct from completed end-to-end KAMI LIVE onboarding.

## Applied continuation / 2026-10-12

The prepared bundle is reused without source reads/rebuild. Its original Windows
CRLF physical SHA is 9418d99c2109b60519d46ef38579a3039652d63b9bc2081ba88ba7c62242c310.
Normalizing CRLF to LF reproduces the manifest SHA ba60d305; artifact bytes stay
unchanged. Future exports write literal UTF8/LF bytes so physical and logical
hashes agree.

5264 rows are APPLIED to competitor_historical_seeds in isolated DB55454 only,
source stanki_ru. Original time/payload, REUSED_KAMI_CAPTURE/HISTORICAL_SEED,
is_live=false and NULL commercial facts retained. Repeated import inserted0.
Current promotions/prices/events and both frozen source datasets VERIFIED equal
before/after import. This lookup table cannot become current page/item observations.
Exact URL context appears separately in page payload, never overwrites live fields.
LIVE discovered campaign membership drives the bounded refresh plan; no age-only
detail refresh is used. See COMPETITOR_FOUR_SOURCE_ONBOARDING report for actual
access gaps, efficiency and remaining work; this appendix does not certify all four.
