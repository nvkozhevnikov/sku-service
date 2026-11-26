# RUNBOOK — Unified RC operator procedures (2026-10-30)

## All11 matching proposals — STOP before mutation, 16Nov

REPOSITORY-VERIFIED: accepted61pairs and source/target hashes are in workspace
reports/MATCHING_ALL11_20261116/PROPOSED_61_LINKS_NOT_AUTHORIZED.json. Do not execute
full-source reconcile/apply from an offline report or an old approval. Obtain exact
operator approval for this file SHA and integrated code first. Recheck readonly
DB identity/1282manual state/null links and hashes; drift stops. Use existing native
review/audit transactions for the approved pairs only; no new canonical products,
retargeting, ingestion, observations, news or XML/LKG replacement. Check expected
additive result separately. [Detailed procedure](docs/unified-rc/ALL11_MATCHING_COVERAGE.md).

## XML continuity — current local view, 16Nov

LIVE-VERIFIED LOCAL: http://127.0.0.1:58161/exports/previews has separate current
strict previews, preserved48/206, historical117/48/206, diagnostics and206candidate
JSON. Login with existing account; initial password change still mandatory. These
downloads grant no import permission. Stage6 contract remains blocked.

REPOSITORY-VERIFIED: use the SAME publisher and reports/xml-previews (or explicit
CONTROL_PLANE_EXPORT_ROOT). Preserve latest.json, latest_attempt.json, versions/,
history/, history_index.json, last_known_good.json, inventory_baseline.json and
historical_new_candidates.json with any file-only evidence backup. Do not reset
baseline or steal publish.lock on error. Copy supplied evidence only into a new
separately reviewed directory; never overwrite an installed runtime/export tree.
[Recovery, limitations and fresh-evidence requirements](docs/unified-rc/XML_CONTINUITY.md).
New code/engine invalidates old unconsumed contracts too; the executed canary grant
is already consumed. No new HTTP, matching apply, import or schedule authorized.

Every procedure below is local-machine only. No procedure here performs a
LIVE supplier request, production write, push, merge or import.

## 1. PostgreSQL 55461 after a Windows reboot

Cluster: PG 17.11, port 55461, database `rc_verify_vi_20261019`,
system identifier **7698346631476390904**.
PGDATA: `C:/Users/man503/.codex/visualizations/2026/10/19/01a153ff-dc7c-77b3-a6be-d05f81a86e5c/rc-pg-20261019`
Binaries: `D:\tools\pg17-runtime\bin` (PG17.11 including lib/plpgsql.dll).
The historical temporary runtime is incomplete and must not be used for restore.
This reduced local runtime lacks UTC/Europe-Moscow timezone aliases; use a fixed
+00:00 offset for canonical backup hashes. Linux needs a complete PG distribution.

1. Check it is really down: port 55461 must not be listening and no
   `postgres.exe` holds it (`netstat -ano -p tcp | findstr :55461`).
2. If `postmaster.pid` exists and its PID is dead (Windows killed the
   postmaster at shutdown): PRESERVE it first — copy to
   `postmaster.pid.original.<timestamp>` — then remove the original.
   Never clear pid/lock files silently; never initdb or overwrite PGDATA.
3. Start (PowerShell, from any directory):

```bash
powershell -NoProfile -Command "& 'D:\tools\pg17-runtime\bin\pg_ctl.exe' start -D 'C:/Users/man503/.codex/visualizations/2026/10/19/01a153ff-dc7c-77b3-a6be-d05f81a86e5c/rc-pg-20261019' -l '<PGDATA>/restart-<date>.log' -w -t 60"
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

**Owner decision 2026-11-05: variant A** — 5 s with the signed robots
exception. The prepared successor window and its exact launch command live in
`../../reports/RC_INTERVESP_5S_ROBOTS_EXCEPTION_20261105/` (launch ONLY the
recorded SHA, via `INTERVESP_5S_START_AFTER_APPROVAL.txt`). The re-sign path
(commit 765ac15) permits changing a pacing policy ONLY for sources proven
NOT_REQUESTED (source-local robots stop, zero product dispatches); every
REUSE_TERMINAL source stays byte-frozen; the robots-exception engine change
stays pinned to commit 1ba4064.

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
