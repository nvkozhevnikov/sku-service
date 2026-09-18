BEGIN;

CREATE TABLE sterbrust_snapshot_runs (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_kind         text NOT NULL,
    source_url          text NOT NULL,
    started_at          timestamptz NOT NULL DEFAULT now(),
    finished_at         timestamptz,
    status              text NOT NULL DEFAULT 'running',
    discovered_count    integer NOT NULL DEFAULT 0,
    synced_count         integer NOT NULL DEFAULT 0,
    write_methods_used  integer NOT NULL DEFAULT 0,
    field_map            jsonb NOT NULL DEFAULT '{}'::jsonb,
    metadata             jsonb NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT sterbrust_snapshot_runs_source_ck CHECK (source_kind IN ('rest_api_get', 'read_only_export', 'sitemap_fallback')),
    CONSTRAINT sterbrust_snapshot_runs_status_ck CHECK (status IN ('running', 'succeeded', 'partial', 'failed', 'blocked')),
    CONSTRAINT sterbrust_snapshot_runs_counts_ck CHECK (discovered_count >= 0 AND synced_count >= 0 AND write_methods_used = 0),
    CONSTRAINT sterbrust_snapshot_runs_json_ck CHECK (jsonb_typeof(field_map) = 'object' AND jsonb_typeof(metadata) = 'object')
);

CREATE TABLE sterbrust_products (
    id                      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    sterbrust_product_id    text NOT NULL,
    catalog_product_id      bigint REFERENCES catalog_products(id) ON DELETE RESTRICT,
    name                    text NOT NULL,
    normalized_name         text NOT NULL,
    article_raw             text,
    supplier_article_raw    text,
    normalized_supplier_article text,
    brand_raw               text,
    normalized_brand        text,
    manufacturer_raw        text,
    model_raw               text,
    normalized_model        text,
    category_id             text,
    category_name           text,
    category_path           text,
    category_metadata       jsonb NOT NULL DEFAULT '{}'::jsonb,
    properties              jsonb NOT NULL DEFAULT '{}'::jsonb,
    product_url             text NOT NULL,
    active                  boolean,
    raw_data                jsonb NOT NULL DEFAULT '{}'::jsonb,
    snapshot_hash           text NOT NULL,
    first_seen_at           timestamptz NOT NULL,
    last_seen_at            timestamptz NOT NULL,
    last_synced_at          timestamptz NOT NULL,
    created_at              timestamptz NOT NULL DEFAULT now(),
    updated_at              timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT sterbrust_products_product_id_uq UNIQUE (sterbrust_product_id),
    CONSTRAINT sterbrust_products_url_ck CHECK (product_url ~ '^https?://'),
    CONSTRAINT sterbrust_products_seen_ck CHECK (last_seen_at >= first_seen_at),
    CONSTRAINT sterbrust_products_json_ck CHECK (jsonb_typeof(category_metadata) = 'object' AND jsonb_typeof(properties) = 'object' AND jsonb_typeof(raw_data) = 'object')
);

CREATE TABLE catalog_product_identifiers (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    catalog_product_id  bigint NOT NULL REFERENCES catalog_products(id) ON DELETE CASCADE,
    identifier_type     text NOT NULL,
    normalized_value    text NOT NULL,
    raw_value           text NOT NULL,
    brand_id            bigint REFERENCES brands(id) ON DELETE SET NULL,
    supplier_id         bigint REFERENCES suppliers(id) ON DELETE CASCADE,
    source_kind         text NOT NULL,
    source_record_id    text,
    reliability         text NOT NULL DEFAULT 'supporting',
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT catalog_product_identifiers_type_ck CHECK (identifier_type IN ('sterbrust_id', 'manufacturer_article', 'gtin', 'ean', 'upc', 'model', 'supplier_article', 'source_external_id')),
    CONSTRAINT catalog_product_identifiers_reliability_ck CHECK (reliability IN ('global', 'manufacturer', 'supplier_scoped', 'supporting')),
    CONSTRAINT catalog_product_identifiers_supplier_ck CHECK ((identifier_type IN ('supplier_article', 'source_external_id')) = (supplier_id IS NOT NULL))
);

CREATE TABLE brand_aliases (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    brand_id            bigint NOT NULL REFERENCES brands(id) ON DELETE CASCADE,
    alias_raw           text NOT NULL,
    normalized_alias    text NOT NULL,
    approved            boolean NOT NULL DEFAULT false,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT brand_aliases_normalized_uq UNIQUE (normalized_alias)
);

CREATE TABLE product_matches (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_product_id   bigint NOT NULL REFERENCES source_products(id) ON DELETE CASCADE,
    catalog_product_id  bigint REFERENCES catalog_products(id) ON DELETE RESTRICT,
    status              text NOT NULL,
    match_method        text NOT NULL,
    confidence_score    numeric(6,5) NOT NULL,
    evidence            jsonb NOT NULL DEFAULT '{}'::jsonb,
    conflicts           jsonb NOT NULL DEFAULT '{}'::jsonb,
    rule_version        text NOT NULL,
    auto_accepted       boolean NOT NULL DEFAULT false,
    created_at          timestamptz NOT NULL DEFAULT now(),
    accepted_at         timestamptz,
    reviewed_at         timestamptz,
    CONSTRAINT product_matches_status_ck CHECK (status IN ('EXACT_MATCH', 'HIGH_CONFIDENCE_MATCH', 'REVIEW', 'NEW_CANDIDATE', 'CONFLICT')),
    CONSTRAINT product_matches_confidence_ck CHECK (confidence_score BETWEEN 0 AND 1),
    CONSTRAINT product_matches_json_ck CHECK (jsonb_typeof(evidence) = 'object' AND jsonb_typeof(conflicts) = 'object'),
    CONSTRAINT product_matches_auto_ck CHECK (NOT auto_accepted OR (status IN ('EXACT_MATCH', 'HIGH_CONFIDENCE_MATCH') AND catalog_product_id IS NOT NULL AND jsonb_typeof(evidence) = 'object' AND evidence <> '{}'::jsonb)),
    CONSTRAINT product_matches_source_rule_uq UNIQUE (source_product_id, rule_version)
);

-- Structural placeholder only. Stage 3A does not implement offer selection.
CREATE TABLE catalog_offer_selection (
    catalog_product_id  bigint PRIMARY KEY REFERENCES catalog_products(id) ON DELETE CASCADE,
    selected_offer_id   bigint,
    selection_status    text NOT NULL DEFAULT 'not_evaluated',
    rule_version        text,
    evidence            jsonb NOT NULL DEFAULT '{}'::jsonb,
    evaluated_at        timestamptz,
    CONSTRAINT catalog_offer_selection_status_ck CHECK (selection_status IN ('not_evaluated', 'selected', 'no_eligible_offer', 'review')),
    CONSTRAINT catalog_offer_selection_offer_fk FOREIGN KEY (selected_offer_id)
        REFERENCES offers(id) DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT catalog_offer_selection_json_ck CHECK (jsonb_typeof(evidence) = 'object')
);

COMMIT;
