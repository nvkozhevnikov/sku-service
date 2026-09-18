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
