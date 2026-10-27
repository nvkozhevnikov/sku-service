# Universal Supplier: операторские настройки, bounded execution и два preview XML

2026-10-27. REPOSITORY-VERIFIED implementation. Проверки и точный локальный HEAD:
workspace `reports/UNIVERSAL_SUPPLIER_AUTONOMOUS_SYSTEM_20261026/COMPLETION_20261027.md`.
Система использует существующие FastAPI/Jinja, PostgreSQL queue, Scheduler, Orchestrator,
DomainRateLimiter, SourceCheckpoint, SourceBackend, matching и offer_selection.
Это не новая панель, очередь, scheduler или matching engine.

## Что применено и что ещё не применено

Новая feature: `feature/sterbrust-autonomous-system-20261026`.
Serving checkout `../sku-service` и его панель58097 не заменены.
Исходный LIVE11/R13 checkout `../sku-service-unified-rc-20261019` заморожен отдельно.
Новая функциональность не должна менять его HEAD/engine или потреблять старый CLAIM.

Все source HTTP/ingest/matching этого задания NOT_RUN. Production, ESOL, upload,
cron, бессрочный автозапуск, push и merge запрещены. Stage6 остаётся
`BLOCKED_IMPORT_CONTRACT_EVIDENCE`.

Миграция018 проверена только в `qa_supplier_automation_20261026` того же кластера.
На LIVE11 `rc_verify_vi_20261019` она НЕ применена: требуется отдельное разрешение.
Без018 новая версия честно останавливает login с
`AUTH_SCHEMA018_REQUIRED_FOR_RUNTIME_DML`; дополнительных GRANT не требуется.
Прежний механизм throttle удалял строки; новый UPDATE `cleared_at` сохраняет
failed_at и историю, включая общий счётчик между web workers/restarts.
Идемпотентный additive SQL: `migrations/018_login_throttle_non_destructive_reset.sql`.
Откат приложения не требует удаления поля/истории или destructive SQL.

## Один кабинет и точный PostgreSQL

После разрешённого применения018 и переключения существующей панели:

```powershell
..\sku-service\.venv-audit\Scripts\python.exe scripts\supplier_panel.py inspect
..\sku-service\.venv-audit\Scripts\python.exe scripts\supplier_panel.py serve --allow-loopback-http
```

Адрес тот же: `http://127.0.0.1:58097/suppliers`.
Если порт занят, новый процесс не подменяет и не останавливает прежнюю панель.
Перед переключением действующую панель завершают штатно в отдельно разрешённом окне.
Нужен существующий `SESSION_SIGNING_SECRET` (не менее32 байт) из прежней защищённой
конфигурации; не передавать его в командной строке, Git или отчётах. Вход — под
существующим SQL ADMIN. Launcher не создаёт пользователей, не меняет пароли/роли/ACL.
Loopback HTTP — явный opt-in только этого процесса; обычные secure cookies по умолчанию
не ослаблены. Origin/CSRF/RBAC/session revocation остаются включёнными.

Конфигурация writer: только `127.0.0.1:55461/rc_verify_vi_20261019`, rc_runtime,
NOSUPERUSER/NOBYPASSRLS, system7698346631476390904 и прежний PGDATA. Пароль читается
из существующего ignored credential файла через `runtime_config`, без legacy fallback.
55449 не используется вместо writer. Конкурентный55454 остаётся отдельным read-only
backend новостей/акций. Ни базы, ни Product ID этих систем не объединяются.

Основные адреса:

- `/suppliers` —11 источников, реальные SQL matching/freshness, последние R/D jobs,
  HTTP и SQL counters, ошибки и причины отсутствия допуска.
- `/suppliers/{id}/parsing` — индивидуальная ревизия, аудит и отдельный host confirmation.
- `/suppliers/{id}/freshness` — возраст отдельных полей и capture evidence.
- `/cabinet/sources/{code}` — прежний source preview/RESUME/status через frozen backend.
- `/live11` — прежние batch preview/status/report; write-session по умолчанию запрещена.
- `/scheduler/execution` — локальные hourly reports, heartbeat и безопасный STOP.
- `/exports/previews` — два отдельных XML, причины исключений и SHA256 downloads.
- `/news`, `/competitor-monitoring` — существующий read-only конкурентный backend.

UI login не запускает worker. В web процессе запрещены HTTP collectors/ingest/matching;
они включаются только внутри отдельно утверждённого bounded execution процесса.

## Индивидуальные настройки

Сохраняются в существующем `suppliers.crawl_settings.automation`, версияv1, revisionCAS,
actor/time/audit; transport values транслируются в существующий `rate_settings`.
Supplier enabled остаётся отдельным существующим административным действием.
R/D/schedule/export флаги по умолчанию false. Нет автоматического включения поставщиков.

REFRESH и DISCOVERY имеют независимые интервалы; start_time/timezone учитываются в
локальном календаре, включая DST. Не проигрываются слоты до начала нового execution grant.
Повтор тика/restart сверяется с durable jobs, их occurrence, lease и checkpoint.
По одному source одновременно допускается одна фаза. Глобально≤2 jobs на разных host.
`priority` — очередь (меньшее значение раньше); существующий commercial offer ranking
остаётся из `config/offer_selection.json` и не меняется настройкой очереди.

Delay min/max означает равномерный случайный **start-to-start** интервал на одном host.
Concurrency не обходит gate. Native limiter переиспользуется между jobs одного host;
для нескольких namespace на одном host действует более строгий из утверждённых floors.
Robots, Retry-After, retries/backoff, native circuit breaker и host caps остаются обязательными.
Новая быстрая rate policy требует отдельного ADMIN confirmation точного host/profileSHA.
Это подтверждение не включает LIVE и не утверждает новый execution grant.

Сохранённые jobs используют прежние immutable snapshots. Изменение настройки после
approval блокирует новые jobs источника `AUTOMATION_SOURCE_SETTINGS_CHANGED`;
для новой ревизии нужен новый точный допуск. Уже сохранённый scope/COMMITTED не переписывается.

## Два разных режима: LIVE11 и обычное расписание

**Незавершённый LIVE11 продолжает только свой existing recovery/backend.**
Новая обычная automation намеренно отвергает незавершённый protected batch:
`AUTOMATION_LIVE11_UNFINISHED_REQUIRES_SEPARATE_RECOVERY`.
Она не возобновляет старые batch jobs, не пополняет attempts и не заменяет original scope.

R13 подготовлен отдельно, НЕ выполнен: точный HEAD/SHA и команда находятся в workspace
`reports/RC_LIVE11_R13_PREPARATION_20261026/`.
Это разрешение не унаследовано от R12. После исходного окна его запуск запрещён;
изменить сроки можно только новым явным операторским решением, сохранив original evidence.

**Обычный bounded schedule** применяется после завершения protected LIVE11 и отдельного
утверждения актуальных source settings/caps/window. Подготовка допуска только SELECT:

```powershell
..\sku-service\.venv-audit\Scripts\python.exe scripts\supplier_system.py prepare --limits PATH_TO_EXPLICIT_11_CAPS_JSON --not-before UTC_START --execution-until UTC_END --output PATH_TO_NEW_APPROVAL_JSON
```

Limits JSON обязан содержать все11 namespace, у каждого целые положительные REFRESH и
DISCOVERY caps. Значения не имеют скрытого default100/500. Рост известного REFRESH scope
за cap блокирует источник, а не обрезает каталог. Неполный discovery всегда маркируется;
успех выбранных карточек не доказывает внешний полный каталог.

Manifest фиксирует HEAD, engine, exact target,11 supplier IDs, config/rate digests,
host policies, per-phase caps/page/time budgets, max2,1282 baseline, not_before/end.
Срок≤24часов. `inspect` проверяет SHA/HEAD/engine/SQL без запуска.
Ни write-session flag, ни зелёные тесты не заменяют `--confirm EXACT_SHA`.

## Одна команда после нового разрешения

После подготовки конкретного manifest и его точного HEAD/SHA подтверждения оператором:

```powershell
.\scripts\start_supplier_once.ps1 -Approval $env:STERBRUST_APPROVED_SCHEDULE_FILE -Sha256 $env:STERBRUST_APPROVED_SCHEDULE_SHA -ConfirmSha $env:STERBRUST_APPROVED_SCHEDULE_SHA -Start
```

Без `-Start` — только read-only inspect и экспорт XML плана Windows, без регистрации/старта.
Это одноразовая native Task Scheduler task: Interactive/Limited, pythonw, без triggers,
boot/logon start, RestartCount, WakeToRun и бессрочного cron. Существующий task не
перезапускается автоматически. Action — сам worker с threads, без дочернего detached crawler.
Сеанс не зависит от Codex/чата. Пользователь Windows должен оставаться вошедшим в систему;
закрытие чата допустимо, выключение/выход из Windows останавливает работу.

Runtime использует UTC+monotonic work STOP за5минут до конца. Внешний Task Scheduler
backstop — ещё за60секунд до absolute deadline. Clock rollback не продлевает runtime.
Thread-scoped SetThreadExecutionState удерживает только idle sleep; экран может выключаться.
Контекст/выход процесса освобождает request. Power plan/Update/антивирус не изменяются.

## Ошибки и восстановление

Одиночная доказанная ошибка извлечения/identity — durable quarantine с сохранённым
response/hash/receipt/history до ingest; SQL update не подменяется. Неизвестная цена не0,
неизвестное наличие не out_of_stock. Неоднозначный matching остаётся REVIEW/CONFLICT.
Не-200 с известным сохранённым ответом после native retries может изолироваться;
403/429/interstitial/Retry-After и массовый threshold блокируют источник.
Остальные источники продолжают свою разрешённую работу.

Недоступность PostgreSQL/lease, SQL receipts/checkpoint mismatch, original1282 drift,
HEAD/SHA/engine drift, robots/rate violation, disk<5GiB и неизвестный результат HTTP —
глобальный STOP. Никакого catch-all ignore или blind retry UNCERTAIN.
Source-local blocked reason не выдаётся за завершение источника. Отказ одной фазы
проверяется до admission другой: REFRESH403 не обходится запуском DISCOVERY.

Для safe partial RESUME существующий SQL/native checkpoint admission проверяется повторно.
COMMITTED не отправляется в HTTP/ingest, cached response обрабатывается offline,
matching receipts исключают повторное matching. Один occurrence ограничен≤3 queue attempts;
счётчик использует SQL timestamptz, поэтому UTC/+03 spellings не пополняют attempts.
его исчерпание требует решения, не нового REFRESH с потерей checkpoint.
Фазы замороженного LIVE11 имеют свои исходные R/D ceilings и сюда не входят.

Глобальный STOP доступен на `/scheduler/execution` или:

```powershell
..\sku-service\.venv-audit\Scripts\python.exe scripts\supplier_system.py stop --session PATH_TO_EXISTING_SESSION_DIRECTORY
```

Дождаться FINAL и SQL0active. STOP_REQUESTED не является доказательством остановки.
После power loss/kill сохраняются SQL/WAL/CP, незавершённый lease становится stale_failed;
UNCERTAIN и `.writer.lock` не очищаются автоматически. Нужны read-only reconciliation и
явное безопасное продолжение в действующем окне, а не неконтролируемый restart.
Новая ordinary automation не меняет DB settings временно; environment/sleep request
восстанавливаются finally. При PG outage FINAL_UNVERIFIED честно фиксирует неизвестность.

## Что читать утром

`reports/automation-runtime/<session>/hourly/latest.json` и `latest.csv`;
versioned hourly files остаются рядом, heartbeat обновляется каждые10сек.
FINAL/FINAL_UNVERIFIED/STOP/runner.log объясняют завершение.
Отчёты создаёт локальный Python без LLM/API/почасового полного backup.
Они содержат source,COMMITTED/unique,AUTO/REVIEW/CONFLICT/NEW,pending,quarantine/errors,
HTTP,remaining,observedURL/hour,ETA(INFERENCE),exactPG/worker/window/1282 evidence.
Скопированный native CP сверяется с более поздним repeatable SQL snapshot.
Отчёт дополняет CP, не заменяет его. Пустой сеанс не означает3009→0 в исходном LIVE11.

## Freshness и два локальных XML

price/availability/content TTL индивидуальны; для fresh нужен capture200,
fingerprint, точная observation identity и подтверждённое время. Synthetic/NOT_HTTP
evidence никогда не превращается в fresh LIVE. Будущие timestamps unknown.
Старые значения и material history сохраняются; единичная ошибка не деактивирует товар.
Остаётся прежняя политика только трёх здоровых полных обходов; bounded runtime
сам по себе вообще не разрешает deactivation.

Более новый неразрешённый source quarantine исключает карточку из обоих фидов;
старое валидное предложение не возрождается поверх отказа до следующего подтверждённого capture.
Updates preview — только существующий подтверждённый Sterbrust ID, текущий matching,
fresh price/currency/availability, native deterministic offer selection. Только три
commercial поля. Name/brand/description/properties/images/quantity не обновляются.
Нельзя повторно fuzzy-rebind подтверждённый Product ID.

New-products preview — только свежий SAFE_NEW/NEW_CANDIDATE, без физической/подтверждённой
связи, REVIEW/CONFLICT/registry collision, с проверенными brand/full model/kind/execution.
Cross-source dedup сохраняет supplier aliases; технический конфликт блокирует группу.
Различающиеся наборы характеристик, включая неполные, исключаются с причиной
`CROSS_SOURCE_PROPERTIES_REQUIRE_REVIEW`: отсутствие свойства не объявляется
доказанным semantic CONFLICT и не изменяет решения существующего matching engine.
Reference-only BekaTR исключён. Одинаковый артикул разных namespace не связывает товары.
После подтверждения реального Product ID карточка перестаёт быть NEW и проходит
existing-ID update flow; виртуальный NEWC не выдаётся за Product ID.

Экспортируется existing raw/normalized content/media contract, stale commercial поля
опускаются. Оба артефакта — **preview**, `import_allowed=false`, не ESOL contract.
Версия, counts, exclusions/reasons, freshness, coverage, SHA256 и selection trace
фиксируются вместе. Оба XML валидируются до атомарной смены latest pointer.
Сбой новой версии сохраняет прежнюю проверенную выгрузку; download проверяет hash.
Auto preview допустим только после фактически succeeded job+matching и если включён
соответствующий флаг; обычный ручной generate — только local read-only SQL.

## Проверки и предел доказательств

Offline: all11 native scheduled RESUME, retained COMMITTED, same-host pacing,
exactapproval/clock/STOP, source vs global failures, settings validation/CAS/TZ,
all mutation routes auth/CSRF, legacy XML/selection/F01/F03 и2200/22 saved replay.
Actual separate QA DB: migrations001–018 + repeat0,11 settings roundtrip,
non-destructive shared throttle,31 app routes,2 XML downloads,0collectorHTTP/enqueue.
Actual LIVE11: SELECT baseline tables/sequences/schema/3009/1282 unchanged;
native SQL monitor и11-source freshness view проверены без новых business writes.

Browser verification incomplete: IAB Origin rejected; Windows browser tooling stopped
with unsupported URL-policy enforcement. Нельзя выдавать TestClient за визуальную
приёмку или отключать Origin ради зелёного browser check. Serving обновление NOT_APPLIED.
24часовая LIVE работа новой версии не выполнялась и не доказана offline тестами.
