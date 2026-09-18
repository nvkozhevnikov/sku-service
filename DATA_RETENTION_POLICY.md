# Data retention policy

## Keep indefinitely while referenced

- Suppliers, source products, offers, URL aliases and canonical relations.
- First/last seen and current hash/state fields.
- Actual commercial change history needed for price/availability analysis.
- Feed-run records and checksums required for traceability.

## Suggested operational retention

- Successful `crawl_runs`: 24 months; keep monthly aggregates thereafter if desired.
- `crawl_run_items`: 90 days for successful runs, 12 months for failed/partial/anomalous runs.
- `crawl_errors`: 12 months minimum; longer for unresolved parser defects.
- Non-commercial change history: 24 months, then archive; commercial history: retain according to business/legal needs (recommended at least 5 years).
- Raw adapter JSON on current entities: keep current value; archive superseded large payloads only when a real change is recorded and investigation value justifies it.

## Never retain by default

- Full unchanged HTML snapshots for every daily crawl.
- Image/document bodies at onboarding stage.
- Sensitive authentication/session data; Partner-ST audit is unauthenticated.

## Purge safety

Purge jobs operate by bounded date partitions/batches, never cascade-delete current catalog entities, and must preserve rows referenced by change/feed/error investigations. Retention values should be supplier/global configuration and reviewed before production.
