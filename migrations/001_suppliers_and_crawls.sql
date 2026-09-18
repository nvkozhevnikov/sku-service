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
