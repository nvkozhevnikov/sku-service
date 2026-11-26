BEGIN;

-- The settings page and audit listings read the newest events first
-- (ORDER BY created_at DESC, id DESC LIMIT n); login auditing makes the table
-- grow with every sign-in. Additive and idempotent; no data changes.
CREATE INDEX IF NOT EXISTS audit_events_recent_idx ON audit_events (created_at DESC, id DESC);

COMMIT;
