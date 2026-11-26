# Unified RC architecture — 2026-10-30

## All11 model discovery boundary — 16Nov

REPOSITORY-VERIFIED: RegistryContext adds by_title_model to the existing CSV/SQL
loaders. Strong article/model retrieval is unioned; missing model comparison can
use a visible OWN_MODEL title projection without changing database/raw fields.
Same F01/F03 match_product is the positive authority. Native automatic planner adds
only a negative generic-model guard, retaining hard CONFLICT/manual ID protection.
No second matching engine or schema, supplier namespace merge or new ingestion.
[Scope and offline proof](docs/unified-rc/ALL11_MATCHING_COVERAGE.md).

16Nov native canary: a purpose-specific policy in existing commercial_refresh and
automation_execution; same queue/Orchestrator/SourceBackend/receipts, no second engine.
Native source updates transactionally preserve signed source PK/catalog link; matching
stays dry-run. Details: docs/unified-rc/CANARY_EXECUTION.md. Studio integration boundary
and exact storage/auth remainder are documented separately; no SQLite/SSO migration.

2026-11-16 REPOSITORY-VERIFIED delta: migration019 extends existing app_users with
access profiles and mandatory password change; application roles do not create SQL
roles. Release Compose contains app/explicit workers only; PostgreSQL17.11 remains
a separate host service55461. Confirmed hash-pinned deployment contract replaces
machine-specific system ID/PGDATA only; host/port/database/version remain fixed.
Content Studio is a separate SQLite/DPAPI application without a verified shared-auth
contract; /applications is an honest integration boundary, not SSO. Details:
docs/unified-rc/RELEASE_20261116.md. No production deployment performed.

One repository, one PostgreSQL, one panel. This file is the map; exact
operational commands live in RUNBOOK.md, acceptance terms in
ACCEPTANCE_MATRIX.md, gate definitions in QUALITY_GATES.md.

## Runtime topology

- **PostgreSQL**: `127.0.0.1:55461/rc_verify_vi_20261019`, PG 17.11,
  system identifier 7698346631476390904. PGDATA (local Windows cluster,
  started by pg_ctl, no service): see RUNBOOK.md. Writer role `rc_runtime`
  plus separate per-surface roles (`app_supplier`, `app_dispatch`,
  `competitor_runtime`); connections re-verify identity/role on every connect.
- **Control plane** (`universal_supplier/control_plane/`): FastAPI panel
  (`web.py`), Postgres job queue (`queue.py`), Scheduler (`scheduler.py`),
  Orchestrator (`orchestrator.py`), bounded automation sessions
  (`automation_execution.py`, `supplier_automation.py`).
- **Source runtime** (`source_runtime.py`): leased per-host execution of the
  11 configured sources; REFRESH (frozen URL list) / DISCOVERY (node
  traversal) / RESUME (receipt reconciliation before any new GET). Per-job
  durable checkpoints, manifests, immutable capture archive under
  `reports/source-runner-evidence/<source>/job-<id>/`.
- **HTTP boundary** (`host_fetch.py`, `http_capture.py`): one start-time rate
  gate per domain (`rate_control.DomainRateLimiter`), circuit breaker,
  Retry-After respect, redirect/host/robots validation, TLS via verified
  public trust (`public_connection.py`).
- **Matching** (`sterbrust_matching/`, `automatic_matching.py`): F01/F03
  strict identity; AUTO only for proven exact identity, REVIEW for ambiguity,
  CONFLICT for proven contradiction, NEW staged separately. Existing mappings
  (1282) are never silently retargeted.
- **Exports** (`xml_export.py`, `supplier_neutral_xml.py`, `yml_feed.py`):
  local previews only, `import_allowed=false`; production import is manual.
- **Competitor intel** (`competitor_intel/`): monitoring, news, articles,
  promotions; separate runner, same database, own rate/robots handling.
- **Admin panel** (`control_plane/web.py`, `cabinet.py`): unified UI over
  supplier + competitor surfaces.

## Rate limiting and robots — the exact chain

1. `DomainRatePolicy` (rate_control.py) per host: `min_request_interval_seconds`
   (start-time gate, all workers), `max_concurrency`, attempts/backoff/cooldown,
   `max_run_duration_seconds`, `respect_retry_after`.
2. `compile_policy` (host_fetch.py) validates bounds and, for non-native
   sources, requires the exact host-policy digest to be approved
   (`approval` reference or `STERBRUST_APPROVED_HOST_POLICIES` env, set by
   the scheduler host from the approval's `host_policies`).
3. `robots()` (source_runtime.py) fetches robots.txt live before product GETs,
   parses `Crawl-delay`/`Request-rate` for agent `UniversalSupplierReadOnly/1.0`,
   and requires `declared minimum ≤ min_request_interval_seconds`.
   Violation → `ROBOTS_REQUIRES_SLOWER_PROFILE`, source-local STOP
   (job-170 evidence). Per-URL `can_fetch` violations → `ROBOTS_DISALLOW`.
4. **Signed robots exception** (2026-10-30): pacing below the declared robots
   minimum is possible ONLY through a `robots_policy_exception` block inside
   an operator-signed approval (`robots-policy-exception/v1`), bound to the
   exact robots.txt SHA256 and an `applied_min_request_interval_seconds`
   that the job profile may not exceed. Inert without an active approved
   session; any mismatch (changed robots bytes, different source, faster
   profile, unsigned block) keeps the default STOP. The applied exception is
   recorded in `result.json` as `robots_policy_exception_applied`.
5. Approval chain (`automation_execution.load` / `validate_job`):
   `supplier-schedule-grant/v1` pins HEAD, engine fingerprint, TLS identity,
   exact 11 sources, per-source `rate_sha256`/`settings_sha256`/caps,
   window ≤24 h, 1282 mappings, protected LIVE11 batch. New HEAD or changed
   engine invalidates the approval; a new window requires an explicit
   `execution_reauthorization` block (same-scope-commercial-window/v1).

## Evidence model

Immutable file evidence under `reports/`: per-job `input.json`,
`checkpoint.json`, `result.json`, `captures/`, `responses/`,
`transport-errors/`, `FIRST_STOP.json`; automation sessions under
`reports/automation-runtime/<uuid>/` (SESSION/heartbeat/hourly/FINAL/STOP).
Approval documents are operator-held outside the repository and referenced by
SHA256 everywhere.

## Recovery model

Native RESUME reconciles checkpoint terminal receipts against SQL before any
new GET; UNCERTAIN states never silently re-enter the HTTP queue. Commercial
continuation (`commercial_continuation.py`) reuses durable receipts and
admits new work only for URLs proven NOT_REQUESTED, with frozen inputs,
ancestor proof chains and explicit window re-authorization.
