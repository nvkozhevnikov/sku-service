# RUNBOOK — Unified RC operator procedures (2026-10-30)

Every procedure below is local-machine only. No procedure here performs a
LIVE supplier request, production write, push, merge or import.

## 1. PostgreSQL 55461 after a Windows reboot

Cluster: PG 17.11, port 55461, database `rc_verify_vi_20261019`,
system identifier **7698346631476390904**.
PGDATA: `C:/Users/man503/.codex/visualizations/2026/10/19/01a153ff-dc7c-77b3-a6be-d05f81a86e5c/rc-pg-20261019`
Binaries: `%LOCALAPPDATA%\Temp\universal_supplier_stage4_pg17_runtime\bin`

1. Check it is really down: port 55461 must not be listening and no
   `postgres.exe` holds it (`netstat -ano -p tcp | findstr :55461`).
2. If `postmaster.pid` exists and its PID is dead (Windows killed the
   postmaster at shutdown): PRESERVE it first — copy to
   `postmaster.pid.original.<timestamp>` — then remove the original.
   Never clear pid/lock files silently; never initdb or overwrite PGDATA.
3. Start (PowerShell, from any directory):

```bash
powershell -NoProfile -Command "& \"$env:LOCALAPPDATA\Temp\universal_supplier_stage4_pg17_runtime\bin\pg_ctl.exe\" start -D 'C:/Users/man503/.codex/visualizations/2026/10/19/01a153ff-dc7c-77b3-a6be-d05f81a86e5c/rc-pg-20261019' -l '<PGDATA>/restart-<date>.log' -w -t 60"
```

4. Verify identity before ANY further step (expected exactly):
   db `rc_verify_vi_20261019`, version `17.11`, system id
   `7698346631476390904`, port 55461. WAL redo after an unclean OS shutdown
   is normal; the log must end with `database system is ready to accept
   connections`.
5. Stop (planned maintenance only): same pg_ctl with `stop -m fast`.

Recovery of the 55449 rc-local cluster has its own guarded script
(`scripts/start_local_rc_postgres.ps1`, `-RecoverStalePid`); do not mix the two.

## 2. Pre-flight before any LIVE window (checklist)

1. `git status` clean; `git rev-parse HEAD` recorded; branch
   `feature/sterbrust-autonomous-system-20261026`.
2. PostgreSQL 55461 identity (§1.4).
3. SQL: 0 jobs in `queued`/`running`; `source_products` mapped count = 1282.
4. Window accounting from the last approved window's session report
   (597 COMMITTED / 353 quarantined / 320 intervesp NOT_REQUESTED for the
   2026-10-29 window; recompute from the session's hourly report, do not
   trust memory).
5. Intervesp job evidence: latest `reports/source-runner-evidence/intervesp/
   job-*/result.json` — expected stop reasons only
   (`ROBOTS_REQUIRES_SLOWER_PROFILE` / `SOURCE_ROBOTS_UNAVAILABLE`),
   frontier exactly 320, terminal 0.
6. Offline tests green (see QUALITY_GATES.md §robots exception).

## 3. Intervesp 5 s mode — decision and LIVE conditions

**Conflict, not hidden**: intervesp.ru/robots.txt (captured 2026-10-29,
SHA256 `a6862161f2c07c36f0ecc789bf7d31cd48ef41b02c142c41d7f7c4e861d53f51`)
declares `Crawl-delay: 20` for `User-Agent: *`. The runtime guard stops any
profile faster than the declared minimum. 5 s < 20 s ⇒ a 5 s run needs an
explicit exception signed by the project owner.

Options (owner decision required):
- **A. 5 s with signed exception** — new approval containing, for intervesp
  only: `min_request_interval_seconds: 5.0`, matching `host_policies` digest,
  and the block
  `{"format":"robots-policy-exception/v1","source":"intervesp","host":"intervesp.ru","robots_sha256":"a6862161…","declared_minimum_seconds":20.0,"applied_min_request_interval_seconds":5.0,"requires_operator_confirmation":true}`.
  Runtime accepts it only while the robots bytes are unchanged; intervesp
  site policy change ⇒ new STOP and a new decision.
- **B. 20 s robots-compliant** — no exception; 320 URLs ≈ 107 min pacing.
- **C. Obtain source permission** — external; then re-issue robots or a
  written allowance, reflected in a new approval.

Exact LIVE launch conditions (all mandatory, in order):
1. Owner's written choice of A/B/C.
2. New approval drafted against the NEW HEAD (code with the exception
   mechanism), SHA256 recorded; `requires_operator_confirmation` honored via
   `start_supplier_system_once.ps1 -Approval <file> -Sha256 <sha> -ConfirmSha <sha> -Start`.
3. Preflight §2 fully green, executed against the new HEAD.
4. Execution window ≤24 h and time budget agreed (320 × 5 s ≈ 27 min pure
   pacing for A; ≈107 min for B) — the approval carries its own deadline.
5. During the run: session heartbeat under `reports/automation-runtime/`;
   STOP semantics below. 597 COMMITTED + 353 quarantined URLs are NEVER
   re-requested (durable receipts); the 320 proven NOT_REQUESTED URLs only.
6. Never: parallel requests to accelerate, silent robots bypass, retry of
   quarantined URLs, edits to historical checkpoints.

## 4. STOP semantics (operator)

- Cooperative stop: write `STOP_REQUESTED` file into the job's evidence dir
  (`reports/source-runner-evidence/intervesp/job-<id>/`) — takes effect
  between GETs, never mid-commit.
- Source-local stop (e.g. `ROBOTS_REQUIRES_SLOWER_PROFILE`) pauses that
  source only; session FINAL.json names the reason; no auto-restart.
- After any stop: inspect `result.json` + `FIRST_STOP.json`; resumption
  requires an explicit reviewed path (native RESUME or a signed
  continuation), never a blind rerun.

## 5. Daily autonomous operations (existing, unchanged)

Scheduler/Orchestrator run under an approved session only; XML previews are
exported locally (`import_allowed=false`) after genuine succeeded jobs;
competitor collection runs its own bounded runner. PostgreSQL must be up
(§1) — after any reboot verify before the next scheduled window.
