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
