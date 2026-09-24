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
