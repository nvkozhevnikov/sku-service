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
