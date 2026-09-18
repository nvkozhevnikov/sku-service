# Canonical data model

## Model boundary

The model separates four layers:

1. `suppliers` — connection, adapter and crawl policy.
2. `source_products` — supplier-published cards and their stable supplier identity.
3. `catalog_products` — optional normalized cross-supplier product identity.
4. `offers` — independently sellable commercial identities under a source card.

This avoids supplier-specific tables. A new supplier is onboarded through configuration plus an adapter that emits the same canonical records.

## Principal entities

### suppliers

Holds code, name, base URL, adapter, enabled flag, direct/future-proxy transport mode, JSONB crawl/rate settings and the safe-deactivation threshold. No proxy pool is implemented.

### source_products

One row per supplier card. It owns supplier identity, URLs, raw/normalized descriptive data, lifecycle timestamps, active/miss state and field-group hashes. A partial unique index enforces `(supplier_id, external_id)` only when the adapter declares the external identity stable. Partner-ST uses numeric `product_id` with that flag enabled.

### catalog_products

Optional supplier-neutral entity for later cross-supplier matching. Onboarding does not force a match: `source_products.catalog_product_id` remains nullable until a trusted normalization/matching process exists.

### sterbrust_product_supplier_identifiers

One Sterbrust catalog product can carry many supplier-specific articles, and each article belongs to a supplier/property namespace. The Stage 3A.1 table stores the Sterbrust product, optional canonical product and supplier foreign keys, stable `supplier_code`, property ID/code, raw and normalized values, observation times, active state, and raw metadata. The legacy scalar columns on `sterbrust_products` remain convenience fields only; the matching engine does not treat them as the complete supplier-article registry.

`(supplier_code, normalized_value)` is intentionally not unique. Repeated values are retained as catalog evidence and emitted as `DUPLICATE_IDENTIFIER_REVIEW`, never merged automatically.

Migration `009_matching_data_quality.sql` closes two optional-link integrity gaps. `(supplier_id, supplier_code)` must reference the same `suppliers` row whenever `supplier_id` is populated. `(sterbrust_product_id, catalog_product_id)` must reference the canonical relation already stored on that Sterbrust product whenever `catalog_product_id` is populated.

`product_matches.warnings` and `product_matches.data_quality_flags` preserve field/extraction problems without inventing new business statuses. Evidence provenance distinguishes an actual product-identity contradiction from a stale source property.

### offers

One or more sellable rows beneath a source card. Commercial fields use `numeric(18,4)`, nullable price/quantity, ISO-like three-letter currency, raw and normalized availability, hashes and lifecycle state. One partial unique index limits each card to one `default` offer; future adapters may add `variant` offers with stable supplier offer IDs.

Partner-ST maps one card to one default offer. Its navigation controls do not generate additional offers.

## Raw and normalized data

Raw supplier values remain first-class:

- `availability_raw` beside `availability_normalized` and `quantity`;
- `brand_raw`/`manufacturer_raw` beside optional normalized entities;
- `raw_data` and `raw_metadata` JSONB for adapter-specific payloads;
- `raw_name`, `raw_value`, `raw_unit`, `raw_value_html` beside nullable normalized property fields.

JSONB is an escape hatch, not a replacement for indexed canonical columns.

General/manufacturer articles and supplier-specific articles are separate identifier namespaces. Equal text does not make them interchangeable.

## Categories

`supplier_categories` stores a supplier tree. `source_product_categories` is many-to-many. Published breadcrumbs are preserved separately through `product_category_paths` and ordered path nodes, so one card can keep its canonical chain while also belonging to other categories.

Every category edge is supplier-scoped in the database. Category parents, product memberships, breadcrumb paths and breadcrumb nodes carry `supplier_id` and use composite foreign keys. Application code therefore cannot accidentally attach a Partner-ST product or path to another supplier's category, even when numeric surrogate IDs are mixed up.

## Media

One `product_media` table covers images, documents and videos using a checked type. It stores source URL, order, primary-image flag, optional document type/hash and raw metadata. Files are not downloaded by this design stage.

## Options and relations

`source_product_options` and `source_product_option_values` preserve raw controls. Classification is checked against the four audit values. `source_product_relations` records navigation/variant/related/accessory/replacement edges. A navigation value points to another `source_product`; a true variant points to a separately identified `offer` through adapter logic, not by inventing products from text.

## Changes and operational state

Field-group hashes live on source products/offers. `entity_change_history` stores only actual changes and references the responsible crawl run. `crawl_runs`, `crawl_run_items`, and `crawl_errors` preserve operational evidence. `feed_runs` is ready for future exporters without defining any XML format.

Operational evidence is also supplier-closed: each crawl item, error and history row must agree with the supplier of its crawl run and any referenced product/offer. `feed_runs.feed_scope` is either `supplier` with a required `supplier_id`, or `combined` with `supplier_id` required to be NULL.

## Supplier-isolation invariant

`supplier_id` is part of the referential contract, not merely a filter convention. Supplier-owned targets expose `(id, supplier_id)` as a candidate key; dependent tables reference that pair. This applies to products, offers, categories, category paths, options, option values and crawl runs. Cross-supplier category trees, memberships, paths, relations, crawl evidence and change history are rejected by PostgreSQL itself.

## Scale and concurrency

Identity columns are narrow and indexed; large raw payloads remain JSONB/text off the hottest paths. Supplier-scoped indexes support parallel crawls. A crawler should update one supplier in bounded transactions, use upserts on stable external identity, and avoid global locks.

## Stage 3A.3 current mapping and decision history

`source_products.catalog_product_id` is the authoritative current accepted relationship. `product_matches` is an evidence/event layer, not the source of current mapping truth. Migration 010 adds `last_validated_at`, `is_current`, a self-reference to the previous decision, and decision/warning fingerprints. Exactly one decision row can be current per source product.

An unchanged daily result updates `last_validated_at` on the current row. A new event row is created only when the decision, conflict state, material warning set, or manual mapping changes. Before inserting a changed event, the previous row is made non-current and linked through `previous_match_id` in the same transaction.

Typed property normalization is lossless: raw values stay in the supplier and Sterbrust evidence, while comparison evidence records nullable Decimal numeric values, canonical units, normalized text, the applied rule, unit conversion, tolerance, importance, and classification. Generic policies classify identity evidence as `IDENTITY_CRITICAL`, `SUPPORTING`, or `NON_IDENTITY`; category-specific policy overlays are the extension point.
