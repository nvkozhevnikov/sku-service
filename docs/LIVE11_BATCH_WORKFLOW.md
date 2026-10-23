# LIVE11: пакет поверх существующей очереди

OPERATOR-CONFIRMED: code/offline only, STOP перед LIVE. Этот документ не разрешает
enqueue, HTTP, SQL, enable, policy activation, role/ACL changes, cron или production.

REPOSITORY-VERIFIED: прежние PostgresJobQueue, Orchestrator (default два slots),
PostgresDomainLease, SourceCheckpoint, SourceBackend и scoped matching переиспользованы.
Пробел на parent34739cb: не было batch identity, атомарного создания source jobs,
перехода R→D и общего отчёта. Новый scheduler/таблицы/миграции не добавлены.

## Подготовка и управление после отдельного допуска

Один будущий запуск охватывает 22 фазы: REFRESH и DISCOVERY каждого из11.
`config/live11_approval_matrix.csv` — byte-identical копия canonical LIVE11 CSV,
SHA256 `4313bce92e0d448066d5b3b12277aa37f5cded1016503e53cd41b4f937051195`.
Никакие новые caps/profiles/seeds не выводятся из defaults. Изменение CSV требует
нового кода/пакета и согласования; runtime не принимает произвольную таблицу.

Исполняемый entry point: `python scripts/source_batch.py`:

- `matrix`: только offline CSV, без подключения.
- `preview`: READ ONLY exact target и current known manifests; возвращает package
  и confirmation_sha256. REFRESH выбирает весь unique active/stable scope, отказывает
  при превышении ceiling. DISCOVERY замораживает seeds, cap, pages, job time.
- `enqueue --approval approval.json`: **будущий SQL WRITE**, только после разрешения.
  JSON содержит confirmation_sha256, batch_id (UUID), operator, approved_at и
  started_at (исходный T0 первой операции пакета, ISO UTC с offset), head.
  Enqueue не перезапускает96h от своего времени. Очередь повторяет preview под locks;11 initial R jobs создаются
  одной транзакцией или ни одного. D snapshot уже сохранён в соответствующем R.
- `status --batch-id UUID`: READ ONLY отчёт всех22 фаз, включая ещё не созданные D.
- `confirm-beka --batch-id UUID --receipt FILE --sha256 SHA`: **будущий audit WRITE**.
  После успешных Beka R+D оператор подтверждает существующий isolation gate пакета
  сохранённым receipt; до него остальные10источников не выдаются. Это контроль
  terminal evidence внутри общего разрешения, не новый допуск после5карточек.
  [Receipt contract](LIVE11_BEKA_GATE_RECEIPT.md); факт подтверждения имеет класс
  OPERATOR-CONFIRMED, сам JSON не объявляется LIVE-VERIFIED проверкой.
- `stop --batch-id UUID`: **будущий WRITE**, queued→cancelled, running→existing STOP.
  Даже если running успел завершиться успешно, следующий D не создаётся после STOP.
- `resume --batch-id UUID`: **явное действие оператора**, не автоматический retry.
  Только latest terminal phase с resume_available и без UNCERTAIN; прежняя exact SQL
  reconciliation обязательна. Не более2children/phase. Указанные неподходящие фазы
  возвращают OPERATOR_DECISION_REQUIRED. Новые REFRESH jobs не заменяют RESUME.

Запускается прежний Orchestrator; нового фонового процесса пакет не требует.
Claim сериализован PostgreSQL advisory transaction lock между worker processes:
не более2running jobs, разные host; прежний session host lease остаётся обязательным.
Сначала весь Beka Mak block R→D + isolation receipt, затем остальные host в порядке
TR→Vekprom→KAMI→Intervesp→LTT SIB→Partner→Optimum→LTT YUG→LTT MSK→USS.
При ошибке Beka gate остальные
остаются в очереди; автоматического обхода gate нет. Failed/partial не запускают D.
Успешное завершение R и enqueue frozen D составляют одну транзакцию с running CAS.
Повтор complete не создаёт второй D; сбой вставки откатывает completion целиком.

## Preflight и отдельное решение о setup

Сейчас ничего не поставлено в очередь и не включено. Preview/enqueue проверяют
current_user=rc_owner и rolsuper=false, без waiver/bypass. Также требуют
current enabled=true у всех11 и exact matrix rate_settings/approval reference;
это fail-closed prerequisite, **не скрытый enabled override**. Установка config и
enable/restore не входят в batch API. Используется прежний guarded preflight,
backup/baseline и явный операторский допуск. Устаревшие historical IDs не применяются.

**Отличие от draft21октября необходимо согласовать до запуска:** исходный текст
предлагал enable только отдельного source block. Атомарное создание11roots требует
enabled11 на момент enqueue; draft нельзя считать автоматически совместимым.
Подготовленный addendum в рабочем отчёте явно предлагает один approved setup всех11
и restore после terminal пакета. Пока оператор его не принял, LIVE остаётся BLOCKED.
Без такого решения код не включает disabled источники и не активирует политики.

Исторический B0 SQL PASS не повторять. Actual rc_owner superuser=true и failed
Option A preflight остаются отдельным нерешённым препятствием. Не создавать новую
роль, не менять ACL и не отключать DB guards в рамках подготовки пакета.

## Границы и отчёт для будущего UX

Scope digest защищает cap/entries/seeds/pages/time/policy/target/engine; проверяется
перед claim и runtime. RESUME сохраняет phase scope и исходный input, batch ID и
deadline. Scope не расширяется ни REFRESH, ни DISCOVERY, ни обычным RESUME.
Window96h; выдача просроченных jobs отменяется, runtime проверяет deadline перед
HTTP/SQL/matching через existing STOP callback. In-flight HTTP ограничен timeout.
Whole-job hard kill SQL/matching не добавлен: запас на tail/cleanup остаётся
условием execution plan. Новый HEAD не мигрирует старый checkpoint автоматически.

Payload `source_batch_status()` / CLI `status` предназначен для UI Grok без нового
дизайна: source/phase/job/attempts, шесть русских состояний, processed/remaining,
SQL COMMITTED receipts, HTTP и matching по попыткам, sql_counts с new_products,
изменения price/old_price/currency и availability/quantity по material history,
REVIEW/CONFLICT/NEW в scoped matching summary, incomplete discovery и operator action.
Unknown остаётся null. Cumulative receipt totals RESUME не суммируются; расходы
и изменения отдельных попыток представлены отдельно. Изменения — события истории,
не обещание числа уникальных изменённых товаров и не net diff всего пакета.
Полный traversal не доказывает полноту внешнего каталога; external coverage NOT_PROVEN.

Дорожная карта: единый пакет → отдельно разрешённый fresh11 → receipts/coverage →
адресный checkpoint RESUME/решения REVIEW/CONFLICT → проверка качества → handoff
разработчику. Stage6 import contract BLOCKED; Stage7/ESOL/production не открываются.


## Runtime-role authorization applied — 2026-10-22 (supersedes historical role blocker)

OPERATOR-CONFIRMED separate rc_runtime + necessary ACL/test runtime settings on
127.0.0.1:55461 / rc_verify_vi_20261019 only. LIVE-VERIFIED(local test SQL):
existing PGDATA/system7698346631476390904/17.11 preserved; rc_owner OID10 unchanged.
rc_runtime LOGIN/NOSUPERUSER/NOCREATEDB/NOCREATEROLE/NOREPLICATION/NOBYPASSRLS;
CONNECT target, public USAGE, reviewed per-table operations, only required sequence
USAGE. pg_read_all_settings is required by exact data_directory identity guard;
pg_control_system() EXECUTE was already available, no redundant function grant.
Capture/observation/audit UPDATE/DELETE and registry-import/admin table writes
remain denied. No schema CREATE, ownership, default ACL or other database grants.
Existing PUBLIC CONNECT is unchanged; target-cluster HBA rules specifically admit
rc_runtime only to targetDB from127.0.0.1 and reject its other database logins.
Previous HBA rules remain byte-identical below this role-specific prefix.

Cabinet adapters use fixed target + rc_runtime SCRAM credential from ignored
reports/integration/private-live11-runtime.json. They never inherit legacy DB_*
settings. Never print or commit that credential. Backend batch guards accept only
rc_runtime/non-superuser and retain exact PGDATA/system/version/host/DB guards.
Unknown roles, bootstrap rc_owner and superusers remain rejected. Existing serving
panel58097 delegates to these adapters; commercial-qa55449 stays separate.

Fresh pre-DDL backup full decode PASS; 1282 mappings/Product IDs unchanged.
Existing Orchestrator constructor + queue +11 registered collectors PASS;
no tick/claim/recover/heartbeat/daemon started. This is readiness preflight, not
worker liveness or LIVE proof. No HTTP, enqueue, business DML or policy activation.
Focused regression and exact-head approval matrix are recorded in workspace
reports/RC_RUNTIME_ROLE_20261022/REPORT.md and LIVE11_APPROVAL_MATRIX.csv.

LIVE remains BLOCKED pending one explicit execution/setup authorization:
9 disabled namespaces + exact11 matrix policies/digests/budgets with backup/restore;
existing runtime HTTP/SQL/session admission; fresh frozen preview/digest/current
HEAD/T0 approval; existing worker max2 different hosts; Beka isolation gate.
No old checkpoint migration to changed HEAD; UNCERTAIN requires operator decision.


## DISCOVERY matching recovery after committed ingest (2026-10-23)

The existing proof-aware RESUME path also admits the exact saved Beka DISCOVERY
job39/run20,104COMMITTED, only with a new exact hash-confirmed continuation
manifest. It binds the parent frozen snapshot, both product/node checkpoints,
finished discovery frontier, all SQL receipts, current matching/identity/REVIEW
rows, manual/rejection hashes and the original1282 mappings. Original partial
job metadata is evidence and is not edited to fabricate success. A technical
child reuses the durable matching result, performs native SQL reconciliation,
and completes through the normal queue path: HTTP0,ingest0,rematching0.

The same manifest binds cancelled never-started attempt2 roots29–38. The existing
queue may create their final attempt3 with unchanged scopes and original batch ID,
T0/deadline. It does not permit attempt4, a started cancellation, scope drift or
unknown parents. Parent rows stay unchanged; retry insertion remains atomic.
The Beka isolation confirmation and exact HEAD/engine/approval SHA guards remain
mandatory. A code change does not itself approve its new compatibility manifest.

Execution continues through `scripts/source_batch.py recover --approval <new file>
--sha256 <exact SHA> --confirm <operator-confirmed SHA> --run
--activate-approved-settings`. Do not reuse RESUME_APPROVED.ps1 from the first
recovery. Existing session restores temporary settings after worker shutdown.
The actual104fixture and tests are tests/fixtures/live11_recovery/
beka_job39_run20.json.gz and tests/test_source_discovery_recovery.py. Offline
replay is not fresh LIVE collection or proof of a complete external catalog.


## One bounded post-stop exception (operator authorization2026-10-23)

The general three-attempt rule is retained. A separately hash-confirmed
`post_stop` manifest may authorize exactly one child/retry for terminal parents
41–50 (attempt3→4), preserving the original22scopes/batch/T0/deadline and all
profiles. A fifth attempt, another parent or changed scope still fails closed.
Never-started cancelled parents get a frozen REFRESH retry in the same queue;
partial41/42/43 use its normal RESUME/reconciliation path. No parent status or
checkpoint is rewritten to claim completion.

The reviewed parser delta is pinned to exact file SHA, not an open-ended parser
allowlist. The known613vt FAILED receipt can supply an offline input only when
its original200capture, hash, input identity and full failed history match the
approved manifest. It is never returned to HTTP frontier. Existing SQL probing
checks all COMMITTED and detects lost commits using the frozen external ID,
including captures archived before parsing supplied that ID. Only normal
persistence writes COMMITTED after readback. All other FAILED/UNCERTAIN stop.

Run22resume_uncertain includes the known failed613vt parse because the old
terminal error omitted capture metadata; it does not signify lost transport.
Only this proof-bound URL can be reconciled; the array is not ignored globally.
Original58TRmatching receipts are reused, pending105Vekprom matching remains
pending until actually executed. Active settings are restored by the same
existing session context manager. A new exact HEAD/engine/manifest SHA must be
confirmed before execution; operator permission to implement is not that SHA.


## Per-URL isolation and bounded continuation (2026-10-23)

Operator-authorized implementation over the existing runtime/checkpoint/queue.
A pre-ingest parser/identity failure appends QUARANTINED with original capture,
fingerprint, category and reason. No model is guessed; no SQL ingest or matching
occurs for that URL. A non-200 HTTP response uses separate archived transport
evidence: never construct a fake HTTP200 ProductCard/HttpCapture. Approved host
retry policies are unchanged; 403/429, interstitial, Retry-After and unknown
transport retain global STOP. Consecutive card failures use the existing host
consecutive_error_threshold. SQL exceptions are outside the extraction catch.

Quarantine remains in the frozen scope and checkpoint history. RESUME probes
SQL and validates evidence; missing evidence or unexpected committed SQL is
UNCERTAIN. Matching uses only SQL COMMITTED without durable matching receipts.
The initial SQL safety gate is still required for eligible cards; quarantined
cards never count as successful SQL proof. HTTP/SQL/matching/quality totals stay
separate. Native execution may finish with quarantine, but collection_complete
is false, report says "завершено с карантином", and operator action remains visible.
Normal R→D advancement remains the queue completion transaction.

`prepare-continuation --previous FILE --previous-sha256 SHA --quarantine-url URL
--output FILE` is SELECT-only. It freezes exact current parents, checkpoints,
source hashes, manual locks and1282mappings, all22original scopes, time/host/rate
budgets. Each phase gets no more than four additional attempts, never above8.
This is an explicit separately approved exception, not a new default retry rule.
The ordinary three-attempt guard and old exact post-stop approval remain intact.
Known BMDO120/130 historic FAILED can become quarantine only with the signed
failed row and archived200capture/history; no manual status edits are used.
Unknown FAILED/UNCERTAIN are not silently classified as parser failures.

The existing recover entry point and session manage continuation. It permits
native RESUME of approved initial partial jobs; subsequent automatic continuation
is limited to safe time/page/product-budget boundaries and the signed ceiling.
Systemic errors still stop the batch. Existing settings activation/restore and
max2different-host leases remain mandatory. New HEAD/engine/approval SHA requires
explicit operator confirmation. Permission to implement is not that confirmation.
Do not run old RESUME_APPROVED.ps1 or an already consumed post-stop command.

After reboot: inspect actual target PG17.11/system7698346631476390904/55461,
original checkpoint/SQL receipts/active leases/settings before a confirmed recover.
Never initdb, overwrite PGDATA, switch cluster, or silently clear writer/pid locks.
Check settings restored and0active after session shutdown; preserve all journals.
Saved actual BMDO and59COMMITTED checkpoint fixtures are offline evidence only.


## Semantic quarantine is not a consecutive structural error — 2026-10-23

LIVE session37491ed0… demonstrated a classification defect: the three captured
BMSO C-NC/CS, GLS-NC/GLS and GS-NC/GS contradictions after59COMMITTED were counted
as consecutive source/parser failures. The native safety STOP and settings restore
worked; nothing was force-resumed. The4semantic quarantines among63attempted cards
are valid evidence, not permission to select a model or strip NC.

Correction: semantic identity quarantines do not increment the structural/HTTP
error counter. Structural parse failures retain the source consecutive threshold;
HTTP breaker/403/429/Retry-After remain unchanged. A semantic source-quality STOP
requires at least safety_gate_size identity quarantines AND at least half of all
actually attempted cards in that phase (including its earlier COMMITTED). This
is a source-quality check, never a product processing cap. Quarantine still does
not count as SQL proof, does not enter matching, and never implies full coverage.
The actual59good+4quarantine replay must continue without SOURCE_CARD_ERROR_THRESHOLD;
a source dominated by identity failures must still STOP. F01/F03/parser/identity
rules are unchanged; all three real followup captures remain rejected by parser.

After a technical STOP, prepare-continuation accepts the previous signed
continuation as its proof-chain input. It resnapshots current native parent/job/
checkpoint/SQL evidence, preserving or reducing the previously signed phase
attempt ceilings, original22scopes/T0/deadline/matrix/host/rate. It cannot replenish
attempts. New exact HEAD/engine/manifest SHA confirmation remains mandatory.


## Proven semantic update rejection — 2026-10-23

Session3a57fc4b… reached422COMMITTED/319unique,3native phases succeeded,19TR
quarantines; then KAMI variant:9833201 / source7188 / protected ProductID146135
triggered SOURCE_UPDATE_SEMANTIC_CONFLICT:EXISTING_LINK_CONFLICT. Current captured
G5-42Y standard conflicts with its retained catalogue family G5-Y/G5-42Y. The
source_update guard and all1282mappings remain unchanged. Native transaction
rolled back, exact capture/observation absent; no model/target override allowed.

SourceBackend now exposes only the unique native SOURCE_UPDATE_SEMANTIC_CONFLICT:
rejection as an isolatable matching quarantine AFTER TransactionStatus.IDLE,
exact SQL capture NOT_FOUND, and one preserved mapped source/target readback.
Non-idle rollback, UNKNOWN/COMMITTED receipt, missing mapping, permissions,
URL/namespace/target/mapping errors remain global STOP. Matching rules/F01/F03
and source_update.py are unchanged. No alternative writer/ingest path exists.
The leased runtime appends QUARANTINED with rejection proof and mapping evidence;
no durable ingest, canonical rewrite or AUTO occurs for the rejected card.
Matching quarantines are not source-layout failures and do not dilute SQL proof.
Incomplete/invalid matching rejection proof classifies UNCERTAIN and blocks
completion/automatic RESUME; final reconciliation cannot advance an uncertainphase.
Existing accepted IDs/manual locks remain operator authority; quarantine requires
REVIEW before any correction of that mapping. Native matching only includes
actually COMMITTED cards. Old historical buckets are not fresh matching.

Real captured KAMI regression fixture retains bytes/hash/IDs; offline tests cover
rollback/status/receipt/mapping failures, unknown SQL and preservation of146135.
After new exact HEAD/SHA confirmation the same batch resumes from current owners,
without re-GET of COMMITTED or the saved KAMI/Vek captures. Prior attempt ceilings
remain R8/D4; proof renewal never replenishes attempts, T0/deadline or scopes.
# 2026-10-23: explicit operator scope quarantine

`prepare-continuation --scope-quarantine-decision <json>` is SELECT-only. It can
bind one explicit operator decision to an exact historic `UNCERTAIN` caused by
`CANARY_URL_OUTSIDE_FROZEN_BATCH`: original URL/entry, owner, job/run, checkpoint,
source-row hash, attempt start and authorization-file hash. No HTTP response is
invented. Capture/observation/history absence must pass both preparation and
native admission/readback; source edits or any SQL ambiguity remain STOP.

Only a newly confirmed exact HEAD/SHA can execute the proposal. The leased
runtime appends `operator_scope` quarantine through existing SourceCheckpoint,
retaining UNCERTAIN history and excluding it from HTTP/ingest/matching. Other
UNCERTAIN states are unchanged. Subsequent phases cannot fetch the same blocked
URL: encountering it trips `OPERATOR_SCOPE_QUARANTINE_NO_HTTP`, requiring a
separate decision rather than silently following a redirect or shrinking scope.
Signed attempt ceilings, original T0/deadline and all identity/SQL guards remain.

## 2026-10-23 — operator-authorized unified refusal policy / R9

The subsequent operator decision authorizes one policy for original frozen
REFRESH URLs and exactly one additional attempt for parents92–100 (R8→R9;
DISCOVERY remainsD4). `--scope-policy-authorization` adds the operator artifact
hash and per-entry source-row hashes to the existing continuation manifest.
Unknown roles/targets, changed rows, SQL ambiguity, cross-host/unsafe targets,
unsigned policy/HEAD, and further attempt10 remain STOP. No attempt is replenished.

A structured scope refusal never dispatches its blocked target. The existing
leased runtime first records UNCERTAIN, verifies exact job/run/original input
and SQL absence, then appends operator quarantine with immutable refusal evidence.
No HTTP200/capture/ingest/matching result is invented. DISCOVERY reuses a proven
REFRESH refusal through the same checkpoint, without another detail GET or a
fake new transport. All original URLs stay accounted for as quarantine; they
do not count as COMMITTED or complete clean catalogue coverage.
