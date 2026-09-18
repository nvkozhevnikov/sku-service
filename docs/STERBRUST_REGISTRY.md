# Sterbrust registry

Primary source is the Sterbrust Bitrix REST API, iblock 48. The completed 2026-09-01 snapshot used only HTTP GET and the allowlisted methods `catalog.product.list`, `catalog.productProperty.list`, and `catalog.section.list`. POST, PUT, PATCH, DELETE, webhooks that write, imports, and admin changes were not used.

The API uses `start` pagination with 50 products per page and reports `total`. The main `select[]=*` bulk response supplies 949 product fields, including dynamic `property<ID>` values, so no per-product detail requests were needed. Bitrix omits hidden properties 1272 and 1273 from the wildcard; a second compact paginated bulk pass selected only those identity fields. All passes independently returned 59,427 unique IDs.

## Snapshot result

- Products: 59,427 total; 57,405 active; 2,022 inactive.
- Unique product IDs: 59,427; duplicates: 0.
- Property definitions: 919, refreshed live through REST.
- Sections: 734, refreshed live through REST.
- Brands: 423.
- Write methods used: 0.

`reports/STERBRUST_REGISTRY.jsonl` is the complete registry representation with non-empty raw REST fields and an array of populated properties including property ID, code, name, type, raw value, flattened value, and a unit parsed only when the property name supplies one. `reports/STERBRUST_REGISTRY.csv` is the compact relational-load view. The webhook token is external configuration and is not stored in the package.

## Canonical bootstrap

Every Sterbrust card is bootstrapped one-to-one. Similar existing cards remain separate canonical products and are reported only as `EXISTING_DUPLICATE_CANDIDATE`. `scripts/bootstrap_sterbrust_catalog.sql` is idempotent and creates the canonical `sterbrust_id` identifier. The deterministic second-run simulation created zero additional products. Live PostgreSQL execution remains gated because no standalone external PostgreSQL/psql is available in this workspace.
