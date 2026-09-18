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
