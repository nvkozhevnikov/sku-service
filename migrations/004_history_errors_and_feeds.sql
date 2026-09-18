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
