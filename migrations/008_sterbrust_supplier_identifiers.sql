BEGIN;

CREATE TABLE sterbrust_product_supplier_identifiers (
    id                      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    sterbrust_product_id    text NOT NULL REFERENCES sterbrust_products(sterbrust_product_id) ON DELETE CASCADE,
    catalog_product_id      bigint REFERENCES catalog_products(id) ON DELETE RESTRICT,
    supplier_id             bigint REFERENCES suppliers(id) ON DELETE RESTRICT,
    supplier_code           varchar(64) NOT NULL,
    property_id             bigint NOT NULL,
    property_code           text NOT NULL,
    identifier_type         text NOT NULL DEFAULT 'supplier_article',
    raw_value               text NOT NULL,
    normalized_value        text NOT NULL,
    first_seen_at           timestamptz NOT NULL,
    last_seen_at            timestamptz NOT NULL,
    active                  boolean NOT NULL DEFAULT true,
    raw_metadata            jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at              timestamptz NOT NULL DEFAULT now(),
    updated_at              timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT sterbrust_supplier_identifiers_supplier_code_ck CHECK (supplier_code ~ '^[a-z][a-z0-9_]*$'),
    CONSTRAINT sterbrust_supplier_identifiers_type_ck CHECK (identifier_type = 'supplier_article'),
    CONSTRAINT sterbrust_supplier_identifiers_values_ck CHECK (btrim(raw_value) <> '' AND btrim(normalized_value) <> ''),
    CONSTRAINT sterbrust_supplier_identifiers_seen_ck CHECK (last_seen_at >= first_seen_at),
    CONSTRAINT sterbrust_supplier_identifiers_json_ck CHECK (jsonb_typeof(raw_metadata) = 'object'),
    CONSTRAINT sterbrust_supplier_identifiers_observation_uq UNIQUE
        (sterbrust_product_id, property_id, normalized_value)
);

CREATE INDEX sterbrust_supplier_identifiers_lookup_idx
    ON sterbrust_product_supplier_identifiers (supplier_code, normalized_value);
CREATE INDEX sterbrust_supplier_identifiers_product_idx
    ON sterbrust_product_supplier_identifiers (sterbrust_product_id);
CREATE INDEX sterbrust_supplier_identifiers_catalog_idx
    ON sterbrust_product_supplier_identifiers (catalog_product_id)
    WHERE catalog_product_id IS NOT NULL;

-- Deliberately no UNIQUE(supplier_code, normalized_value): duplicate values in
-- the existing Sterbrust catalog are retained for DUPLICATE_IDENTIFIER_REVIEW.

COMMIT;
