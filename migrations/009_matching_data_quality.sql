BEGIN;

ALTER TABLE product_matches
    ADD COLUMN warnings jsonb NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN data_quality_flags jsonb NOT NULL DEFAULT '[]'::jsonb,
    ADD CONSTRAINT product_matches_warnings_object_ck CHECK (jsonb_typeof(warnings) = 'object'),
    ADD CONSTRAINT product_matches_data_quality_flags_array_ck CHECK (jsonb_typeof(data_quality_flags) = 'array');

ALTER TABLE suppliers
    ADD CONSTRAINT suppliers_id_code_uq UNIQUE (id, code);

ALTER TABLE sterbrust_products
    ADD CONSTRAINT sterbrust_products_id_catalog_uq UNIQUE (sterbrust_product_id, catalog_product_id);

ALTER TABLE sterbrust_product_supplier_identifiers
    ADD CONSTRAINT sterbrust_supplier_identifiers_supplier_code_fk
        FOREIGN KEY (supplier_id, supplier_code)
        REFERENCES suppliers(id, code) ON DELETE RESTRICT,
    ADD CONSTRAINT sterbrust_supplier_identifiers_catalog_relation_fk
        FOREIGN KEY (sterbrust_product_id, catalog_product_id)
        REFERENCES sterbrust_products(sterbrust_product_id, catalog_product_id) ON DELETE RESTRICT;

COMMIT;
