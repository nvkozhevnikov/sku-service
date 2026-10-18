BEGIN;

-- Session revocation: every signed session cookie carries the user's
-- session_version; bumping it (logout, deactivation, admin action) invalidates
-- all of that user's outstanding sessions without a server-side session table.
ALTER TABLE app_users ADD COLUMN session_version integer NOT NULL DEFAULT 1;
ALTER TABLE app_users ADD CONSTRAINT app_users_session_version_ck CHECK (session_version >= 1);

-- Login throttle shared by all web workers and surviving restarts.
-- throttle_key is sha256(username || NUL || client address); no raw usernames
-- or addresses are stored. Rows are pruned by the application.
CREATE TABLE login_failures (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    throttle_key    char(64) NOT NULL,
    failed_at       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT login_failures_key_ck CHECK (throttle_key ~ '^[0-9a-f]{64}$')
);

CREATE INDEX login_failures_key_time_idx ON login_failures (throttle_key, failed_at);
CREATE INDEX login_failures_failed_at_idx ON login_failures (failed_at);

-- Liveness of background loops (scheduler, orchestrator) for /health.
CREATE TABLE component_heartbeats (
    component               text PRIMARY KEY,
    last_tick_at            timestamptz NOT NULL,
    last_success_at         timestamptz,
    last_error_at           timestamptz,
    last_error              text,
    consecutive_failures    integer NOT NULL DEFAULT 0,
    poll_seconds            double precision NOT NULL,
    updated_at              timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT component_heartbeats_component_ck CHECK (component IN ('scheduler','orchestrator')),
    CONSTRAINT component_heartbeats_failures_ck CHECK (consecutive_failures >= 0),
    CONSTRAINT component_heartbeats_poll_ck CHECK (poll_seconds > 0)
);

COMMIT;
