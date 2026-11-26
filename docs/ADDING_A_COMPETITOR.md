# Adding a competitor to the generic business report

The analytics/HTML layer has no competitor-name branches. Registry rows determine
display names and source blocks; normalized page types, price semantics, timestamps
and declared capabilities determine behavior. Existing two adapters remain separate.

1. Implement the public source adapter with exact authorized host/region, bounded
   discovery, raw captures, private/auth/protection guards and source-specific policy.
2. Register the source in competitor_sources and declare its capabilities in the
   declarative source_capabilities.json (or normalized source.capabilities metadata).
   Supported contracts: PROMOTIONS, NEWS, SALE_PRODUCTS, HOMEPAGE_BLOCKS,
   PRODUCT_PRICES, CAMPAIGN_DATES, PRODUCT_DETAIL. No NEWS capability is not an error.
3. Normalize to the same model: page types PROMOTION/CAMPAIGN/NEWS/SALE_PAGE/
   HOMEPAGE_BLOCK; index/detail observations remain separate. Canonical URL identity,
   source/region, Decimal values, price role, explicit dates, raw labels and priority.
   Never set a Sterbrust ID or borrow supplier matching. Unknown is NULL, not zero.
4. Add positive/negative parser fixtures, source policy/access tests and pricing-role
   QA. Source facts/versions must be saved independently of report generation.
5. Apply source QA before a version becomes ACCEPTED; rejected/unreviewed parser
   versions must not become comparable change history. Parser/coverage changes begin
   a new baseline, not a PRICE_DECREASE/new-model event.

Then business_data → change_detection → business_report → business_html works
without a source-specific rewrite. Generic fixture future_factory validates this.

## Completeness contract for new / disappeared entities

First-known page ≠ newly launched promotion or news. A discovery record may be
provided in competitor_runs.result.coverage (no new source-fact schema required):

```json
{
  "coverage": [
    {
      "source": "new_source",
      "observed_at": "2026-10-09T08:00:00+03:00",
      "complete": true,
      "entities": ["https://example.com/campaign/one/"]
    }
  ]
}
```

Only declare complete for the same bounded discovery scope on both observations.
Full-source completeness must not be inferred from one first listing page or an
expanded parser/navigation scope. Without proof, first-seen entities remain
BASELINE_OBSERVATION. This task's current source data has no complete discovery
history, so preview explicitly says INITIAL BASELINE, not "new743 products".

For page product additions/removals, a normalized snapshot can declare
item_scope_complete=true only when the same observed block/window was completely
rendered and comparable. The existing DB does not carry this flag; its historical
loader conservatively leaves it false. This makes membership-loss alerts unavailable
until an adapter records completeness. It does not prevent price/text/deadline
comparison for an already known, accepted, same-extractor page.

Saved offline re-extraction/fixture observations never create fresh live changes.
The current version can still supply source-backed baseline facts and dates.
The same product in multiple campaign contexts is not multiple new models.
No fuzzy cross-source product identity is performed.

## Event identity and price semantics

Rebuildable events are derived files, not source-fact DML. Their stable identity uses
source/entity/type, before/after version, transition observation timestamps and
relevant values. Repeated builds on the same state add0 events. Return to an old
content hash and a later identical price transition remain distinct observations.
Ending reminders are calendar facts, not new source-content changes.

Only PUBLIC/SALE, same source/product URL/region/currency and positive observed
amounts compare as ordinary prices. PERSONAL/AUTH_REQUIRED/FROM_PRICE/monthly/
credit/bonus transitions yield PRICE_SEMANTICS_CHANGED or PRICE_REVIEW. Site old
price is distinct from the previous observed current price. Discount statistics
use unique public RUB products; contradictory current contexts are excluded.

## AI

Any Analyzer persists DONE normalized competitor_ai_analysis rows separately.
Preview reads those rows; it never invokes a provider. Provider/model/prompt and
structured mechanics/signals/relevance are displayed in a separate AI section.
Without DONE rows, the same report is fully functional and AI_NETWORK_CALLS=0.
