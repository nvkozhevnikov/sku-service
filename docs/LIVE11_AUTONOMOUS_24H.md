# LIVE11: один автономный запуск до 24 часов

Статус: REPOSITORY-VERIFIED подготовка. Новый LIVE, R12, HTTP, ingest и matching
не разрешены самим этим документом. Нужны точные новый HEAD/SHA и подтверждение
оператором предлагаемого допуска. Не запускать старые NEXT_7/RESUME_APPROVED.

## Что используется

Существующие batch `3a69ca0d-b9d0-41f9-8f2a-49c4b43bc57d`, queue,
Orchestrator, SourceCheckpoint, source backend и matching. PostgreSQL только
127.0.0.1:55461/rc_verify_vi_20261019, system7698346631476390904, rc_runtime.
Никаких новых очередей, API, схем, парсеров, PostgreSQL-ролей или ACL.

`start_live11_once.ps1` создаёт одну задачу Windows текущего пользователя с
InteractiveToken/LeastPrivilege, без триггеров, без RestartOnFailure,
StartWhenAvailable или WakeToRun. Action непосредственно запускает pythonw.exe
и `live11_autonomous.py`; supplier workers — потоки этого же процесса.
MultipleInstances=IgnoreNew, ExecutionTimeLimit=P1D, AllowHardTerminate=true.
Для каждого approval SHA существует один exclusive CLAIM.json. Ошибка,
перезагрузка или ручное повторное нажатие не открывают новый запуск этого SHA.

Это одноразовый механизм запуска процесса, а не scheduler поставщиков.
Планирование поставщиков по-прежнему выполняет только существующий Orchestrator:
максимум два задания на разных host, исходные frozen rate/robots/host budgets.

Закрыть Codex можно. Пользователь должен оставаться вошедшим в Windows; экран
может выключаться и сеанс можно заблокировать. Выход из Windows, принудительный
сон, закрытие крышки, питание и Windows Update не блокируются. Обновления,
антивирус, UAC, ExecutionPolicy и другие защиты не отключаются.

## Время и допуск

Исходные T0/deadline остаются в SQL snapshots и evidence без изменений:
2026-10-22T11:27:36.560055Z / 2026-10-26T11:27:36.560055Z.
Предыдущий отдельный execution_until: 2026-10-27T11:27:36Z.

Предлагаемый новый допуск: ровно один старт не позднее
2026-10-27T11:27:36Z; общий процесс не более 86400 секунд от фактического
старта, и не позднее 2026-10-28T11:27:36Z. Это явное продление, а не
перезапись старого deadline. Если старт позже 26.10 11:27:36Z, полные 24 часа
уже не помещаются в прежнее окно. Разрешение на новый срок пока требуется.

CLAIM связывает actual started_at/work_until/hard_until с exact approval SHA
и HEAD; его hash хранится в окружении этого процесса и проверяется backend.
Через 23ч55мин по monotonic clock либо раньше при достижении UTC work_until
начинается штатный STOP. Пять минут внутри общего лимита оставлены на закрытие
транзакций и восстановление настроек. Сдвиг часов назад не добавляет времени;
сдвиг вперёд может остановить раньше. Дополнительный предел Windows — 24 часа.
Новые запросы, persist и matching проверяют stop перед началом операции.
Ранее начатый HTTP/SQL может завершаться в резерве shutdown.

При зависании процесса ОС может принудительно завершить его на hard limit.
При недоступной БД, отказе диска, перезагрузке или kill нельзя обещать успешное
восстановление настроек. Для этого до активации сохраняется
RESTORATION_PENDING.json; исчезает он только после SELECT-подтверждения
baseline и отсутствия active jobs. Наличие файла — обязательная ручная
проверка, а не разрешение автоматически перезапускать сбор.

Предлагается только одна R12 для stopped/cancelled родителей119–127;
DISCOVERY ceiling остаётсяD4, scopes/лимиты не расширяются. R13 этим пакетом
не разрешается. Завершение всего внешнего каталога за24ч не гарантируется:
бюджеты discovery и обязательные STOP остаются, неполнота показывается честно.

## Ошибки

Локальная parse/normalization/identity ambiguity остаётся в существующем
durable quarantine/REVIEW/CONFLICT. Сохраняются URL, capture, receipt и причина;
карантин не считается COMMITTED или AUTO. HTTP с сохранённым ответом может
изолироваться по существующей политике после ограниченных retries. Только
ранее утверждённые доказательные scope/duplicate/TimeoutError случаи могут
использовать свои точные proofs без повторного HTTP/ingest. Произвольный
UNKNOWN transport outcome автоматически не повторяется.

STOP: SQL/identity/receipt/checkpoint/mapping inconsistency, approval/HEAD/SHA,
window, robots/host/rate violations, HTTP403/429/Retry-After, native circuit
breaker и массовые source failures, недостаток диска или отказ local reports.
Порог диска — 5GiB на evidence- и PGDATA-томах. Проверка диска кэшируется
на5сек; SQL health/identity — каждые30сек. Native profiles не меняются:
KAMI3errors/cooldown60, Partner/Optimum5/30. Другие supplier rate profiles
сохраняются ровно из матрицы.

После критического STOP попыток «чинить всё и продолжать» нет: worker закрывает
работу, сохраняет частичный результат и возвращает настройки при доступной БД.
Неизвестная ошибка не превращается в локальную успешную карточку.

## Почасовые отчёты без ИИ

Папка feature: `reports/autonomous-runs/<approval_sha>/hourly/`.
Начальный, затем каждые3600monotonic секунд и итоговый снимок:
versioned JSON/CSV плюс atomic `latest.json`/`latest.csv`.
Отдельный `heartbeat.json` обновляется каждые10сек.

Отчёт использует readonly SQL snapshot и проверенные SourceCheckpoint copies.
COMMITTED подтверждается captures/observations; уникальные товары считаются по
source_product_id. AUTO/REVIEW/CONFLICT/NEW — только для подтверждённого fresh
matching. Старые durable решения у matching_pending не выдаются за свежие.
1282 исходных mappings и manual-decision hashes проверяются через SELECT.
Нет HTTP, matching apply, enqueue, backup или LLM/API в функции отчёта.

Поля: время, источник, COMMITTED, unique, matching buckets/pending, quarantine,
errors, cached/inflight, remaining_urls, uncommitted_refresh_urls, rate и ETA,
phase/job/status/reason/resume_available, PostgreSQL/worker и1282exact.
remaining_urls — известная очередь frontier+cached+inflight; uncommitted_refresh
включает и терминальный карантин. ETA относится только к известной очереди,
помечена INFERENCE и не включает ещё не завершённый discovery. При нулевой
скорости/первом отчёте ETA=null. Пропуск данных никогда не означает0ошибок/PASS:
при отказе SQL/receipt/mappings оба latest-файла показывают UNVERIFIED/error,
сеанс останавливается. Хранятся исходные checkpoints, отчёт их не заменяет.

## Как запускать и проверять

Единственная точная команда запуска после подтверждения будет сохранена в
workspace `reports/RC_LIVE11_AUTONOMOUS_24H_20261026/START_AFTER_APPROVAL.txt`.
Она вызывает `start_live11_once.ps1 -Approval ... -Sha256 ... -ConfirmSha ... -Start`.
Без -Start выполняются только SELECT preflight и экспорт XML, без регистрации
задачи. Доступ Task Scheduler и pythonw проверены отдельным offline probe;
при запрете регистрации показывается ошибка Windows, права не повышаются.

Утром открыть hourly/latest.json либоCSV и проверить timestamp, worker,
original1282_exact, matching_pending, errors/phase reasons и remaining.
FINAL.json подтверждает остановку,0active и settings_restored=true.
RESTORATION_PENDING.json или отсутствие свежего heartbeat при состоянии RUNNING
требуют проверки; старый CSV не является доказательством продолжающегося сбора.
Последний EXIT_ERROR.json/runner.log объясняет завершение.

Для ручной паузы создать в папке `<approval_sha>` файл STOP_REQUESTED
либо нажать существующий STOP в кабинете. Это штатная остановка. Не использовать
Stop-ScheduledTask как обычный STOP: он может убить процесс до cleanup.
После завершения остаётся инертная запись задачи без триггеров; её можно удалить
по точному имени STERBRUST-LIVE11-<первые16SHA>, убедившись что State не Running.
Не удалять CLAIM/checkpoints/settings evidence для «повторного старта».

На текущей паузе3009COMMITTED/2847unique/1282exact/61matching pending/2cached,
0active. При разрешённом native RESUME сохранённые ответы обрабатываются локально,
pending matching завершается существующим engine до нового HTTP, с native
receipt stamps. Сегодня эта обработка ещё НЕ применялась.

## Питание и durable данные

Временный `SetThreadExecutionState(ES_CONTINUOUS|ES_SYSTEM_REQUIRED)` действует
на поток runner; `ES_DISPLAY_REQUIRED` не устанавливается. В finally запрос
освобождается. План питания и настройки экрана не переписываются.
Это не защита от пользовательского принудительного сна/отключения питания.

Read-only проверка PG17.11: fsync=on,full_page_writes=on,synchronous_commit=on.
Отчёт/health требуют сохранить эти настройки; они не изменяются кодом.
PostgreSQL использует обычную WAL crash recovery. При аварии процесс/задача
сами не рестартуют; возможный разрыв SQL commit/CP остаётся под existing
receipt/UNCERTAIN guards. Проверять recovery/настройки перед новым допуском.

Официальные основания: [Task settings](https://learn.microsoft.com/en-us/powershell/module/scheduledtasks/new-scheduledtasksettingsset?view=windowsserver2025-ps),
[SetThreadExecutionState](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-setthreadexecutionstate).
Native offline probe проверил3-секундный одноразовый процесс и release power request;
полный24-часовой LIVE не выполнялся и закрытие самого Codex не имитировалось.


REPOSITORY-VERIFIED final QA:312focusedPASS (3 known pre-existing UI-auth cases explicitly deselected),154broaderPASS including2200saved-model/22phase native replay. PowerShell AST PASS. Native Windows offline probe task exited0, used/released system-required idle request, had no triggers/restarts and was removed; no supplier HTTP/SQL/LLM. Readonly real report validates11sources,3009COMMITTED/2847unique/1282exact/61matchingpending/2cached/0active/settingsrestored. PostgreSQL durability on/on/on; diskD~34GiB/C~60GiB. No full24h LIVE performed.

Intermediate QA failures retained as evidence: historic fixtures initially hit their real expired calendar deadline; tests now freeze only the recorded replay calendar without editing frozen evidence or real guards. An intermediate large replay also correctly rejected an engine fingerprint change while code was being edited; final312/154 runs used stable engine and passed. No B0 SQL QA/Partner-STLIVE50 rerun. New exact operator HEAD/SHA, R12/window and one-shot launch approval remain pending.

Final hourly guard also binds each SQL capture fingerprint to its original external identity; mismatches stop with HOURLY_SQL_RECEIPT_MISMATCH. Real readonly3009receipt/sample and added offline identity-drift case PASS.

Autonomous admission additionally requires the active same-SHA Controller and bound one-use session before enqueue/admission/runtime, including alternate legacy recover paths. A manifest/context alone outside that controlled process cannot enqueue R12. Guard regression PASS before any SQL.
