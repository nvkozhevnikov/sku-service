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

