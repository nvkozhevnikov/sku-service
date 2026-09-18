# PostgreSQL schema guide

## Source of truth

Apply files in `migrations/` by numeric order. `schema.sql` is a concatenated convenience view and must not be edited independently.

1. `001_suppliers_and_crawls.sql` — suppliers and crawl-run gates.
2. `002_products_and_offers.sql` — normalized catalog shell, supplier cards, URL aliases and offers.
3. `003_catalog_details.sql` — category membership/paths, raw properties, media, options and product relations.
4. `004_history_errors_and_feeds.sql` — crawl items/errors, change history and future feed runs.
5. `005_indexes.sql` — selective unique and lookup indexes.
6. `006_sterbrust_registry_and_matching.sql` — read-only Sterbrust registry and auditable matching records.
7. `007_sterbrust_registry_indexes.sql` — Sterbrust/matching lookup indexes.
8. `008_sterbrust_supplier_identifiers.sql` — one-to-many supplier-specific Sterbrust identifiers.
9. `009_matching_data_quality.sql` — match warnings and composite optional-link integrity.
10. `010_persistent_match_quality.sql` — current/history matching state and durable fingerprints.
11. `011_sterbrust_optional_product_url.sql` — optional Sterbrust product URL handling.

## Type choices

- Surrogate keys: `bigint GENERATED ALWAYS AS IDENTITY` for scale and simple foreign keys.
- Time: `timestamptz` everywhere operational order matters.
- Money/quantity: `numeric(18,4)`, never float; NULL means unknown/not published.
- Currency: nullable `varchar(3)` checked as uppercase letters.
- URLs/raw text: `text`; HTTP(S) checks catch obvious adapter mistakes.
- Supplier-specific payloads: JSONB constrained to object/array shape.
- Statuses: text plus CHECK constraints, easier to evolve than PostgreSQL enums.

## Critical constraints

- Stable card identity: partial unique `(supplier_id, external_id)`.
- Stable offer identity: partial unique `(supplier_id, external_offer_id)`.
- Offer ownership: composite FK requires the offer and product relationship to match.
- One default offer per source product; unlimited independently identified variant offers.
- Missing price allowed; negative prices/quantities rejected.
- Product can belong to many categories and keep multiple historical/published paths.
- Properties/media can be product-scoped or offer-scoped; partial unique indexes handle NULL correctly.
- Change history targets exactly one product or one offer and rejects no-op entries.
- Supplier-owned parent rows expose `UNIQUE (id, supplier_id)` and supplier-owned edges use composite foreign keys.
- Category parents, product/category membership and breadcrumb nodes cannot cross supplier boundaries.
- Product relations, crawl items/errors and change history cannot reference products/offers from another supplier.
- Feed scope is coherent: `supplier` requires a supplier, while `combined` forbids one.

## Referential-integrity integration test

`tests/postgres_integrity_tests.sql` proves nine invalid cross-supplier/scope cases are rejected and then inserts valid same-supplier controls. `scripts/test_postgres_integrity.sh` applies migrations `001`–`005` in order and runs the test against a fresh disposable PostgreSQL database. It refuses superusers, non-test database names and non-empty schemas, and it never creates or destroys the PostgreSQL service or database. See `docs/POSTGRES_INTEGRITY_TESTS.md`.

## Timestamp ownership

The ingest application explicitly updates `updated_at` and business timestamps in the same transaction as data changes. The schema deliberately avoids a generic trigger that would mark rows changed on operational no-op updates.

## Deployment note

From Stage 3D the canonical runtime is PostgreSQL 17.11 in Docker Compose. Data lives in the named `universal_supplier_pgdata` volume at `/var/lib/postgresql/data`; it is not kept only in the container layer. Use `scripts/docker_postgres_migrate.py` to verify and apply immutable migrations 001–011. See `docs/POSTGRES_DOCKER_DEPLOYMENT.md` and `docs/POSTGRES_BACKUP.md`.

## Stage 3A registry and matching additions

Migrations `006` and `007` add the Sterbrust snapshot registry, canonical identifiers, approved brand aliases, auditable product matches, and indexes. `catalog_offer_selection` is only a schema placeholder; no selection or pricing policy is implemented.

Automatic matches require non-empty structured evidence and one of the two accepting statuses. Supplier-scoped identifiers require `supplier_id`; they cannot accidentally become global identifiers. `scripts/bootstrap_sterbrust_catalog.sql` creates exactly one initial canonical product for each complete Sterbrust registry row and aborts if any row remains unlinked.

The application still uses `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, and `DB_SSLMODE`, so host Python workflows remain supported. Inside Compose, `DB_HOST=postgres`; from Windows, use loopback and the configured host port.

## Stage 3A.2 evidence quality and integrity

Migration `009` adds JSONB `warnings` and `data_quality_flags` to `product_matches`. It also enforces that a populated supplier identifier link agrees with `suppliers.code`, and that a populated canonical identifier link agrees with `sterbrust_products.catalog_product_id`. Migrations `001`–`008` remain byte-for-byte unchanged from the approved Stage 3A.1 package.
