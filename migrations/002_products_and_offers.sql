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
