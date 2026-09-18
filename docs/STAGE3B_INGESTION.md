# Stage 3B Partner-ST ingestion

The `universal_supplier` package separates generic transport, domain models, change detection, crawl state, repositories, and supplier adapters. Partner-ST-specific HTML and sitemap logic is confined to `universal_supplier/adapters/partner_st.py`.

## Live crawl

`scripts/run_partner_st_live_crawl.py` reads the current sitemap index, discovers every `products*.xml` sitemap dynamically, and fetches product cards through direct HTTP. The transport uses a `requests.Session`, explicit timeouts, bounded exponential retry/backoff, a shared request interval, a normal user agent, and redirect evidence. It does not use Playwright or a proxy.

The adapter preserves product ID, supplier SKU, canonical URL, description, JSON-LD raw evidence, properties, images, attachment documents, video metadata, breadcrumbs, option groups and option values. A selected/self option is informational; another product URL is navigation. Neither becomes a variant offer. Every card maps to one default offer with `external_offer_id = product_id`.

## PostgreSQL integration

`scripts/run_stage3b_postgres_integration.py` is the guarded external-PostgreSQL path. It requires all `DB_*` variables plus explicit `STAGE3B_DB_CONFIRM=YES`, rejects a superuser and a non-empty schema, verifies the immutable migration 001–010 checksums, and applies migrations 001–011. Migration 011 preserves legacy/inactive Sterbrust identity records whose read-only API snapshot has no public URL; present URLs remain constrained to absolute HTTP(S). It then performs two new dynamic-sitemap live crawls. Each run owns a separate `crawl_runs` row plus URL-level `crawl_run_items` and error evidence. RUN1 is ingested before the packaged read-only Sterbrust registry is bootstrapped and matched. RUN2 reads accepted links only from `source_products.catalog_product_id` in the same database; precomputed CSV files are not authoritative inputs.

Ingestion persists URL history, properties, media, categories, options and relations. The options hash is stored under the reserved `_stage3b` key in `source_products.raw_data`, so an option-only change refreshes option rows without an options-specific schema migration. Real group changes produce sparse `entity_change_history`; unchanged downloads do not. Healthy full crawls increment missing state and deactivate products/default offers after three misses, while anomalous discovery blocks mass missing updates. Commercial out-of-stock never makes an observed source card inactive.

An automatic accepted link is quarantined when later revalidation produces a real identity conflict. Manual provenance is deliberately outside this automatic clearing rule. Partner-ST Sterbrust identifier rows are backfilled to the real `partner_st` supplier namespace before matching.

Credentials are read only from process environment and are never written to reports. No PostgreSQL container, SQLite, or DuckDB fallback exists.

`generate_stage3b_reports.py` preserves a previously recorded PASS integration result. `qa_stage3b.py` validates the self-contained, non-secret integration evidence and does not require a reviewer's DB password.

## Transaction durability

The Psycopg connection is opened with `autocommit=True`. Read-only SELECT statements therefore cannot create a hidden outer transaction, while every repository write uses an explicit `connection.transaction()` block. Migration and integrity SQL files retain their deliberate `BEGIN`/`COMMIT` or `BEGIN`/`ROLLBACK` boundaries.

The integration runner never writes PASS from the original connection. It closes that connection, opens a new independent connection with the same environment-only settings, repeats the gate and integrity assertions, compares every table count, and verifies both current mapping regressions. A valid PASS artifact must include `post_close_verification`, `post_close_select_1`, post-close table counts and current-match counts. Old PASS artifacts without this durability proof are rejected.

## Current checkpoint boundary

In this environment both live Partner-ST crawls and full static matching were completed, but no `DB_*` connection was available. Consequently PostgreSQL migrations, ingestion, persistent mapping and database idempotency were not executed. The package is `STAGE3B = INTEGRATION_BLOCKED`, not closed. See `reports/STAGE3B_QA_REPORT.md` and `reports/STAGE3B_POSTGRES_INTEGRATION_QA.json`.

The Sterbrust side used the packaged read-only snapshot captured at the timestamp reported in `sterbrust_snapshot_metadata.json`. Sterbrust writes and XML generation remained zero.
