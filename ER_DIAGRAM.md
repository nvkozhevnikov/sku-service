# ER diagram

```mermaid
erDiagram
    SUPPLIERS ||--o{ CRAWL_RUNS : runs
    SUPPLIERS ||--o{ SOURCE_PRODUCTS : publishes
    CATALOG_PRODUCTS o|--o{ SOURCE_PRODUCTS : normalizes
    BRANDS o|--o{ CATALOG_PRODUCTS : brands
    MANUFACTURERS o|--o{ CATALOG_PRODUCTS : manufactures
    SOURCE_PRODUCTS ||--o{ SOURCE_PRODUCT_URLS : aliases
    SOURCE_PRODUCTS ||--|{ OFFERS : sells
    SOURCE_PRODUCTS ||--o{ PRODUCT_PROPERTIES : has
    OFFERS o|--o{ PRODUCT_PROPERTIES : scopes
    SOURCE_PRODUCTS ||--o{ PRODUCT_MEDIA : has
    OFFERS o|--o{ PRODUCT_MEDIA : scopes
    SUPPLIERS ||--o{ SUPPLIER_CATEGORIES : defines
    SUPPLIER_CATEGORIES o|--o{ SUPPLIER_CATEGORIES : parent
    SOURCE_PRODUCTS ||--o{ SOURCE_PRODUCT_CATEGORIES : belongs
    SUPPLIER_CATEGORIES ||--o{ SOURCE_PRODUCT_CATEGORIES : includes
    SOURCE_PRODUCTS ||--o{ PRODUCT_CATEGORY_PATHS : publishes
    PRODUCT_CATEGORY_PATHS ||--|{ PRODUCT_CATEGORY_PATH_NODES : orders
    SOURCE_PRODUCTS ||--o{ SOURCE_PRODUCT_OPTIONS : exposes
    SOURCE_PRODUCT_OPTIONS ||--o{ SOURCE_PRODUCT_OPTION_VALUES : contains
    SOURCE_PRODUCT_OPTION_VALUES o|--o{ SOURCE_PRODUCT_RELATIONS : explains
    SOURCE_PRODUCTS ||--o{ SOURCE_PRODUCT_RELATIONS : from
    SOURCE_PRODUCTS ||--o{ SOURCE_PRODUCT_RELATIONS : to
    CRAWL_RUNS ||--o{ CRAWL_RUN_ITEMS : records
    CRAWL_RUNS ||--o{ CRAWL_ERRORS : reports
    CRAWL_RUNS ||--o{ ENTITY_CHANGE_HISTORY : caused
    SUPPLIERS ||--o{ FEED_RUNS : exports
    STERBRUST_SNAPSHOT_RUNS ||--o{ STERBRUST_PRODUCTS : observes
    CATALOG_PRODUCTS ||--o| STERBRUST_PRODUCTS : represents
    STERBRUST_PRODUCTS ||--o{ STERBRUST_PRODUCT_SUPPLIER_IDENTIFIERS : carries
    CATALOG_PRODUCTS o|--o{ STERBRUST_PRODUCT_SUPPLIER_IDENTIFIERS : normalizes
    SUPPLIERS o|--o{ STERBRUST_PRODUCT_SUPPLIER_IDENTIFIERS : namespaces
    CATALOG_PRODUCTS ||--o{ CATALOG_PRODUCT_IDENTIFIERS : identified_by
    BRANDS ||--o{ BRAND_ALIASES : aliases
    SOURCE_PRODUCTS ||--o{ PRODUCT_MATCHES : evaluated
    CATALOG_PRODUCTS o|--o{ PRODUCT_MATCHES : candidate
    PRODUCT_MATCHES o|--o{ PRODUCT_MATCHES : previous_decision
    CATALOG_PRODUCTS ||--o| CATALOG_OFFER_SELECTION : future_selection
```

The `catalog_products` association on supplier cards remains optional until anti-duplicate matching succeeds. Each complete Sterbrust registry card is bootstrapped one-to-one; existing lookalikes are review candidates and are not merged. Partner-ST has one default offer per source product; different supplier prices and availability remain separate offers, not variants.

Supplier-specific articles are one-to-many from a Sterbrust product. Duplicate `(supplier_code, normalized article)` observations can point to multiple Sterbrust products and remain explicit review cases rather than being blocked by a global uniqueness constraint.

Migration 009 makes the optional supplier and canonical links composite integrity contracts: populated supplier IDs must agree with `supplier_code`, and populated canonical IDs must agree with the owning Sterbrust product. Matching warnings/data-quality flags remain attributes of `PRODUCT_MATCHES`.

Supplier-owned relationships shown above are implemented with composite `(entity_id, supplier_id)` foreign keys where a mismatched supplier could otherwise produce a valid-looking edge. The diagram omits repeated `supplier_id` columns for readability; the migrations are the constraint-level source of truth.

Migration 010 keeps `SOURCE_PRODUCTS.catalog_product_id` as the current accepted mapping and makes `PRODUCT_MATCHES` a current-plus-history event stream. A partial unique index permits one current decision per source product; repeated unchanged validation updates `last_validated_at` instead of adding duplicate history rows.
