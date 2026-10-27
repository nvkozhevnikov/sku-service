BEGIN;

-- Preserve failure evidence; runtime needs SELECT/INSERT/UPDATE, never DELETE.
ALTER TABLE public.login_failures ADD COLUMN IF NOT EXISTS cleared_at timestamptz;
CREATE INDEX IF NOT EXISTS login_failures_active_key_time_idx
    ON public.login_failures (throttle_key, failed_at) WHERE cleared_at IS NULL;

COMMIT;
