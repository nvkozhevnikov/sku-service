# PROJECT_INDEX — where things are (2026-10-30)

Top-level operator documents: CURRENT_STATE.md (live status),
ARCHITECTURE.md (map), RUNBOOK.md (procedures), ACCEPTANCE_MATRIX.md
(acceptance terms), QUALITY_GATES.md (gates), WORKING_RULES.md,
ANTIPATTERNS.md, CANONICAL_DATA_MODEL.md, ER_DIAGRAM.md,
POSTGRESQL_SCHEMA.md, UPDATE_STATE_MACHINE.md, FALSE_MATCH_CASES.md,
DATA_RETENTION_POLICY.md, INTEGRATION_STATUS.md,
ARCHITECTURE_DECISIONS.md.

## Release continuation 2026-11-16

- [Native canary execution](docs/unified-rc/CANARY_EXECUTION.md): strict20+20 on
  existing queue/worker/receipts, waiting exact LIVE confirmation; old1270 unchanged.
- [Content Studio integration remainder](docs/unified-rc/CONTENT_STUDIO_INTEGRATION_REMAINDER.md):
  actual auth/storage boundaries, migration/security acceptance; not implemented SSO.

- [Release accounts, applications boundary and Linux runbook](docs/unified-rc/RELEASE_20261116.md).
- compose.release.yml / Dockerfile.server / requirements.lock: host PostgreSQL,
  no DB service; Dockerfile.release-qa / requirements-qa.lock: isolated QA only.
- migration019: additive application profiles and mandatory initial password change.

## Code

- `universal_supplier/control_plane/` — panel, queue, scheduler,
  orchestrator, automation sessions, source runtime, host fetch, rate
  control, recovery/continuation, exports plumbing.
  - `source_runtime.py` — leased source execution; robots guard; signed
    robots exception (`ROBOTS_EXCEPTION_KIND`, `signed_robots_exception`).
  - `host_fetch.py` — bounded per-host fetcher; policy digest approval.
  - `rate_control.py` — DomainRatePolicy/Limiter, breaker, Retry-After.
  - `automation_execution.py` — approval load/validate (pins HEAD/engine/
    rate/window); robots-exception contract validation.
  - `commercial_continuation.py` / `commercial_refresh.py` — receipt reuse
    and window re-authorization.
- `universal_supplier/` root — adapters (11 sources), http_capture,
  matching integration, xml/yml exports, commercial persistence.
- `sterbrust_matching/` — identity/normalization library (F01/F03).
- `competitor_intel/` — competitor monitoring/news/promotions runner.
- `scripts/` — one-shot operator entry points:
  - `start_supplier_system_once.ps1` — approved one-shot launch host
    (`-Approval/-Sha256/-ConfirmSha/-Start`).
  - `rc_runtime.py` — permanent local UI + competitor runner.
  - `rc_prepare_database.py` — RC cluster/DB preparation (55461 constants).
  - `build_local_rc_db.py`, `start_local_rc_postgres.ps1` — rc-local 55449.
  - `enrich_intervesp_details.py` — HISTORICAL local full-enrichment runner
    (55449 system, 20 s floor; superseded by the control-plane runtime).

## Tests

`tests/` — offline only (fake network/SQL). Key files for this change:
`test_robots_policy_exception.py` (signed exception: allow/stop matrix +
approval contract), `test_source_runtime.py` (nine parsers, robots stop,
resume), `test_host_fetch.py` (policy digest, limiter), 
`test_automation_execution.py` (grant load/session), 
`test_supplier_network_isolation.py`, `offline_host_profiles.py`
(synthetic 20 s offline profile).

## Evidence and reports

- `reports/source-runner-evidence/<source>/job-<id>/` — immutable per-job
  evidence (input/checkpoint/result/captures/responses/FIRST_STOP).
- `reports/automation-runtime/<uuid>/` — session lifecycle.
- `reports/automation-launch/<guid>/` — one-shot host state (HOST.json).
- `reports/integration/` — DB preparation artifacts, runtime credentials
  holder (`private-main-supplier-runtime.json` — local secrets, never push).
- `reports/xml-previews/`, `reports/qa-*.xml` — export previews, QA junit.
- Approval documents are operator-held OUTSIDE the repo; referenced by SHA.

## Database

`127.0.0.1:55461/rc_verify_vi_20261019` (see RUNBOOK §1). Key tables:
`suppliers`, `source_products`, `offers`, `supplier_http_captures`,
`offer_commercial_observations`, `product_matches`, `crawl_jobs`,
`crawl_runs`, `app_users`, `audit_events`.

## Known local-only gaps in a fresh worktree

`reports/LTT_USS_INTEGRATION_2026-10-13/...` evidence files referenced by
`tests/test_uss_terminal_batch.py` exist only in the main checkout; that
test file errors at collection in a fresh worktree (pre-existing, unrelated
to runtime code).
