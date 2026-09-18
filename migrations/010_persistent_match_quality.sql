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
