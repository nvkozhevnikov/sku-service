# Commercial REFRESH: продолжение после фактических COMMITTED

## Продолжение после robots-only job169 — 29Oct

Первый29Oct manifest3deec0c... использован: job169/run60 partial, SOURCE_ROBOTS_UNAVAILABLE,
один TLS network attempt к robots, товарных HTTP/ingest0. Старый manifest не запускается
повторно. Без ручной смены статуса создаётся отдельный exact successor в прежнем окне.

receipt_proof проверяет цепочку подписанных previous approvals, scope/target/матрицу,
все frozen input/checkpoint/result hashes каждого предка. Семь REUSE_TERMINAL jobs
161,163–168 наследуются с их исходными engine/grant; latest Intervesp169 допускается
только при полном доказательстве NOT_REQUESTED всех320, пустом inflight/terminal,
отсутствии product requests, новых captures и SQL receipts. Изменённые файлы, scope,
чужой approval, UNCERTAIN или попытка товарного HTTP дают STOP.
Исходное окно27–28Oct сохраняется как evidence; successor требует явного exact
подтверждения окна29Oct09–13UTC, действующего HEAD/SHA и TLS bundle SHA.
597 COMMITTED и353 Q не становятся новыми заданиями. Это прежний коммерческий
operation_id/1270 targets, без LIVE11/R13, новых matching или schema/role изменений.

## Дополнение29Oct — интегрированный runtime и явно новый допуск

Этап1 Unified RC зафиксирован167d3cf поверх одобренной интеграции52f3ece.
Новый prepare --reuse-receipts может принять **оба** --not-before/--execution-until
как предложение нового ограниченного окна. Поля execution_reauthorization сохраняют
исходные27Oct13Z–28Oct13Z и точные новые даты/тот же scope SHA. Продление не выполняется
без нового exact HEAD/engine/file SHA подтверждения. Диапазон<=24ч, timezone обязателен,
новое окно не перекрывает прежнее. Старое разрешение никогда не становится новым.

transport_tls pin содержит версию и SHA256 установленного certifi, CERT_REQUIRED и
hostname=true; load и периодический session check отклоняют CA policy drift.
Compatibility принимает неизменённые runtime/store/competitor templates **только**
из exact интеграции52f3ece, существующую receipt-admission/reporting правку и public
TLS context. Parser/matching/SQL writer delta по-прежнему требует review.
Для historical receipts старый engine читается как evidence, а не исполняется.

Пример подготовки после согласования конкретных дат (SELECT only):

```powershell
& ..\sku-service\.venv-audit\Scripts\python.exe scripts\supplier_system.py prepare --reuse-receipts --continue-approval OLD_APPROVAL.json --previous-sha256 OLD_SHA --not-before EXACT_NEW_START --execution-until EXACT_NEW_END --output NEW_NOT_APPROVED.json
```

Полный1270 manifest/1215 Product IDs,597 COMMITTED/353 Q и320 NOT_REQUESTED,
operation_id, original T0/window/CP/history и max2/host/rate остаются. Активируется
только pending Intervesp; семь учтённых sources не запускаются. Старые Q не ingest.
Первый HTTP — robots внутри нового separately approved окна. Никакого LIVE11/R13.

Ниже описание28Oct является историей до этого explicit-window дополнения.

## Проверенные границы

LIVE-VERIFIED предыдущий запуск79dc/session4d83abab: полный manifest1270 URL,
597 COMMITTED,353 QUARANTINED,320 Intervesp NOT_REQUESTED. Это отдельный
commercial REFRESH, а не возобновление LIVE11/R13. Старая команда использована.
Исходное окно заканчивается28.10.2026 13:00UTC, dispatch STOP12:55UTC.
Код и offline QA не являются разрешением нового LIVE или продления окна.

## Реализованное продолжение

REPOSITORY-VERIFIED: существующий commercial_continuation принимает новый вид
доказательства `commercial-receipt-continuation/v1`. SELECT-only prepare сохраняет
все1270 targets, supplier policies/limits, operation_id, исходное окно и1282 mappings.
Не создаётся новая очередь, схема, scheduler, parser или matching engine.

Для каждого старого задания сверяются immutable input, engine, checksum checkpoint,
job/run lineage, file SHA и фактические capture bytes. Полностью учтённая succeeded
фаза переиспользуется только при отсутствии frontier/inflight/UNCERTAIN и при точных
COMMITTED receipts с успешным durable matching. Карантин требует сохранённого
HTTP200, причины, rollback/SQL-absence proof; он остаётся карантином, а не COMMITTED.
SQL сверяет точные fingerprint, source identity, URL, дату, response/evidence hash
и offer observation. Недостающий, лишний, повторный receipt или SQL capture карантина
даёт STOP. Повторно проверяются1282 baseline mappings, все physical mappings,
current matching hashes и старые job rows. Старые статусы/CP не редактируются.

Семь завершённых sources не активируются и не enqueue-ятся. Даже прямой preview,
snapshot admission или authorize_card запрещает их повторное исполнение. Только
полностью NOT_REQUESTED фаза с source-local robots failure получает новые frozen
inputs при новом exact approval; robots/TLS обязаны пройти до товарных GET.
Смешанные partial/cached/UNCERTAIN фазы этот узкий путь намеренно не принимает.
Они требуют штатного RESUME с отдельным доказательством, а не ослабления этого guard.

Hourly SELECT-отчёт включает прежние597/353 и их исходные даты; HTTP_reused и
HTTP_this_execution разделены. Старые commits не становятся новой скоростью или
новой свежестью. Сохраняются native hourly/STOP/CAS restore, max2 разных host,
rate/robots/SQL/identity guards и запрет импорта. Совместимость engine разрешает
только три модуля admission/reporting; parser/matching delta требует нового review.
Новый execution HEAD/engine/SHA всегда требует точного подтверждения оператора.

## Команды

Из этой feature, Python — существующий `..\sku-service\.venv-audit\Scripts\python.exe`.
Подготовка только SELECT, без HTTP/enqueue/ingest:

```powershell
& ..\sku-service\.venv-audit\Scripts\python.exe scripts\supplier_system.py prepare --reuse-receipts --continue-approval "..\..\reports\COMMERCIAL_SOURCE_ISOLATION_20261027\EXECUTION_20261028\TIMEOUT_FIXED_CONTINUATION_NOT_APPROVED.json" --previous-sha256 79dc99fc32881cafb7ab1c49ba386156f2ea93ad6d8612ddb7e44d98cc874c5a --output "NEW_NOT_APPROVED.json"
```

Параметры изменения scope/window для receipt reuse отвергаются. Если исходное окно
истекло, draft остаётся evidence, а run отвергается. Нужен явно утверждённый новый
execution window с сохранением исходного как evidence; этот commit его не выдаёт.

Независимый Windows-host проверяется без БД/HTTP/worker:

```powershell
.\scripts\start_supplier_system_once.ps1 -Probe
```

Default inspect и будущий запуск используют один и тот же существующий CLI:

```powershell
.\scripts\start_supplier_system_once.ps1 -Approval "NEW_NOT_APPROVED.json" -Sha256 EXACT_SHA
# Только после решения TLS, действующего окна и exact HEAD/SHA подтверждения:
.\scripts\start_supplier_system_once.ps1 -Approval "NEW_NOT_APPROVED.json" -Sha256 EXACT_SHA -ConfirmSha EXACT_SHA -Start
```

Start перед созданием процесса проверяет подтверждение, SHA, SELECT-preflight и окно.
Через local Windows CIM создаётся один скрытый исходный pythonw, без Windows Task,
cron, boot trigger или restart. Используются пакеты существующего venv. Его Windows
redirector не используется: его probe оказался внутри JobObject, тогда как probe
исходного interpreter — вне JobObject. Child сам проверяет
отсутствие JobObject и повторно проверяет exact HEAD/engine/SHA/window до worker.
При неудаче записывает HOST.json и останавливается. Job/security flags не меняются.
Пятиминутный drain, keep-awake, восстановление настроек и hourly остаются в run_approved.
PGCONNECT_TIMEOUT child явно5; пустое значение PowerShell не наследуется.

Создание процесса не равно успеху сбора: читать startup.jsonl → SESSION/heartbeat →
FINAL и SETTINGS_RESTORED. STOP — прежняя команда supplier_system.py stop --session.
При отключении питания native CP остаётся, автоматического restart нет.

## BLOCKED

320 Intervesp: подтверждён TLS verify_code19 (`self-signed certificate in certificate
chain`). Источник цепочки ещё не доказан. Не отключать TLS/robots и не устанавливать
непроверенный CA. Нужна отдельно разрешённая проверка цепочки и новый допуск перед HTTP.
353 semantic Q требуют REVIEW по сохранённым HTML; IDs не менялись.91 fresh offers
(88 Product ID) исключены бизнес-флагами disabled/inactive; collector approval
не разрешает их активацию. XML остаются48 updates/206 ранее сохранённых NEW,
PREVIEW_ONLY/import_allowed=false. Полного business PASS1270 нет.

Реальные результаты и новые проверки находятся в workspace reports/
COMMERCIAL_SOURCE_ISOLATION_20261027/RECEIPT_CONTINUATION_20261028/.
