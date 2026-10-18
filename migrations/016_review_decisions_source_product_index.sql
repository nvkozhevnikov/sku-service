BEGIN;

-- review_decisions.source_product_id references source_products(id) but has no
-- index, so per-product decision lookups and ON DELETE checks scan the table.
-- Additive and idempotent; no data changes.
CREATE INDEX IF NOT EXISTS review_decisions_source_product_idx ON review_decisions (source_product_id);

COMMIT;
