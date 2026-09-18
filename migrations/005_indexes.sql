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
