# Second-developer preservation

REPOSITORY-VERIFIED: base is remote develop58152e4bd390361d3e97ee475bae78a91295e241,
not stale local develope63eb6f. Ancestry retains9e8a579review fixes,
9b8e5b5session revocation/login throttle/heartbeat and58152e4strict keys/CI.

No changes to .github CI, requirements, security.py, heartbeat.py, scheduler.py,
orchestrator.py, normalization.py, property_normalization.py or migrations.
Those paths remain byte-equivalent at Git-object level to the pinned base.
Shared-file changes to web.py and postgres.py were applied by scoped three-way
patches and retain session-version checks, origin/CSRF/login throttling and
operator identity guards. Targeted auth/control-plane/strict-key regressions
passed as part of289Python3.12Windows tests. No historical assertions weakened.

Existing worktrees were read only: no edits/reset/clean/stash/rebase/switch.
Original local develop and production refs remain unchanged. No fetch/push,
merge, PR or deployment performed. Remote develop metadata read only.
