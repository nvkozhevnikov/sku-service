BEGIN;

-- Idempotent one-existing-card-to-one-canonical-product bootstrap.
INSERT INTO catalog_products (name, normalized_data)
SELECT sp.name, jsonb_build_object('bootstrap_source', 'sterbrust', 'sterbrust_product_id', sp.sterbrust_product_id)
FROM sterbrust_products sp
WHERE sp.catalog_product_id IS NULL
  AND NOT EXISTS (
      SELECT 1 FROM catalog_products cp
      WHERE cp.normalized_data->>'bootstrap_source' = 'sterbrust'
        AND cp.normalized_data->>'sterbrust_product_id' = sp.sterbrust_product_id
  );

UPDATE sterbrust_products sp
SET catalog_product_id = cp.id, updated_at = now()
FROM catalog_products cp
WHERE sp.catalog_product_id IS NULL
  AND cp.normalized_data->>'bootstrap_source' = 'sterbrust'
  AND cp.normalized_data->>'sterbrust_product_id' = sp.sterbrust_product_id;

INSERT INTO catalog_product_identifiers (
    catalog_product_id, identifier_type, normalized_value, raw_value,
    source_kind, source_record_id, reliability
)
SELECT sp.catalog_product_id, 'sterbrust_id', sp.sterbrust_product_id,
       sp.sterbrust_product_id, 'sterbrust_registry', sp.sterbrust_product_id, 'supporting'
FROM sterbrust_products sp
WHERE sp.catalog_product_id IS NOT NULL
ON CONFLICT DO NOTHING;

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM sterbrust_products WHERE catalog_product_id IS NULL) THEN
    RAISE EXCEPTION 'canonical bootstrap incomplete';
  END IF;
END $$;

COMMIT;
