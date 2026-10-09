# Competitor business preview / 2026-10-09

Standalone Russian HTML, no server/npm/CDN/internet required. One index.html embeds
CSS, JavaScript and normalized business facts. HTTP links open a new tab only after
a user's click; local CSV/JSON downloads sit beside it. No raw DOM or database IDs
are needed by the presentation. The DB keeps its original raw evidence/history.

```powershell
python -m competitor_intel --dev-config competitor-data/dev-config.json report --preview --since 2026-09-01 --through 2026-10-09
```

Report date defaults to Europe/Moscow. Output defaults to
reports/COMPETITOR_BUSINESS_REPORT_PREVIEW_<date>/index.html. `--ending-days` defaults
7; reminders distinguish <=3 days. No crawler, external AI, registry/source DML,
schema migration, scheduler or Sterbrust matching is invoked by preview.

The reader uses REPEATABLE READ READ ONLY snapshots. Whole-table fingerprints and
per-source source/page/version/item/observation/analysis/surface fingerprints before
and after prove zero mutations. Event/report state is rebuildable local JSON files.
EVENT_REGISTRY.json permits explicit second-build new_events0 / duplicate_events0.
No DB event table is required. Index material is not price authority.

## Business facts

Known dated active promotions, unknown-date count, expiring dates, campaign-context
card count, saved HIGH priority, public discount stats, source/category summaries,
news excerpts, current prices, actual observation-to-observation deltas and optional
saved AI analysis are separate concepts. Baseline cannot claim new models/daily price
changes. Uncomputable change metrics are omitted, not presented as authoritative0.
Site old price and prior observed price are displayed in separate columns.

Current preview:34accepted current pages (Kuvalda29/MetalMaster5),16promotion/campaign
pages,814current card contexts (743/71),729card contexts in explicitly promotional/
sale/homepage blocks,201saved HIGH cards,1news material in the selected publication
period,13known dated active promotions,2unknown-date promotions,0ending <=7days.
34BASELINE_OBSERVATION events; no evidenced daily increase/decrease/new-model claim.
AI DONE rows0, AI_NETWORK_CALLS0. Current dataset has no proved discovery/rendered
scope completeness, so absence alerts are conservatively suppressed. This is
documented in ADDING_A_COMPETITOR.md; synthetic full-scope tests cover the events.

Discount mean/median/max are calculated from deduplicated current PUBLIC/SALE RUB
contexts with positive old/new. Special or ambiguous contexts are excluded. Charts
use 0–5/5–10/10–20/20–30/30+% bands. A high displayed sale discount is a source fact,
not an inferred new daily price drop or a forecast of strategy.

HIGH remains the existing saved classifier, including industrial components. The
three factual highlighted offers rank by absolute public discount in HIGH so large
equipment offers lead. The table defaults to HIGH first, then absolute discount;
explicit maximum-discount/min-price/fresh-observation sorting overrides that order.
All consumer data remains available with HIGH/NORMAL/LOW/ALL filters.

Browser QA: native installed Edge file://, desktop/mobile, local offline resources,
10price rows/5promotions/5HIGH rows/all source blocks, filters/sort/paging/expansion/
safe canonical target=_blank links, correct Russian/price/date text, no NaN/null/None
visual output. All HTTP(S) attempts aborted; clicking a link tests popup creation
without crawling its destination. Human visual screenshots also reviewed.

Feature-only delivery. No develop/production/KAMI/Vekprom/US-table/Sterbrust/ESOL or
automation change. Stop at COMPETITOR_INTELLIGENCE_BUSINESS_PREVIEW_VERIFIED /
WAITING_FOR_OPERATOR_REPORT_REVIEW; operator decides report usefulness next.
