-- Convenience schema view. Source of truth: migrations/*.sql

-- ===== 001_suppliers_and_crawls.sql =====
BEGIN;

CREATE TABLE suppliers (
    id                      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code                    varchar(64) NOT NULL,
    name                    text NOT NULL,
    base_url                text NOT NULL,
    enabled                 boolean NOT NULL DEFAULT true,
    adapter_name            text NOT NULL,
    transport_mode          text NOT NULL DEFAULT 'direct',
    crawl_settings          jsonb NOT NULL DEFAULT '{}'::jsonb,
    rate_settings           jsonb NOT NULL DEFAULT '{}'::jsonb,
    deactivate_after_misses integer NOT NULL DEFAULT 3,
    created_at              timestamptz NOT NULL DEFAULT now(),
    updated_at              timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT suppliers_code_uq UNIQUE (code),
    CONSTRAINT suppliers_code_format_ck CHECK (code ~ '^[a-z][a-z0-9_]*$'),
    CONSTRAINT suppliers_base_url_ck CHECK (base_url ~ '^https?://'),
    CONSTRAINT suppliers_transport_mode_ck CHECK (transport_mode IN ('direct', 'proxy_optional', 'proxy_required')),
    CONSTRAINT suppliers_deactivate_after_misses_ck CHECK (deactivate_after_misses >= 1),
    CONSTRAINT suppliers_crawl_settings_object_ck CHECK (jsonb_typeof(crawl_settings) = 'object'),
    CONSTRAINT suppliers_rate_settings_object_ck CHECK (jsonb_typeof(rate_settings) = 'object')
);

CREATE TABLE crawl_runs (
    id                          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    supplier_id                 bigint NOT NULL REFERENCES suppliers(id) ON DELETE RESTRICT,
    crawl_type                  text NOT NULL,
    started_at                  timestamptz NOT NULL DEFAULT now(),
    finished_at                 timestamptz,
    status                      text NOT NULL DEFAULT 'running',
    discovered_count            integer NOT NULL DEFAULT 0,
    fetched_count               integer NOT NULL DEFAULT 0,
    new_count                   integer NOT NULL DEFAULT 0,
    changed_count               integer NOT NULL DEFAULT 0,
    unchanged_count             integer NOT NULL DEFAULT 0,
    missing_count               integer NOT NULL DEFAULT 0,
    error_count                 integer NOT NULL DEFAULT 0,
    baseline_discovered_count   integer,
    discovery_ratio             numeric(9,6),
    anomalous_discovery         boolean NOT NULL DEFAULT false,
    deactivation_allowed        boolean NOT NULL DEFAULT false,
    metadata                    jsonb NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT crawl_runs_id_supplier_uq UNIQUE (id, supplier_id),
    CONSTRAINT crawl_runs_type_ck CHECK (crawl_type IN ('discovery', 'fast', 'full', 'identity_validation')),
    CONSTRAINT crawl_runs_status_ck CHECK (status IN ('running', 'succeeded', 'partial', 'failed', 'cancelled')),
    CONSTRAINT crawl_runs_finished_ck CHECK (finished_at IS NULL OR finished_at >= started_at),
    CONSTRAINT crawl_runs_counts_ck CHECK (
        discovered_count >= 0 AND fetched_count >= 0 AND new_count >= 0 AND changed_count >= 0
        AND unchanged_count >= 0 AND missing_count >= 0 AND error_count >= 0
    ),
    CONSTRAINT crawl_runs_baseline_ck CHECK (baseline_discovered_count IS NULL OR baseline_discovered_count >= 0),
    CONSTRAINT crawl_runs_ratio_ck CHECK (discovery_ratio IS NULL OR discovery_ratio >= 0),
    CONSTRAINT crawl_runs_metadata_object_ck CHECK (jsonb_typeof(metadata) = 'object')
);

COMMIT;

-- ===== 002_products_and_offers.sql =====
BEGIN;

CREATE TABLE brands (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    normalized_name text NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT brands_normalized_name_uq UNIQUE (normalized_name)
);

CREATE TABLE manufacturers (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    normalized_name text NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT manufacturers_normalized_name_uq UNIQUE (normalized_name)
);

CREATE TABLE catalog_products (
    id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name             text NOT NULL,
    brand_id         bigint REFERENCES brands(id) ON DELETE SET NULL,
    manufacturer_id  bigint REFERENCES manufacturers(id) ON DELETE SET NULL,
    normalized_data  jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT catalog_products_normalized_data_object_ck CHECK (jsonb_typeof(normalized_data) = 'object')
);

CREATE TABLE source_products (
    id                      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    supplier_id             bigint NOT NULL REFERENCES suppliers(id) ON DELETE RESTRICT,
    catalog_product_id      bigint REFERENCES catalog_products(id) ON DELETE SET NULL,
    external_id             text,
    external_id_is_stable   boolean NOT NULL DEFAULT false,
    sku                     text,
    name                    text NOT NULL,
    source_url              text NOT NULL,
    canonical_url           text,
    brand_raw               text,
    manufacturer_raw        text,
    description_text        text,
    description_html        text,
    raw_data                jsonb NOT NULL DEFAULT '{}'::jsonb,
    identity_hash           text,
    description_hash        text,
    properties_hash         text,
    images_hash             text,
    documents_hash          text,
    category_hash           text,
    first_seen_at           timestamptz NOT NULL,
    last_seen_at            timestamptz NOT NULL,
    last_success_at         timestamptz,
    last_changed_at         timestamptz,
    active                  boolean NOT NULL DEFAULT true,
    missed_crawls           integer NOT NULL DEFAULT 0,
    last_http_status        smallint,
    created_at              timestamptz NOT NULL DEFAULT now(),
    updated_at              timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT source_products_id_supplier_uq UNIQUE (id, supplier_id),
    CONSTRAINT source_products_source_url_ck CHECK (source_url ~ '^https?://'),
    CONSTRAINT source_products_canonical_url_ck CHECK (canonical_url IS NULL OR canonical_url ~ '^https?://'),
    CONSTRAINT source_products_external_identity_ck CHECK (NOT external_id_is_stable OR external_id IS NOT NULL),
    CONSTRAINT source_products_missed_crawls_ck CHECK (missed_crawls >= 0),
    CONSTRAINT source_products_http_status_ck CHECK (last_http_status IS NULL OR last_http_status BETWEEN 100 AND 599),
    CONSTRAINT source_products_seen_order_ck CHECK (last_seen_at >= first_seen_at),
    CONSTRAINT source_products_raw_data_object_ck CHECK (jsonb_typeof(raw_data) = 'object')
);

CREATE TABLE source_product_urls (
    id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_product_id bigint NOT NULL REFERENCES source_products(id) ON DELETE CASCADE,
    url               text NOT NULL,
    url_kind          text NOT NULL,
    is_current        boolean NOT NULL DEFAULT false,
    first_seen_at     timestamptz NOT NULL,
    last_seen_at      timestamptz NOT NULL,
    created_at        timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT source_product_urls_product_url_uq UNIQUE (source_product_id, url),
    CONSTRAINT source_product_urls_url_ck CHECK (url ~ '^https?://'),
    CONSTRAINT source_product_urls_kind_ck CHECK (url_kind IN ('source', 'canonical', 'redirect', 'alias')),
    CONSTRAINT source_product_urls_seen_order_ck CHECK (last_seen_at >= first_seen_at)
);

CREATE TABLE offers (
    id                      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    supplier_id             bigint NOT NULL REFERENCES suppliers(id) ON DELETE RESTRICT,
    source_product_id       bigint NOT NULL,
    external_offer_id       text,
    external_id_is_stable   boolean NOT NULL DEFAULT false,
    offer_kind              text NOT NULL DEFAULT 'default',
    sku                     text,
    title                   text,
    price                   numeric(18,4),
    old_price               numeric(18,4),
    currency                varchar(3),
    availability_raw        text,
    availability_normalized text NOT NULL DEFAULT 'unknown',
    quantity                numeric(18,4),
    raw_data                jsonb NOT NULL DEFAULT '{}'::jsonb,
    commercial_hash         text,
    identity_hash           text,
    first_seen_at           timestamptz NOT NULL,
    last_seen_at            timestamptz NOT NULL,
    last_success_at         timestamptz,
    last_changed_at         timestamptz,
    active                  boolean NOT NULL DEFAULT true,
    missed_crawls           integer NOT NULL DEFAULT 0,
    created_at              timestamptz NOT NULL DEFAULT now(),
    updated_at              timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT offers_id_product_uq UNIQUE (id, source_product_id),
    CONSTRAINT offers_id_supplier_uq UNIQUE (id, supplier_id),
    CONSTRAINT offers_product_supplier_fk FOREIGN KEY (source_product_id, supplier_id)
        REFERENCES source_products(id, supplier_id) ON DELETE CASCADE,
    CONSTRAINT offers_external_identity_ck CHECK (NOT external_id_is_stable OR external_offer_id IS NOT NULL),
    CONSTRAINT offers_kind_ck CHECK (offer_kind IN ('default', 'variant')),
    CONSTRAINT offers_price_ck CHECK (price IS NULL OR price >= 0),
    CONSTRAINT offers_old_price_ck CHECK (old_price IS NULL OR old_price >= 0),
    CONSTRAINT offers_currency_ck CHECK (currency IS NULL OR currency ~ '^[A-Z]{3}$'),
    CONSTRAINT offers_availability_ck CHECK (availability_normalized IN (
        'unknown', 'in_stock', 'out_of_stock', 'preorder', 'incoming', 'backorder', 'discontinued'
    )),
    CONSTRAINT offers_quantity_ck CHECK (quantity IS NULL OR quantity >= 0),
    CONSTRAINT offers_missed_crawls_ck CHECK (missed_crawls >= 0),
    CONSTRAINT offers_seen_order_ck CHECK (last_seen_at >= first_seen_at),
    CONSTRAINT offers_raw_data_object_ck CHECK (jsonb_typeof(raw_data) = 'object')
);

COMMIT;

-- ===== 003_catalog_details.sql =====
BEGIN;

CREATE TABLE supplier_categories (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    supplier_id         bigint NOT NULL REFERENCES suppliers(id) ON DELETE RESTRICT,
    external_id         text,
    name                text NOT NULL,
    source_url          text,
    parent_id           bigint,
    depth               integer NOT NULL DEFAULT 0,
    raw_metadata        jsonb NOT NULL DEFAULT '{}'::jsonb,
    first_seen_at       timestamptz NOT NULL,
    last_seen_at        timestamptz NOT NULL,
    active              boolean NOT NULL DEFAULT true,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT supplier_categories_id_supplier_uq UNIQUE (id, supplier_id),
    CONSTRAINT supplier_categories_parent_supplier_fk FOREIGN KEY (parent_id, supplier_id)
        REFERENCES supplier_categories(id, supplier_id) ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT supplier_categories_depth_ck CHECK (depth >= 0),
    CONSTRAINT supplier_categories_url_ck CHECK (source_url IS NULL OR source_url ~ '^https?://'),
    CONSTRAINT supplier_categories_seen_order_ck CHECK (last_seen_at >= first_seen_at),
    CONSTRAINT supplier_categories_raw_metadata_object_ck CHECK (jsonb_typeof(raw_metadata) = 'object')
);

CREATE TABLE source_product_categories (
    supplier_id       bigint NOT NULL REFERENCES suppliers(id) ON DELETE RESTRICT,
    source_product_id bigint NOT NULL,
    category_id       bigint NOT NULL,
    membership_source text NOT NULL DEFAULT 'listing',
    first_seen_at     timestamptz NOT NULL,
    last_seen_at      timestamptz NOT NULL,
    PRIMARY KEY (supplier_id, source_product_id, category_id),
    CONSTRAINT source_product_categories_product_supplier_fk FOREIGN KEY (source_product_id, supplier_id)
        REFERENCES source_products(id, supplier_id) ON DELETE CASCADE,
    CONSTRAINT source_product_categories_category_supplier_fk FOREIGN KEY (category_id, supplier_id)
        REFERENCES supplier_categories(id, supplier_id) ON DELETE CASCADE,
    CONSTRAINT source_product_categories_source_ck CHECK (membership_source IN ('listing', 'breadcrumb', 'sitemap', 'adapter')),
    CONSTRAINT source_product_categories_seen_order_ck CHECK (last_seen_at >= first_seen_at)
);

CREATE TABLE product_category_paths (
    id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    supplier_id       bigint NOT NULL REFERENCES suppliers(id) ON DELETE RESTRICT,
    source_product_id bigint NOT NULL,
    path_type         text NOT NULL DEFAULT 'published',
    is_current        boolean NOT NULL DEFAULT true,
    raw_breadcrumb    jsonb NOT NULL DEFAULT '[]'::jsonb,
    observed_at       timestamptz NOT NULL,
    CONSTRAINT product_category_paths_id_supplier_uq UNIQUE (id, supplier_id),
    CONSTRAINT product_category_paths_product_supplier_fk FOREIGN KEY (source_product_id, supplier_id)
        REFERENCES source_products(id, supplier_id) ON DELETE CASCADE,
    CONSTRAINT product_category_paths_type_ck CHECK (path_type IN ('published', 'canonical', 'alternate')),
    CONSTRAINT product_category_paths_raw_breadcrumb_array_ck CHECK (jsonb_typeof(raw_breadcrumb) = 'array')
);

CREATE TABLE product_category_path_nodes (
    supplier_id  bigint NOT NULL REFERENCES suppliers(id) ON DELETE RESTRICT,
    path_id      bigint NOT NULL,
    position     integer NOT NULL,
    category_id  bigint,
    raw_name     text NOT NULL,
    raw_url      text,
    PRIMARY KEY (supplier_id, path_id, position),
    CONSTRAINT product_category_path_nodes_path_supplier_fk FOREIGN KEY (path_id, supplier_id)
        REFERENCES product_category_paths(id, supplier_id) ON DELETE CASCADE,
    CONSTRAINT product_category_path_nodes_category_supplier_fk FOREIGN KEY (category_id, supplier_id)
        REFERENCES supplier_categories(id, supplier_id) ON DELETE RESTRICT,
    CONSTRAINT product_category_path_nodes_position_ck CHECK (position >= 0),
    CONSTRAINT product_category_path_nodes_url_ck CHECK (raw_url IS NULL OR raw_url ~ '^https?://')
);

CREATE TABLE product_properties (
    id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_product_id bigint NOT NULL REFERENCES source_products(id) ON DELETE CASCADE,
    offer_id          bigint,
    raw_name          text NOT NULL,
    raw_value         text,
    raw_unit          text,
    raw_value_html    text,
    position          integer NOT NULL,
    normalized_name   text,
    normalized_value  text,
    normalized_unit   text,
    raw_metadata      jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT product_properties_offer_product_fk FOREIGN KEY (offer_id, source_product_id)
        REFERENCES offers(id, source_product_id) ON DELETE CASCADE,
    CONSTRAINT product_properties_position_ck CHECK (position >= 0),
    CONSTRAINT product_properties_raw_metadata_object_ck CHECK (jsonb_typeof(raw_metadata) = 'object')
);

CREATE TABLE product_media (
    id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_product_id bigint NOT NULL REFERENCES source_products(id) ON DELETE CASCADE,
    offer_id          bigint,
    media_type        text NOT NULL,
    source_url        text NOT NULL,
    title             text,
    document_type     text,
    position          integer NOT NULL,
    is_primary        boolean NOT NULL DEFAULT false,
    content_hash      text,
    raw_metadata      jsonb NOT NULL DEFAULT '{}'::jsonb,
    first_seen_at     timestamptz NOT NULL,
    last_seen_at      timestamptz NOT NULL,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT product_media_offer_product_fk FOREIGN KEY (offer_id, source_product_id)
        REFERENCES offers(id, source_product_id) ON DELETE CASCADE,
    CONSTRAINT product_media_type_ck CHECK (media_type IN ('image', 'document', 'video')),
    CONSTRAINT product_media_url_ck CHECK (source_url ~ '^https?://'),
    CONSTRAINT product_media_position_ck CHECK (position >= 0),
    CONSTRAINT product_media_seen_order_ck CHECK (last_seen_at >= first_seen_at),
    CONSTRAINT product_media_raw_metadata_object_ck CHECK (jsonb_typeof(raw_metadata) = 'object')
);

CREATE TABLE source_product_options (
    id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    supplier_id       bigint NOT NULL REFERENCES suppliers(id) ON DELETE RESTRICT,
    source_product_id bigint NOT NULL,
    raw_name          text NOT NULL,
    position          integer NOT NULL,
    raw_metadata      jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT source_product_options_id_supplier_uq UNIQUE (id, supplier_id),
    CONSTRAINT source_product_options_product_supplier_fk FOREIGN KEY (source_product_id, supplier_id)
        REFERENCES source_products(id, supplier_id) ON DELETE CASCADE,
    CONSTRAINT source_product_options_position_ck CHECK (position >= 0),
    CONSTRAINT source_product_options_raw_metadata_object_ck CHECK (jsonb_typeof(raw_metadata) = 'object'),
    CONSTRAINT source_product_options_product_position_uq UNIQUE (source_product_id, position)
);

CREATE TABLE source_product_option_values (
    id                    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    supplier_id           bigint NOT NULL REFERENCES suppliers(id) ON DELETE RESTRICT,
    option_id             bigint NOT NULL,
    raw_value             text NOT NULL,
    position              integer NOT NULL,
    target_url            text,
    variant_internal_id   text,
    classification        text NOT NULL DEFAULT 'UNKNOWN_REVIEW',
    is_selected           boolean NOT NULL DEFAULT false,
    raw_metadata          jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at            timestamptz NOT NULL DEFAULT now(),
    updated_at            timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT source_product_option_values_id_supplier_uq UNIQUE (id, supplier_id),
    CONSTRAINT source_product_option_values_option_supplier_fk FOREIGN KEY (option_id, supplier_id)
        REFERENCES source_product_options(id, supplier_id) ON DELETE CASCADE,
    CONSTRAINT source_product_option_values_position_ck CHECK (position >= 0),
    CONSTRAINT source_product_option_values_target_url_ck CHECK (target_url IS NULL OR target_url ~ '^https?://'),
    CONSTRAINT source_product_option_values_classification_ck CHECK (classification IN (
        'NAVIGATION_TO_PRODUCT', 'TRUE_VARIANT', 'INFORMATIONAL', 'UNKNOWN_REVIEW'
    )),
    CONSTRAINT source_product_option_values_raw_metadata_object_ck CHECK (jsonb_typeof(raw_metadata) = 'object'),
    CONSTRAINT source_product_option_values_option_position_uq UNIQUE (option_id, position)
);

CREATE TABLE source_product_relations (
    id                      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    supplier_id             bigint NOT NULL REFERENCES suppliers(id) ON DELETE RESTRICT,
    from_source_product_id  bigint NOT NULL,
    to_source_product_id    bigint NOT NULL,
    relation_type           text NOT NULL,
    source_option_value_id  bigint,
    raw_metadata            jsonb NOT NULL DEFAULT '{}'::jsonb,
    first_seen_at           timestamptz NOT NULL,
    last_seen_at            timestamptz NOT NULL,
    created_at              timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT source_product_relations_from_supplier_fk FOREIGN KEY (from_source_product_id, supplier_id)
        REFERENCES source_products(id, supplier_id) ON DELETE CASCADE,
    CONSTRAINT source_product_relations_to_supplier_fk FOREIGN KEY (to_source_product_id, supplier_id)
        REFERENCES source_products(id, supplier_id) ON DELETE CASCADE,
    CONSTRAINT source_product_relations_option_value_supplier_fk FOREIGN KEY (source_option_value_id, supplier_id)
        REFERENCES source_product_option_values(id, supplier_id) ON DELETE RESTRICT,
    CONSTRAINT source_product_relations_type_ck CHECK (relation_type IN ('navigation', 'variant', 'related', 'accessory', 'replacement')),
    CONSTRAINT source_product_relations_not_self_ck CHECK (from_source_product_id <> to_source_product_id),
    CONSTRAINT source_product_relations_seen_order_ck CHECK (last_seen_at >= first_seen_at),
    CONSTRAINT source_product_relations_raw_metadata_object_ck CHECK (jsonb_typeof(raw_metadata) = 'object')
);

COMMIT;

-- ===== 004_history_errors_and_feeds.sql =====
BEGIN;

CREATE TABLE crawl_run_items (
    crawl_run_id       bigint NOT NULL,
    supplier_id        bigint NOT NULL REFERENCES suppliers(id) ON DELETE RESTRICT,
    source_product_id  bigint,
    discovered_url     text NOT NULL,
    item_status        text NOT NULL,
    http_status        smallint,
    error_type         text,
    processed_at       timestamptz NOT NULL DEFAULT now(),
    raw_metadata       jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (crawl_run_id, discovered_url),
    CONSTRAINT crawl_run_items_run_supplier_fk FOREIGN KEY (crawl_run_id, supplier_id)
        REFERENCES crawl_runs(id, supplier_id) ON DELETE CASCADE,
    CONSTRAINT crawl_run_items_product_supplier_fk FOREIGN KEY (source_product_id, supplier_id)
        REFERENCES source_products(id, supplier_id) ON DELETE RESTRICT,
    CONSTRAINT crawl_run_items_url_ck CHECK (discovered_url ~ '^https?://'),
    CONSTRAINT crawl_run_items_status_ck CHECK (item_status IN (
        'discovered', 'fetched_new', 'fetched_changed', 'fetched_unchanged', 'missing', 'error', 'skipped'
    )),
    CONSTRAINT crawl_run_items_http_status_ck CHECK (http_status IS NULL OR http_status BETWEEN 100 AND 599),
    CONSTRAINT crawl_run_items_raw_metadata_object_ck CHECK (jsonb_typeof(raw_metadata) = 'object')
);

CREATE TABLE crawl_errors (
    id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    crawl_run_id      bigint NOT NULL,
    supplier_id       bigint NOT NULL REFERENCES suppliers(id) ON DELETE RESTRICT,
    source_product_id bigint,
    offer_id          bigint,
    url               text NOT NULL,
    error_type        text NOT NULL,
    http_status       smallint,
    message           text NOT NULL,
    details           jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at        timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT crawl_errors_run_supplier_fk FOREIGN KEY (crawl_run_id, supplier_id)
        REFERENCES crawl_runs(id, supplier_id) ON DELETE CASCADE,
    CONSTRAINT crawl_errors_product_supplier_fk FOREIGN KEY (source_product_id, supplier_id)
        REFERENCES source_products(id, supplier_id) ON DELETE RESTRICT,
    CONSTRAINT crawl_errors_offer_supplier_fk FOREIGN KEY (offer_id, supplier_id)
        REFERENCES offers(id, supplier_id) ON DELETE RESTRICT,
    CONSTRAINT crawl_errors_url_ck CHECK (url ~ '^https?://'),
    CONSTRAINT crawl_errors_http_status_ck CHECK (http_status IS NULL OR http_status BETWEEN 100 AND 599),
    CONSTRAINT crawl_errors_details_object_ck CHECK (jsonb_typeof(details) = 'object')
);

CREATE TABLE entity_change_history (
    id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    crawl_run_id      bigint NOT NULL,
    supplier_id       bigint NOT NULL REFERENCES suppliers(id) ON DELETE RESTRICT,
    source_product_id bigint,
    offer_id          bigint,
    field_group       text NOT NULL,
    field_name        text,
    old_value         jsonb,
    new_value         jsonb,
    changed_at        timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT entity_change_history_run_supplier_fk FOREIGN KEY (crawl_run_id, supplier_id)
        REFERENCES crawl_runs(id, supplier_id) ON DELETE RESTRICT,
    CONSTRAINT entity_change_history_product_supplier_fk FOREIGN KEY (source_product_id, supplier_id)
        REFERENCES source_products(id, supplier_id) ON DELETE RESTRICT,
    CONSTRAINT entity_change_history_offer_supplier_fk FOREIGN KEY (offer_id, supplier_id)
        REFERENCES offers(id, supplier_id) ON DELETE RESTRICT,
    CONSTRAINT entity_change_history_target_ck CHECK (num_nonnulls(source_product_id, offer_id) = 1),
    CONSTRAINT entity_change_history_group_ck CHECK (field_group IN (
        'identity', 'commercial', 'description', 'properties', 'images', 'documents', 'categories', 'other'
    )),
    CONSTRAINT entity_change_history_actual_change_ck CHECK (old_value IS DISTINCT FROM new_value)
);

CREATE TABLE feed_runs (
    id                    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    feed_scope            text NOT NULL,
    supplier_id           bigint REFERENCES suppliers(id) ON DELETE RESTRICT,
    feed_type              text NOT NULL,
    started_at             timestamptz NOT NULL DEFAULT now(),
    finished_at            timestamptz,
    product_count          integer NOT NULL DEFAULT 0,
    offer_count            integer NOT NULL DEFAULT 0,
    file_path              text,
    checksum               text,
    status                 text NOT NULL DEFAULT 'running',
    error                  text,
    metadata               jsonb NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT feed_runs_scope_ck CHECK (feed_scope IN ('supplier', 'combined')),
    CONSTRAINT feed_runs_scope_supplier_ck CHECK (
        (feed_scope = 'supplier' AND supplier_id IS NOT NULL)
        OR (feed_scope = 'combined' AND supplier_id IS NULL)
    ),
    CONSTRAINT feed_runs_status_ck CHECK (status IN ('running', 'succeeded', 'failed', 'cancelled')),
    CONSTRAINT feed_runs_finished_ck CHECK (finished_at IS NULL OR finished_at >= started_at),
    CONSTRAINT feed_runs_counts_ck CHECK (product_count >= 0 AND offer_count >= 0),
    CONSTRAINT feed_runs_metadata_object_ck CHECK (jsonb_typeof(metadata) = 'object')
);

COMMIT;

-- ===== 005_indexes.sql =====
BEGIN;

CREATE UNIQUE INDEX source_products_supplier_stable_external_id_uq
    ON source_products (supplier_id, external_id)
    WHERE external_id IS NOT NULL AND external_id_is_stable;

CREATE INDEX source_products_supplier_sku_idx
    ON source_products (supplier_id, sku)
    WHERE sku IS NOT NULL;

CREATE INDEX source_products_supplier_canonical_url_idx
    ON source_products (supplier_id, canonical_url)
    WHERE canonical_url IS NOT NULL;

CREATE INDEX source_products_supplier_active_seen_idx
    ON source_products (supplier_id, active, last_seen_at DESC);

CREATE INDEX source_products_updated_at_idx ON source_products (updated_at DESC);
CREATE INDEX source_products_last_changed_at_idx ON source_products (last_changed_at DESC) WHERE last_changed_at IS NOT NULL;
CREATE INDEX source_product_urls_url_idx ON source_product_urls (url);
CREATE UNIQUE INDEX source_product_urls_one_current_kind_uq
    ON source_product_urls (source_product_id, url_kind)
    WHERE is_current;

CREATE UNIQUE INDEX offers_supplier_stable_external_id_uq
    ON offers (supplier_id, external_offer_id)
    WHERE external_offer_id IS NOT NULL AND external_id_is_stable;

CREATE INDEX offers_source_product_idx ON offers (source_product_id);
CREATE INDEX offers_supplier_sku_idx ON offers (supplier_id, sku) WHERE sku IS NOT NULL;
CREATE INDEX offers_supplier_active_availability_idx ON offers (supplier_id, active, availability_normalized);
CREATE INDEX offers_updated_at_idx ON offers (updated_at DESC);
CREATE INDEX offers_last_changed_at_idx ON offers (last_changed_at DESC) WHERE last_changed_at IS NOT NULL;
CREATE UNIQUE INDEX offers_one_default_per_product_uq ON offers (source_product_id) WHERE offer_kind = 'default';

CREATE UNIQUE INDEX supplier_categories_supplier_external_id_uq
    ON supplier_categories (supplier_id, external_id)
    WHERE external_id IS NOT NULL;
CREATE INDEX supplier_categories_parent_idx ON supplier_categories (parent_id);
CREATE UNIQUE INDEX supplier_categories_supplier_source_url_uq ON supplier_categories (supplier_id, source_url) WHERE source_url IS NOT NULL;
CREATE INDEX source_product_categories_category_idx ON source_product_categories (supplier_id, category_id, source_product_id);
CREATE INDEX product_category_paths_product_current_idx ON product_category_paths (supplier_id, source_product_id, is_current);
CREATE INDEX product_category_path_nodes_category_idx ON product_category_path_nodes (supplier_id, category_id) WHERE category_id IS NOT NULL;

CREATE UNIQUE INDEX product_properties_product_position_uq
    ON product_properties (source_product_id, position) WHERE offer_id IS NULL;
CREATE UNIQUE INDEX product_properties_offer_position_uq
    ON product_properties (offer_id, position) WHERE offer_id IS NOT NULL;
CREATE INDEX product_properties_product_position_idx ON product_properties (source_product_id, position);
CREATE INDEX product_properties_offer_position_idx ON product_properties (offer_id, position) WHERE offer_id IS NOT NULL;
CREATE INDEX product_properties_normalized_name_idx ON product_properties (normalized_name) WHERE normalized_name IS NOT NULL;

CREATE UNIQUE INDEX product_media_product_url_uq
    ON product_media (source_product_id, media_type, source_url) WHERE offer_id IS NULL;
CREATE UNIQUE INDEX product_media_offer_url_uq
    ON product_media (offer_id, media_type, source_url) WHERE offer_id IS NOT NULL;
CREATE INDEX product_media_product_type_position_idx ON product_media (source_product_id, media_type, position);
CREATE INDEX product_media_offer_type_position_idx ON product_media (offer_id, media_type, position) WHERE offer_id IS NOT NULL;

CREATE INDEX source_product_options_product_position_idx ON source_product_options (supplier_id, source_product_id, position);
CREATE INDEX source_product_option_values_option_position_idx ON source_product_option_values (supplier_id, option_id, position);
CREATE INDEX source_product_option_values_target_url_idx ON source_product_option_values (supplier_id, target_url) WHERE target_url IS NOT NULL;
CREATE INDEX source_product_relations_from_idx ON source_product_relations (supplier_id, from_source_product_id, relation_type);
CREATE INDEX source_product_relations_to_idx ON source_product_relations (supplier_id, to_source_product_id, relation_type);
CREATE UNIQUE INDEX source_product_relations_option_uq
    ON source_product_relations (from_source_product_id, to_source_product_id, relation_type, source_option_value_id)
    WHERE source_option_value_id IS NOT NULL;
CREATE UNIQUE INDEX source_product_relations_direct_uq
    ON source_product_relations (from_source_product_id, to_source_product_id, relation_type)
    WHERE source_option_value_id IS NULL;

CREATE INDEX crawl_runs_supplier_started_idx ON crawl_runs (supplier_id, started_at DESC);
CREATE INDEX crawl_runs_status_started_idx ON crawl_runs (status, started_at DESC) WHERE status <> 'succeeded';
CREATE INDEX crawl_run_items_product_idx ON crawl_run_items (supplier_id, source_product_id, crawl_run_id) WHERE source_product_id IS NOT NULL;
CREATE INDEX crawl_errors_run_idx ON crawl_errors (crawl_run_id, created_at);
CREATE INDEX crawl_errors_supplier_created_idx ON crawl_errors (supplier_id, created_at DESC);
CREATE INDEX entity_change_history_product_changed_idx ON entity_change_history (supplier_id, source_product_id, changed_at DESC) WHERE source_product_id IS NOT NULL;
CREATE INDEX entity_change_history_offer_changed_idx ON entity_change_history (supplier_id, offer_id, changed_at DESC) WHERE offer_id IS NOT NULL;
CREATE INDEX entity_change_history_group_changed_idx ON entity_change_history (field_group, changed_at DESC);
CREATE INDEX feed_runs_supplier_started_idx ON feed_runs (supplier_id, started_at DESC);

COMMIT;

-- ===== 006_sterbrust_registry_and_matching.sql =====
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

-- ===== 007_sterbrust_registry_indexes.sql =====
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

-- ===== 008_sterbrust_supplier_identifiers.sql =====
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

-- ===== 009_matching_data_quality.sql =====
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

-- ===== 010_persistent_match_quality.sql =====
BEGIN;

-- source_products.catalog_product_id remains the authoritative current accepted
-- supplier-to-catalog relationship. product_matches stores decision events.
ALTER TABLE product_matches
    ADD COLUMN last_validated_at timestamptz NOT NULL DEFAULT now(),
    ADD COLUMN is_current boolean NOT NULL DEFAULT true,
    ADD COLUMN previous_match_id bigint REFERENCES product_matches(id) ON DELETE SET NULL,
    ADD COLUMN decision_fingerprint varchar(64),
    ADD COLUMN warning_fingerprint varchar(64),
    ADD CONSTRAINT product_matches_validation_time_ck CHECK (last_validated_at >= created_at),
    ADD CONSTRAINT product_matches_decision_fingerprint_ck CHECK (
        decision_fingerprint IS NULL OR decision_fingerprint ~ '^[0-9a-f]{64}$'
    ),
    ADD CONSTRAINT product_matches_warning_fingerprint_ck CHECK (
        warning_fingerprint IS NULL OR warning_fingerprint ~ '^[0-9a-f]{64}$'
    );

-- Existing packages can contain one decision per rule version. Mark only the
-- newest one current before enforcing a single current decision per source.
WITH ranked AS (
    SELECT id, row_number() OVER (
        PARTITION BY source_product_id ORDER BY created_at DESC, id DESC
    ) AS position
    FROM product_matches
)
UPDATE product_matches AS target
SET is_current = (ranked.position = 1)
FROM ranked
WHERE target.id = ranked.id;

ALTER TABLE product_matches
    DROP CONSTRAINT product_matches_source_rule_uq;

CREATE UNIQUE INDEX product_matches_one_current_per_source_uq
    ON product_matches (source_product_id)
    WHERE is_current;

CREATE INDEX product_matches_history_idx
    ON product_matches (source_product_id, created_at DESC, id DESC);

CREATE INDEX product_matches_previous_idx
    ON product_matches (previous_match_id)
    WHERE previous_match_id IS NOT NULL;

COMMIT;

-- ===== 011_sterbrust_optional_product_url.sql =====
BEGIN;

-- The read-only Sterbrust registry contains legacy/inactive products for which
-- the API exposes no public URL. Keep those identity records without inventing
-- a URL; when a URL is present it must still be absolute HTTP(S).
ALTER TABLE sterbrust_products
    DROP CONSTRAINT sterbrust_products_url_ck,
    ALTER COLUMN product_url DROP NOT NULL,
    ADD CONSTRAINT sterbrust_products_url_ck
        CHECK (product_url IS NULL OR product_url ~ '^https?://');

COMMIT;

-- ===== 012_web_control_plane_scheduler.sql =====
BEGIN;

-- Stage 6C schema gap: crawl_runs represents work only after it starts.  It
-- cannot persist queued/manual/scheduled intent, leases, deduplication or cron
-- state.  These objects are internal supplier-crawl control-plane state only.
CREATE TABLE crawl_jobs (
    id                    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    supplier_id           bigint NOT NULL REFERENCES suppliers(id) ON DELETE RESTRICT,
    trigger_type          text NOT NULL,
    crawl_type            text NOT NULL,
    status                text NOT NULL DEFAULT 'queued',
    scheduled_for         timestamptz NOT NULL,
    requested_at          timestamptz NOT NULL DEFAULT now(),
    started_at            timestamptz,
    heartbeat_at          timestamptz,
    lease_expires_at      timestamptz,
    finished_at           timestamptz,
    crawl_run_id          bigint REFERENCES crawl_runs(id) ON DELETE SET NULL,
    settings_snapshot     jsonb NOT NULL,
    error_message         text,
    deduplicated_job_id   bigint REFERENCES crawl_jobs(id) ON DELETE SET NULL,
    created_at            timestamptz NOT NULL DEFAULT now(),
    updated_at            timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT crawl_jobs_trigger_ck CHECK (trigger_type IN ('manual', 'schedule')),
    CONSTRAINT crawl_jobs_type_ck CHECK (crawl_type IN ('discovery', 'fast', 'full', 'identity_validation')),
    CONSTRAINT crawl_jobs_status_ck CHECK (status IN (
        'queued', 'running', 'succeeded', 'partial', 'failed', 'cancelled',
        'skipped_locked', 'skipped_already_queued', 'stale_failed'
    )),
    CONSTRAINT crawl_jobs_settings_object_ck CHECK (jsonb_typeof(settings_snapshot) = 'object'),
    CONSTRAINT crawl_jobs_times_ck CHECK (
        (started_at IS NULL OR started_at >= requested_at)
        AND (finished_at IS NULL OR started_at IS NULL OR finished_at >= started_at)
    )
);

CREATE UNIQUE INDEX crawl_jobs_one_active_supplier_uq
    ON crawl_jobs (supplier_id)
    WHERE status IN ('queued', 'running');
CREATE INDEX crawl_jobs_queue_idx
    ON crawl_jobs (scheduled_for, requested_at, id)
    WHERE status = 'queued';
CREATE INDEX crawl_jobs_supplier_history_idx
    ON crawl_jobs (supplier_id, requested_at DESC, id DESC);
CREATE INDEX crawl_jobs_running_lease_idx
    ON crawl_jobs (lease_expires_at)
    WHERE status = 'running';

CREATE TABLE supplier_schedules (
    supplier_id            bigint PRIMARY KEY REFERENCES suppliers(id) ON DELETE CASCADE,
    enabled                boolean NOT NULL DEFAULT false,
    cron_expression        text,
    timezone               text NOT NULL DEFAULT 'UTC',
    crawl_type             text NOT NULL DEFAULT 'full',
    last_enqueued_for      timestamptz,
    last_evaluated_at      timestamptz,
    created_at             timestamptz NOT NULL DEFAULT now(),
    updated_at             timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT supplier_schedules_cron_ck CHECK (
        (NOT enabled) OR (cron_expression IS NOT NULL AND btrim(cron_expression) <> '')
    ),
    CONSTRAINT supplier_schedules_timezone_ck CHECK (btrim(timezone) <> ''),
    CONSTRAINT supplier_schedules_type_ck CHECK (crawl_type IN ('discovery', 'fast', 'full', 'identity_validation'))
);

-- Stable Stage 6B presentation layer.  It does not mutate matching decisions;
-- imports preserve the rule version and full evidence/reason.
CREATE TABLE product_identity_decisions (
    source_product_id      bigint PRIMARY KEY REFERENCES source_products(id) ON DELETE CASCADE,
    decision               text NOT NULL,
    own_model              text,
    reference_model        text,
    product_kind           text,
    best_sterbrust_id      text,
    decision_reason        text NOT NULL,
    conflicts              jsonb NOT NULL DEFAULT '{}'::jsonb,
    identity_characteristics jsonb NOT NULL DEFAULT '{}'::jsonb,
    rule_version           text NOT NULL,
    audited_at             timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT product_identity_decision_ck CHECK (decision IN (
        'EXACT_EXISTING', 'HIGH_CONFIDENCE_EXISTING', 'REVIEW_EXISTING', 'CONFLICT',
        'SAFE_NEW_PRODUCT_CANDIDATE', 'ACCESSORY_OR_COMPATIBILITY_ITEM', 'INSUFFICIENT_IDENTITY'
    )),
    CONSTRAINT product_identity_json_ck CHECK (
        jsonb_typeof(conflicts) = 'object' AND jsonb_typeof(identity_characteristics) = 'object'
    )
);

CREATE INDEX product_identity_decision_idx ON product_identity_decisions (decision, source_product_id);
CREATE INDEX product_identity_kind_idx ON product_identity_decisions (product_kind, source_product_id);
CREATE INDEX source_products_supplier_brand_id_idx ON source_products (supplier_id, brand_raw, id);
CREATE INDEX source_products_supplier_name_id_idx ON source_products (supplier_id, lower(name), id);
CREATE INDEX offers_product_availability_idx ON offers (source_product_id, availability_normalized, id);

-- Preserve existing values: defaults fill only absent keys.
UPDATE suppliers SET rate_settings =
    CASE code
      WHEN 'partner_st' THEN '{"max_concurrency":4,"min_request_interval_seconds":0.20,"jitter_min_seconds":0.0,"jitter_max_seconds":0.10,"request_timeout_seconds":40,"max_attempts":3,"backoff_base_seconds":0.5,"backoff_max_seconds":8,"respect_retry_after":true,"consecutive_error_threshold":5,"cooldown_seconds":30,"max_run_duration_seconds":7200}'::jsonb || rate_settings
      WHEN 'optimum' THEN '{"max_concurrency":3,"min_request_interval_seconds":0.15,"jitter_min_seconds":0.0,"jitter_max_seconds":0.10,"request_timeout_seconds":35,"max_attempts":3,"backoff_base_seconds":0.5,"backoff_max_seconds":8,"respect_retry_after":true,"consecutive_error_threshold":5,"cooldown_seconds":30,"max_run_duration_seconds":7200}'::jsonb || rate_settings
      ELSE rate_settings
    END,
    updated_at = now()
WHERE code IN ('partner_st', 'optimum');

INSERT INTO supplier_schedules (supplier_id, enabled, timezone, crawl_type)
SELECT id, false, 'UTC', 'full' FROM suppliers
ON CONFLICT (supplier_id) DO NOTHING;

COMMIT;

-- ===== 013_operator_admin_review.sql =====
BEGIN;

-- Stage 6D adds internal operator identity and review state.  It does not add
-- any Sterbrust/ESOL write path.
CREATE TABLE app_users (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    username        text NOT NULL,
    display_name    text NOT NULL,
    password_hash   text NOT NULL,
    role            text NOT NULL,
    is_active       boolean NOT NULL DEFAULT true,
    created_at      timestamptz NOT NULL DEFAULT now(),
    last_login_at   timestamptz,
    updated_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT app_users_username_ck CHECK (username = lower(username) AND username ~ '^[a-z0-9][a-z0-9_.-]{2,63}$'),
    CONSTRAINT app_users_display_name_ck CHECK (btrim(display_name) <> ''),
    CONSTRAINT app_users_password_hash_ck CHECK (password_hash LIKE 'scrypt$%'),
    CONSTRAINT app_users_role_ck CHECK (role IN ('ADMIN','OPERATOR','VIEWER')),
    CONSTRAINT app_users_username_uq UNIQUE (username)
);

CREATE TABLE review_cases (
    id                          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_product_id           bigint NOT NULL REFERENCES source_products(id) ON DELETE CASCADE,
    lifecycle_status            text NOT NULL DEFAULT 'OPEN',
    priority_rank               integer NOT NULL,
    priority_reason             text NOT NULL,
    automatic_decision          text NOT NULL,
    proposed_sterbrust_id       text REFERENCES sterbrust_products(sterbrust_product_id) ON DELETE SET NULL,
    explanation                 jsonb NOT NULL DEFAULT '{}'::jsonb,
    source_identity_fingerprint char(64) NOT NULL,
    source_identity_snapshot    jsonb NOT NULL,
    assigned_to_user_id         bigint REFERENCES app_users(id) ON DELETE SET NULL,
    postponed_comment           text,
    resolved_decision_class     text,
    opened_at                   timestamptz NOT NULL DEFAULT now(),
    resolved_at                 timestamptz,
    updated_at                  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT review_cases_source_uq UNIQUE (source_product_id),
    CONSTRAINT review_cases_lifecycle_ck CHECK (lifecycle_status IN ('OPEN','ASSIGNED','POSTPONED','RESOLVED','REOPENED_SOURCE_CHANGED')),
    CONSTRAINT review_cases_priority_ck CHECK (priority_rank BETWEEN 1 AND 4),
    CONSTRAINT review_cases_json_ck CHECK (jsonb_typeof(explanation)='object' AND jsonb_typeof(source_identity_snapshot)='object'),
    CONSTRAINT review_cases_fingerprint_ck CHECK (source_identity_fingerprint ~ '^[0-9a-f]{64}$'),
    CONSTRAINT review_cases_resolution_ck CHECK ((lifecycle_status='RESOLVED') = (resolved_decision_class IS NOT NULL))
);

CREATE INDEX review_cases_queue_idx ON review_cases (lifecycle_status, priority_rank, updated_at, id);
CREATE INDEX review_cases_assignee_idx ON review_cases (assigned_to_user_id, lifecycle_status, priority_rank);

CREATE TABLE review_decisions (
    id                          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    review_case_id              bigint NOT NULL REFERENCES review_cases(id) ON DELETE RESTRICT,
    source_product_id           bigint NOT NULL REFERENCES source_products(id) ON DELETE RESTRICT,
    user_id                     bigint NOT NULL REFERENCES app_users(id) ON DELETE RESTRICT,
    decision_class              text NOT NULL,
    sterbrust_product_id        text REFERENCES sterbrust_products(sterbrust_product_id) ON DELETE RESTRICT,
    parent_sterbrust_product_id text REFERENCES sterbrust_products(sterbrust_product_id) ON DELETE RESTRICT,
    comment                     text,
    source_identity_fingerprint char(64) NOT NULL,
    source_identity_snapshot    jsonb NOT NULL,
    previous_decision_id        bigint REFERENCES review_decisions(id) ON DELETE SET NULL,
    created_at                  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT review_decisions_class_ck CHECK (decision_class IN ('MANUAL_CONFIRMED','MANUAL_CONFIRMED_NEW','MANUAL_ACCESSORY','POSTPONED','MARKED_REVIEWED')),
    CONSTRAINT review_decisions_comment_ck CHECK (
      (decision_class='MANUAL_CONFIRMED' AND sterbrust_product_id IS NOT NULL)
      OR (decision_class='MANUAL_CONFIRMED_NEW' AND sterbrust_product_id IS NULL AND btrim(coalesce(comment,''))<>'')
      OR (decision_class='MANUAL_ACCESSORY' AND btrim(coalesce(comment,''))<>'')
      OR decision_class IN ('POSTPONED','MARKED_REVIEWED')
    ),
    CONSTRAINT review_decisions_json_ck CHECK (jsonb_typeof(source_identity_snapshot)='object')
);
CREATE INDEX review_decisions_case_history_idx ON review_decisions (review_case_id, created_at DESC, id DESC);

CREATE TABLE candidate_rejections (
    id                          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    review_case_id              bigint NOT NULL REFERENCES review_cases(id) ON DELETE RESTRICT,
    source_product_id           bigint NOT NULL REFERENCES source_products(id) ON DELETE RESTRICT,
    sterbrust_product_id        text NOT NULL REFERENCES sterbrust_products(sterbrust_product_id) ON DELETE RESTRICT,
    user_id                     bigint NOT NULL REFERENCES app_users(id) ON DELETE RESTRICT,
    evidence_fingerprint        char(64) NOT NULL,
    comment                     text NOT NULL,
    created_at                  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT candidate_rejections_comment_ck CHECK (btrim(comment)<>''),
    CONSTRAINT candidate_rejections_uq UNIQUE (source_product_id, sterbrust_product_id, evidence_fingerprint)
);

CREATE TABLE audit_events (
    id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id          bigint REFERENCES app_users(id) ON DELETE RESTRICT,
    action           text NOT NULL,
    entity_type      text NOT NULL,
    entity_id        text NOT NULL,
    before_state     jsonb,
    after_state      jsonb,
    comment          text,
    correlation_id   text,
    created_at       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT audit_events_action_ck CHECK (btrim(action)<>''),
    CONSTRAINT audit_events_entity_ck CHECK (btrim(entity_type)<>'' AND btrim(entity_id)<>''),
    CONSTRAINT audit_events_json_ck CHECK ((before_state IS NULL OR jsonb_typeof(before_state)='object') AND (after_state IS NULL OR jsonb_typeof(after_state)='object'))
);
CREATE INDEX audit_events_entity_idx ON audit_events (entity_type, entity_id, created_at DESC, id DESC);
CREATE INDEX audit_events_user_idx ON audit_events (user_id, created_at DESC, id DESC);

CREATE OR REPLACE FUNCTION prevent_audit_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'audit_events is append-only';
END;
$$;
CREATE TRIGGER audit_events_no_update BEFORE UPDATE OR DELETE ON audit_events
FOR EACH ROW EXECUTE FUNCTION prevent_audit_mutation();

ALTER TABLE crawl_jobs ADD COLUMN requested_by_user_id bigint REFERENCES app_users(id) ON DELETE SET NULL;
CREATE INDEX crawl_jobs_requested_by_idx ON crawl_jobs (requested_by_user_id, requested_at DESC, id DESC);

COMMIT;

-- ===== 014_stage6e_operator_data_tools.sql =====
BEGIN;

-- Stage 6E is an internal-data and read-only preview layer.  No table or
-- function in this migration can write to Sterbrust or invoke ESOL.
ALTER TABLE offers
    ADD COLUMN price_type text NOT NULL DEFAULT 'unknown',
    ADD COLUMN price_raw text,
    ADD COLUMN price_source text,
    ADD CONSTRAINT offers_price_type_ck CHECK (price_type IN ('RRP','retail','wholesale','dealer','promo','unknown'));

CREATE TABLE data_quality_snapshots (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    supplier_id bigint NOT NULL REFERENCES suppliers(id) ON DELETE CASCADE,
    crawl_run_id bigint REFERENCES crawl_runs(id) ON DELETE SET NULL,
    products_seen integer NOT NULL,
    products_parsed integer NOT NULL,
    metrics jsonb NOT NULL,
    anomaly_codes text[] NOT NULL DEFAULT '{}',
    critical boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT data_quality_counts_ck CHECK (products_seen >= 0 AND products_parsed >= 0),
    CONSTRAINT data_quality_metrics_ck CHECK (jsonb_typeof(metrics)='object')
);
CREATE INDEX data_quality_supplier_time_idx ON data_quality_snapshots(supplier_id,created_at DESC,id DESC);

CREATE TABLE diagnostic_evidence_snapshots (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    supplier_id bigint NOT NULL REFERENCES suppliers(id) ON DELETE CASCADE,
    source_product_id bigint REFERENCES source_products(id) ON DELETE CASCADE,
    crawl_run_id bigint REFERENCES crawl_runs(id) ON DELETE SET NULL,
    evidence_type text NOT NULL,
    source_url text NOT NULL,
    http_metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    sanitized_excerpt text,
    parser_version text,
    observed_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL DEFAULT (now()+interval '30 days'),
    CONSTRAINT diagnostic_evidence_type_ck CHECK (evidence_type IN ('parser_error','schema_drift','identity_conflict','material_identity_change')),
    CONSTRAINT diagnostic_evidence_url_ck CHECK (source_url ~ '^https?://'),
    CONSTRAINT diagnostic_evidence_metadata_ck CHECK (jsonb_typeof(http_metadata)='object'),
    CONSTRAINT diagnostic_evidence_expiry_ck CHECK (expires_at>observed_at)
);

CREATE TABLE missing_observations (
    source_product_id bigint PRIMARY KEY REFERENCES source_products(id) ON DELETE CASCADE,
    consecutive_confirmed integer NOT NULL DEFAULT 0,
    last_evidence_type text,
    state text NOT NULL DEFAULT 'ACTIVE',
    first_missing_at timestamptz,
    last_observed_at timestamptz NOT NULL DEFAULT now(),
    last_crawl_run_id bigint REFERENCES crawl_runs(id) ON DELETE SET NULL,
    history jsonb NOT NULL DEFAULT '[]'::jsonb,
    CONSTRAINT missing_observations_count_ck CHECK (consecutive_confirmed>=0),
    CONSTRAINT missing_observations_evidence_ck CHECK (last_evidence_type IS NULL OR last_evidence_type IN ('HTTP_404','HTTP_410','NOT_IN_SITEMAP','NOT_DISCOVERED','TIMEOUT','HTTP_403','HTTP_429','NETWORK_ERROR','PARSER_ERROR','SEEN')),
    CONSTRAINT missing_observations_state_ck CHECK (state IN ('ACTIVE','WATCH','WARNING','MISSING_CANDIDATE')),
    CONSTRAINT missing_observations_history_ck CHECK (jsonb_typeof(history)='array')
);

CREATE TABLE business_rules (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    supplier_id bigint REFERENCES suppliers(id) ON DELETE CASCADE,
    scope text NOT NULL,
    entity_key text,
    rule_type text NOT NULL,
    config jsonb NOT NULL DEFAULT '{}'::jsonb,
    reason text NOT NULL,
    created_by bigint NOT NULL REFERENCES app_users(id) ON DELETE RESTRICT,
    active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT business_rules_scope_ck CHECK (scope IN ('supplier','product','category','property')),
    CONSTRAINT business_rules_type_ck CHECK (rule_type IN ('IGNORE_PRODUCT','DO_NOT_IMPORT','FORCE_REVIEW','BRAND_OVERRIDE','CATEGORY_OVERRIDE','PROPERTY_MAPPING_OVERRIDE','DO_NOT_USE_SUPPLIER_FIELD')),
    CONSTRAINT business_rules_config_ck CHECK (jsonb_typeof(config)='object'),
    CONSTRAINT business_rules_reason_ck CHECK (btrim(reason)<>'')
);
CREATE INDEX business_rules_active_idx ON business_rules(supplier_id,scope,entity_key,rule_type) WHERE active;

CREATE TABLE canonical_properties (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code text NOT NULL UNIQUE,
    display_name_ru text NOT NULL,
    data_type text NOT NULL,
    canonical_unit text,
    description text,
    active boolean NOT NULL DEFAULT true,
    show_in_characteristics boolean NOT NULL DEFAULT true,
    usable_for_identity boolean NOT NULL DEFAULT false,
    identity_critical boolean NOT NULL DEFAULT false,
    usable_as_filter boolean NOT NULL DEFAULT false,
    created_by bigint REFERENCES app_users(id) ON DELETE SET NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT canonical_properties_code_ck CHECK (code ~ '^[A-Z][A-Z0-9_]*$'),
    CONSTRAINT canonical_properties_name_ck CHECK (btrim(display_name_ru)<>''),
    CONSTRAINT canonical_properties_type_ck CHECK (data_type IN ('number','integer','boolean','string','enum','range')),
    CONSTRAINT canonical_properties_identity_ck CHECK (NOT identity_critical OR usable_for_identity)
);

CREATE TABLE supplier_property_mappings (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    supplier_id bigint NOT NULL REFERENCES suppliers(id) ON DELETE CASCADE,
    category_context text NOT NULL DEFAULT '',
    source_name text NOT NULL,
    normalized_source_name text NOT NULL,
    sample_value text,
    source_unit text,
    inferred_type text,
    canonical_property_id bigint REFERENCES canonical_properties(id) ON DELETE RESTRICT,
    status text NOT NULL DEFAULT 'UNMAPPED',
    confidence numeric(6,5),
    evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
    comment text,
    confirmed_by bigint REFERENCES app_users(id) ON DELETE SET NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT supplier_property_mapping_uq UNIQUE(supplier_id,category_context,normalized_source_name),
    CONSTRAINT supplier_property_mapping_status_ck CHECK (status IN ('AUTO_PROPOSED','MANUAL_CONFIRMED','MANUAL_REJECTED','IGNORED','UNMAPPED')),
    CONSTRAINT supplier_property_mapping_confidence_ck CHECK (confidence IS NULL OR confidence BETWEEN 0 AND 1),
    CONSTRAINT supplier_property_mapping_evidence_ck CHECK (jsonb_typeof(evidence)='object'),
    CONSTRAINT supplier_property_mapping_confirmed_ck CHECK (status<>'MANUAL_CONFIRMED' OR (canonical_property_id IS NOT NULL AND confirmed_by IS NOT NULL))
);

CREATE TABLE sterbrust_property_mappings (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    canonical_property_id bigint NOT NULL REFERENCES canonical_properties(id) ON DELETE CASCADE,
    category_context text NOT NULL DEFAULT '',
    sterbrust_property_id text,
    sterbrust_code text,
    sterbrust_name text,
    sterbrust_type text,
    sterbrust_unit text,
    filter_enabled_evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
    status text NOT NULL DEFAULT 'UNMAPPED',
    comment text,
    confirmed_by bigint REFERENCES app_users(id) ON DELETE SET NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT sterbrust_property_mapping_uq UNIQUE(canonical_property_id,category_context),
    CONSTRAINT sterbrust_property_mapping_status_ck CHECK (status IN ('MATCHED','REVIEW','UNMAPPED','CONFLICT','IGNORED')),
    CONSTRAINT sterbrust_property_mapping_evidence_ck CHECK (jsonb_typeof(filter_enabled_evidence)='object')
);

CREATE TABLE category_filter_profiles (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    category_key text NOT NULL UNIQUE,
    display_name_ru text NOT NULL,
    active boolean NOT NULL DEFAULT true,
    created_by bigint REFERENCES app_users(id) ON DELETE SET NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE category_filter_profile_properties (
    profile_id bigint NOT NULL REFERENCES category_filter_profiles(id) ON DELETE CASCADE,
    canonical_property_id bigint NOT NULL REFERENCES canonical_properties(id) ON DELETE CASCADE,
    use_as_characteristic boolean NOT NULL DEFAULT true,
    use_as_filter boolean NOT NULL DEFAULT false,
    position integer NOT NULL DEFAULT 0,
    PRIMARY KEY(profile_id,canonical_property_id),
    CONSTRAINT category_filter_profile_position_ck CHECK(position>=0)
);

CREATE TABLE field_observations (
    source_product_id bigint NOT NULL REFERENCES source_products(id) ON DELETE CASCADE,
    field_name text NOT NULL,
    value jsonb,
    source_kind text NOT NULL,
    source_path text,
    observed_at timestamptz NOT NULL,
    normalized_by text,
    PRIMARY KEY(source_product_id,field_name),
    CONSTRAINT field_observations_kind_ck CHECK(source_kind IN ('supplier_html','supplier_json','supplier_xml','supplier_api','operator','derived'))
);

CREATE TABLE field_update_policies (
    field_name text PRIMARY KEY,
    policy text NOT NULL,
    reason text,
    updated_by bigint REFERENCES app_users(id) ON DELETE SET NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT field_update_policy_ck CHECK(policy IN ('SUPPLIER_ALLOWED','MANUAL_ONLY','STERBRUST_OWNED','REVIEW_REQUIRED','NEVER_OVERWRITE'))
);
INSERT INTO field_update_policies(field_name,policy,reason) VALUES
 ('price','SUPPLIER_ALLOWED','Commercial supplier field'),
 ('availability','SUPPLIER_ALLOWED','Commercial supplier field'),
 ('quantity','SUPPLIER_ALLOWED','Commercial supplier field'),
 ('description','STERBRUST_OWNED','Editorial content is protected'),
 ('seo_title','STERBRUST_OWNED','Manual SEO field'),
 ('seo_description','STERBRUST_OWNED','Manual SEO field'),
 ('category','MANUAL_ONLY','Category requires operator control'),
 ('brand','REVIEW_REQUIRED','Supplier changes require review'),
 ('main_image','NEVER_OVERWRITE','Primary media is protected');

CREATE TABLE reparse_previews (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_product_id bigint NOT NULL REFERENCES source_products(id) ON DELETE CASCADE,
    requested_by bigint NOT NULL REFERENCES app_users(id) ON DELETE RESTRICT,
    current_data jsonb NOT NULL,
    proposed_data jsonb NOT NULL,
    diff jsonb NOT NULL,
    persisted boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT reparse_preview_json_ck CHECK(jsonb_typeof(current_data)='object' AND jsonb_typeof(proposed_data)='object' AND jsonb_typeof(diff)='object')
);

COMMIT;

-- ===== 015_commercial_capture_observations.sql =====
BEGIN;

-- Additive, append-only evidence for public commercial observations.  This
-- migration never creates catalog_products or product_matches and does not
-- change offer-selection policy.
CREATE TABLE supplier_http_captures (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    supplier_id bigint NOT NULL REFERENCES suppliers(id) ON DELETE RESTRICT,
    source_product_id bigint NOT NULL REFERENCES source_products(id) ON DELETE RESTRICT,
    requested_url text NOT NULL,
    final_url text NOT NULL,
    http_status smallint,
    content_type text,
    observed_at timestamptz NOT NULL,
    capture_fingerprint char(64) NOT NULL,
    response_sha256 char(64) NOT NULL,
    evidence_sha256 char(64) NOT NULL,
    evidence_ref text NOT NULL,
    redirect_urls jsonb NOT NULL DEFAULT '[]'::jsonb,
    region_code text NOT NULL DEFAULT '',
    region_label text,
    diagnostics jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT supplier_http_captures_product_supplier_fk FOREIGN KEY (source_product_id, supplier_id)
        REFERENCES source_products(id, supplier_id) ON DELETE RESTRICT,
    CONSTRAINT supplier_http_captures_urls_ck CHECK (requested_url ~ '^https?://' AND final_url ~ '^https?://'),
    CONSTRAINT supplier_http_captures_status_ck CHECK (http_status IS NULL OR http_status BETWEEN 100 AND 599),
    CONSTRAINT supplier_http_captures_fingerprint_ck CHECK (capture_fingerprint ~ '^[0-9a-f]{64}$'),
    CONSTRAINT supplier_http_captures_response_hash_ck CHECK (response_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT supplier_http_captures_evidence_hash_ck CHECK (evidence_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT supplier_http_captures_redirects_array_ck CHECK (jsonb_typeof(redirect_urls) = 'array'),
    CONSTRAINT supplier_http_captures_diagnostics_object_ck CHECK (jsonb_typeof(diagnostics) = 'object'),
    CONSTRAINT supplier_http_captures_product_capture_uq UNIQUE (source_product_id, capture_fingerprint),
    CONSTRAINT supplier_http_captures_id_product_uq UNIQUE (id, source_product_id)
);

CREATE INDEX supplier_http_captures_supplier_observed_idx
    ON supplier_http_captures (supplier_id, observed_at DESC, id DESC);

CREATE TABLE offer_commercial_observations (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    supplier_id bigint NOT NULL REFERENCES suppliers(id) ON DELETE RESTRICT,
    source_product_id bigint NOT NULL REFERENCES source_products(id) ON DELETE RESTRICT,
    offer_id bigint NOT NULL REFERENCES offers(id) ON DELETE RESTRICT,
    capture_id bigint NOT NULL REFERENCES supplier_http_captures(id) ON DELETE RESTRICT,
    observed_at timestamptz NOT NULL,
    extraction_fingerprint char(64) NOT NULL,
    price_state text NOT NULL,
    price numeric(18,4),
    old_price numeric(18,4),
    currency varchar(3),
    availability_raw text,
    availability_normalized text NOT NULL DEFAULT 'unknown',
    price_type text NOT NULL DEFAULT 'unknown',
    price_raw text,
    price_source text,
    extraction_evidence jsonb NOT NULL,
    unusable_price_reasons jsonb NOT NULL DEFAULT '[]'::jsonb,
    region_code text,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT offer_commercial_observations_product_supplier_fk FOREIGN KEY (source_product_id, supplier_id)
        REFERENCES source_products(id, supplier_id) ON DELETE RESTRICT,
    CONSTRAINT offer_commercial_observations_offer_product_fk FOREIGN KEY (offer_id, source_product_id)
        REFERENCES offers(id, source_product_id) ON DELETE RESTRICT,
    CONSTRAINT offer_commercial_observations_capture_product_fk FOREIGN KEY (capture_id, source_product_id)
        REFERENCES supplier_http_captures(id, source_product_id) ON DELETE RESTRICT,
    CONSTRAINT offer_commercial_observations_fingerprint_ck CHECK (extraction_fingerprint ~ '^[0-9a-f]{64}$'),
    CONSTRAINT offer_commercial_observations_state_ck CHECK (price_state IN ('numeric_public','price_on_request','missing','parse_error','zero_invalid','ambiguous')),
    CONSTRAINT offer_commercial_observations_price_ck CHECK (price IS NULL OR price > 0),
    CONSTRAINT offer_commercial_observations_old_price_ck CHECK (old_price IS NULL OR old_price > 0),
    CONSTRAINT offer_commercial_observations_currency_ck CHECK (currency IS NULL OR currency ~ '^[A-Z]{3}$'),
    CONSTRAINT offer_commercial_observations_price_state_ck CHECK (
        (price_state = 'numeric_public' AND price IS NOT NULL AND currency IS NOT NULL)
        OR (price_state <> 'numeric_public' AND price IS NULL AND old_price IS NULL)
    ),
    CONSTRAINT offer_commercial_observations_availability_ck CHECK (availability_normalized IN ('unknown','in_stock','out_of_stock','preorder','incoming','backorder','discontinued')),
    CONSTRAINT offer_commercial_observations_public_type_ck CHECK (price_type = 'unknown'),
    CONSTRAINT offer_commercial_observations_evidence_object_ck CHECK (jsonb_typeof(extraction_evidence) = 'object'),
    CONSTRAINT offer_commercial_observations_reasons_array_ck CHECK (jsonb_typeof(unusable_price_reasons) = 'array'),
    CONSTRAINT offer_commercial_observations_capture_fingerprint_uq UNIQUE (source_product_id, capture_id, extraction_fingerprint)
);

CREATE INDEX offer_commercial_observations_product_time_idx
    ON offer_commercial_observations (source_product_id, observed_at DESC, id DESC);

-- History is immutable even to the runtime table owner.  RESTRICT FKs make
-- source/offer deletion fail rather than silently erasing observations.
CREATE OR REPLACE FUNCTION commercial_history_append_only()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'commercial history is append-only';
END;
$$;

CREATE TRIGGER supplier_http_captures_append_only
    BEFORE UPDATE OR DELETE ON supplier_http_captures
    FOR EACH ROW EXECUTE FUNCTION commercial_history_append_only();

CREATE TRIGGER offer_commercial_observations_append_only
    BEFORE UPDATE OR DELETE ON offer_commercial_observations
    FOR EACH ROW EXECUTE FUNCTION commercial_history_append_only();

REVOKE UPDATE, DELETE ON supplier_http_captures, offer_commercial_observations FROM PUBLIC;

COMMIT;

-- ===== 016_review_decisions_source_product_index.sql =====
BEGIN;

-- review_decisions.source_product_id references source_products(id) but has no
-- index, so per-product decision lookups and ON DELETE checks scan the table.
-- Additive and idempotent; no data changes.
CREATE INDEX IF NOT EXISTS review_decisions_source_product_idx ON review_decisions (source_product_id);

COMMIT;

-- ===== 017_session_revocation_login_throttle_heartbeats.sql =====
BEGIN;

-- Session revocation: every signed session cookie carries the user's
-- session_version; bumping it (logout, deactivation, admin action) invalidates
-- all of that user's outstanding sessions without a server-side session table.
ALTER TABLE app_users ADD COLUMN session_version integer NOT NULL DEFAULT 1;
ALTER TABLE app_users ADD CONSTRAINT app_users_session_version_ck CHECK (session_version >= 1);

-- Login throttle shared by all web workers and surviving restarts.
-- throttle_key is sha256(username || NUL || client address); no raw usernames
-- or addresses are stored. Rows are pruned by the application.
CREATE TABLE login_failures (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    throttle_key    char(64) NOT NULL,
    failed_at       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT login_failures_key_ck CHECK (throttle_key ~ '^[0-9a-f]{64}$')
);

CREATE INDEX login_failures_key_time_idx ON login_failures (throttle_key, failed_at);
CREATE INDEX login_failures_failed_at_idx ON login_failures (failed_at);

-- Liveness of background loops (scheduler, orchestrator) for /health.
CREATE TABLE component_heartbeats (
    component               text PRIMARY KEY,
    last_tick_at            timestamptz NOT NULL,
    last_success_at         timestamptz,
    last_error_at           timestamptz,
    last_error              text,
    consecutive_failures    integer NOT NULL DEFAULT 0,
    poll_seconds            double precision NOT NULL,
    updated_at              timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT component_heartbeats_component_ck CHECK (component IN ('scheduler','orchestrator')),
    CONSTRAINT component_heartbeats_failures_ck CHECK (consecutive_failures >= 0),
    CONSTRAINT component_heartbeats_poll_ck CHECK (poll_seconds > 0)
);

COMMIT;

-- ===== 018_audit_events_recent_index.sql =====
BEGIN;

-- The settings page and audit listings read the newest events first
-- (ORDER BY created_at DESC, id DESC LIMIT n); login auditing makes the table
-- grow with every sign-in. Additive and idempotent; no data changes.
CREATE INDEX IF NOT EXISTS audit_events_recent_idx ON audit_events (created_at DESC, id DESC);

COMMIT;

