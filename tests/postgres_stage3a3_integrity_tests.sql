BEGIN;

DO $qa$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'sterbrust_supplier_identifiers_supplier_code_fk'
          AND contype = 'f'
    ) THEN
        RAISE EXCEPTION 'supplier_id/supplier_code composite integrity constraint is missing';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'sterbrust_supplier_identifiers_catalog_relation_fk'
          AND contype = 'f'
    ) THEN
        RAISE EXCEPTION 'Sterbrust/catalog cross-product integrity constraint is missing';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = 'source_products'::regclass
          AND attname = 'catalog_product_id' AND NOT attisdropped
    ) THEN
        RAISE EXCEPTION 'authoritative source_products.catalog_product_id mapping is missing';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_indexes
        WHERE schemaname = current_schema()
          AND indexname = 'product_matches_one_current_per_source_uq'
          AND indexdef ILIKE '%UNIQUE%WHERE is_current%'
    ) THEN
        RAISE EXCEPTION 'single-current-decision index is missing or not partial unique';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema() AND table_name = 'product_matches'
          AND column_name = 'last_validated_at'
    ) THEN
        RAISE EXCEPTION 'last_validated_at is missing';
    END IF;

    RAISE NOTICE 'Stage 3A.3 PostgreSQL integrity structure passed.';
END
$qa$;

ROLLBACK;
