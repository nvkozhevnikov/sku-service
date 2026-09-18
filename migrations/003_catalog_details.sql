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
