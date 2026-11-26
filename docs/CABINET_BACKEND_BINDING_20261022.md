# Applied cabinet binding

2026-10-27 supplement: the historical record below describes the original serving
binding, not current runtime readiness. New feature retains these cabinet routes
inside the same app and adds settings/freshness/two previews. Runtime is rc_runtime
on55461; new feature/QA/serving boundaries and pending migration018 are documented in
[SUPPLIER_AUTOMATION.md](SUPPLIER_AUTOMATION.md). The frozen LIVE11/R13 checkout
and original batch scope/HEAD approval remain separate; no new LIVE was executed.

Serving application remains `../sku-service/scripts/run_local_rc_ui.py`, localhost
58097, existing authentication and templates. The sibling feature is not mounted
as a second application and never inherits the serving DB config.

`scripts/cabinet_batch_read.py` delegates preview/status to the existing
PostgresJobQueue and source_batch. The exact target remains55461 /
rc_verify_vi_20261019 / rc_owner. Read actions force read-only transactions and
bounded connect/statement timeouts; failures return safe reason codes.

Operator POSTs can delegate enqueue/stop/resume/confirm-beka only in a separately
admitted session (`STERBRUST_CABINET_LIVE11_WRITE_CONFIRM=rc_verify_vi_20261019`),
after existing login/role/Origin/CSRF. This flag is NOT enabled by this task.
Exact target identity/non-superuser role is checked again; enqueue uses the
unchanged atomic immutable-scope backend; receipt verification and safe RESUME
remain the existing functions. No policies, suppliers, roles or scheduler are
enabled. Current target is unavailable and the historical role prerequisite is
unresolved. No real batch or HTTP was started.

Grok's three UX files are preserved byte-for-byte and now tracked. Their old
34739cb/non-batch scope is EXTERNAL-SPEC historical; the operator's final
2026-10-22 implementation request authorizes the current bindings, not LIVE.
GLM baseline is PACKAGED-EVIDENCE T0, never a fresh collection.

Focused offline: adapter4 + existing batch34 =38PASS, including the saved-data
large22phase test. B0 SQL, Partner LIVE50 and matching regression not repeated.
Actual browser/routes/DB readback evidence and operator instructions are in the
workspace final report and sibling `docs/OPERATOR_CABINET_20261022.md`.

## Applied per-source remainder

The serving source detail now binds every namespace to existing REFRESH/DISCOVERY
preview+enqueue, source STOP and checkpoint/SQL-safe RESUME. The adapter extension
is `scripts/cabinet_source.py`, reached only through the same cabinet command
adapter. No queue/parser/checkpoint/backend changes. The source ID is selected
from exact55461, never from RC55449 or T0. Matrix source/cap/host-policy and phase
budgets bound the existing queue preview; digest is checked again under queue lock.
RESUME keeps the original parent, batch envelope and scope; UNCERTAIN is blocked.

Per-source SELECT status exposes job/run IDs, real metadata HTTP separately from
SQL receipts/counts/matching/material changes, errors and owner checkpoint paths.
The selected old job is not excluded by the latest50 history bound. STOP is the
existing running-job command; queued batch cancellation remains batch STOP.

Current real launch availability remains gated: exact55461 not listening; last
role evidence rc_owner bootstrap/SUPERUSER. No role, target, flags, host policies,
supplier enabled or worker state changed. The serving UI's three legacy manual
sources remain separate. Code wiring is complete for11; LIVE readiness is not.
