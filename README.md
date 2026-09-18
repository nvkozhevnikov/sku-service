# Universal Supplier Stage 6E

Stage 6E adds authenticated CSV/XLSX exports, live data-quality drilldowns, deterministic anomaly and missing-product guards, product history/provenance/freshness structures, single-product parser inspection, persistent business rules, a typed canonical property registry, supplier/category-aware mappings, unit normalization, category filter profiles, field ownership and a read-only Sterbrust change preview. Stage 6E.1 wires these features through the normal PostgreSQL runtime, adds true one-product reparse and closes Stage 6E without enabling imports.

Safety remains unchanged: no Sterbrust/ESOL mutation or preview-apply route exists. Parent Stage 6 remains `BLOCKED_IMPORT_CONTRACT_EVIDENCE`; Stage 6F and Stage 7 are not started.

Internal web control plane, persistent PostgreSQL job queue, timezone-aware supplier scheduler and crawl orchestrator. It never writes to Sterbrust.

> STERBRUST IMPORT DISABLED — IMPORT CONTRACT NOT PROVEN

## Start on Windows

1. Copy `.env.example` to `.env` and replace every `CHANGE_ME` value. Keep `STAGE3B_DB_CONFIRM=NO` and `STAGE3D_DB_CONFIRM=NO`; these are unrelated historical gates.
2. Start Docker Desktop.
3. From this directory run `docker compose up -d postgres web orchestrator`.
4. Open `http://127.0.0.1:8080/`. Health endpoints are `/health` and `/ready`.

View service state with `docker compose ps` and logs with `docker compose logs -f web orchestrator`. Stop with `docker compose down` (data remains in `universal_supplier_pgdata`).

## Scheduler

All persisted schedules start disabled and use UTC unless an operator explicitly saves another IANA timezone. Set `SCHEDULER_ENABLED=NO` in `.env` and restart `orchestrator` to suspend cron evaluation while retaining manual queue processing. “Start now” only enqueues; the separate orchestrator claims the job.

Different supplier domains may run in parallel up to `MAX_PARALLEL_SUPPLIER_JOBS` (default 2, valid range 1–32). A normalized-domain PostgreSQL advisory lock prevents concurrent crawls against the same domain. Partner-ST and Optimum currently advertise only the `full` crawl type; unsupported types fail before adapter or network execution.

The immutable policy captured when a job is enqueued reaches the actual Partner `HttpTransport` or Optimum `HttpFetcher`. Concurrency, request interval, jitter, timeout, attempts, bounded backoff, Retry-After behavior, circuit breaker and monotonic maximum-run duration are all effective and represented in run metadata. A deadline produces `MAX_RUN_DURATION_EXCEEDED`, partial status and no missing-product deactivation.

## Database and tests

Services run `scripts/docker_postgres_migrate.py` before startup. Migrations 001–014 are immutable; Stage 6E.1 requires no migration 015. To test without live crawling:

`docker compose --profile tools run --rm application`

The test suite uses fixtures/fakes only. The optional local screenshot/demo mode is read-only:

`$env:CONTROL_PLANE_DEMO_SNAPSHOT='YES'; python -m uvicorn universal_supplier.control_plane.web:app --host 127.0.0.1 --port 8080`

Stage 7 remains out of scope. No importer, product-creation endpoint, webhook, cron write outside this application, or Sterbrust credential is included.

Correction evidence is in `reports/STAGE6C_CORRECTION_QA.md`; Windows host-port verification is reproducible with `scripts/verify_stage6c1_host_web.ps1` against the QA compose override.

## Stage 6D operator administration and review

Stage 6D adds a Russian-language operator interface, authenticated sessions, `ADMIN` / `OPERATOR` / `VIEWER` roles, an explainable review queue, manual review decisions, candidate rejection memory, append-only audit events and queue-only manual crawl requests.

Create the first administrator after setting the database environment variables:

`python scripts/create_admin.py --username admin`

Generate or refresh review cases from persisted product and match data without crawling suppliers:

`python scripts/generate_review_cases.py`

Only Partner-ST and Optimum are created by `scripts/ensure_real_suppliers.py`; it performs no network request. The web service remains an internal control plane. A manual crawl action only creates a PostgreSQL queue record. The separate orchestrator is the only component that can execute a queued crawl, and it is not required for interface or review QA.

The Stage 6D evidence set is listed in `reports/STAGE6D_SUMMARY.txt`. Stage 7 remains prohibited and Sterbrust import remains blocked until the external import contract is proven.

## Stage 6D.1 runtime identity and operator corrections

Every successful Partner-ST or Optimum ingest now runs the same seven-class identity classifier used by the packaged Stage 6B reports, persists structured evidence and candidates, then synchronizes review cases. Post-processing failure is recorded separately and does not invalidate already committed supplier data.

Manual mappings are protected. A resolved case reopens only when identity-critical source fields change; price, stock and other commercial changes do not reopen it. Rejected candidates remain excluded. The main operator explanation is generated in Russian from structured evidence, while raw reason codes remain only in technical details.

The scheduler defaults to basic daily/weekday/custom controls with time and timezone; raw cron is an explicit advanced mode. Configure internal hostnames through `CONTROL_PLANE_ALLOWED_HOSTS` as a comma-separated allow-list. Trusted-host, origin/referer, session, role, CSRF and audit protections remain active for configured future internal hosts.

Corrective evidence is in the ten `reports/STAGE6D1_*` files. Packaging stops at Stage 6D.1 and contains no credentials, live crawl output, database dump, importer, or Sterbrust write path.

## Stage 6D.2 corrective closure

Stage 6D.2 makes post-processing failures truthful at both job and crawl-run level, completes Russian operator labels, adds direct proposed-candidate rejection, fixes next-review navigation, and exposes real last-run details on supplier cards. The product detail relation query uses the schema's `raw_metadata` column.

For a fresh isolated runtime check, create the QA database, apply migrations, and run:

`docker compose -p stage6d2qa -f docker-compose.yml -f docker-compose.stage6d2-qa.yml --profile tools run --rm application scripts/docker_postgres_migrate.py`

`docker compose -p stage6d2qa -f docker-compose.yml -f docker-compose.stage6d2-qa.yml --profile tools run --rm -e STAGE6D2_QA_ADMIN_PASSWORD application scripts/qa_stage6d2_runtime.py`

The parity command is fully offline:

`docker compose -p stage6d2qa -f docker-compose.yml -f docker-compose.stage6d2-qa.yml --profile tools run --rm application scripts/qa_stage6d2_identity_parity.py`

Stage 6D.2 evidence is in exactly six `reports/STAGE6D2_*` report files plus updated screenshots. Stage 7 is not started; the import contract remains `BLOCKED_IMPORT_CONTRACT_EVIDENCE`.
