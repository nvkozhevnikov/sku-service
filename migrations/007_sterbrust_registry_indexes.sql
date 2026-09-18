BEGIN;

CREATE INDEX sterbrust_products_catalog_idx ON sterbrust_products (catalog_product_id);
CREATE INDEX sterbrust_products_brand_model_idx ON sterbrust_products (normalized_brand, normalized_model) WHERE normalized_brand IS NOT NULL AND normalized_model IS NOT NULL;
CREATE INDEX sterbrust_products_supplier_article_idx ON sterbrust_products (normalized_brand, normalized_supplier_article) WHERE normalized_supplier_article IS NOT NULL;
CREATE INDEX sterbrust_products_category_idx ON sterbrust_products (category_id);
CREATE INDEX sterbrust_products_properties_gin_idx ON sterbrust_products USING gin (properties);
CREATE INDEX catalog_product_identifiers_lookup_idx ON catalog_product_identifiers (identifier_type, normalized_value, brand_id, supplier_id);
CREATE UNIQUE INDEX catalog_product_identifiers_uq ON catalog_product_identifiers
    (identifier_type, normalized_value, COALESCE(brand_id, 0), COALESCE(supplier_id, 0), catalog_product_id);
CREATE INDEX product_matches_status_idx ON product_matches (status, auto_accepted);
CREATE INDEX product_matches_catalog_idx ON product_matches (catalog_product_id);

COMMIT;
