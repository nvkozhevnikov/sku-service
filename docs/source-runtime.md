# Одиннадцать collectors: runtime и безопасное продолжение

REPOSITORY-VERIFIED / APPLIED: beka_mak, beka_mak_tr, intervesp, vekprom,
kami, ltt_msk, ltt_sib, ltt_yug, uss_stanko проходят через существующие
registry, панель, crawl_jobs, PostgreSQL host lease и узкие writers.
Partner-ST / Optimum также поддерживают явные REFRESH/DISCOVERY/RESUME через
тот же runtime и SourceCheckpoint. Используются прежние native parsers и
ProductCard, прежний ingest в bounded transaction без full-catalog quality/
missing reconciliation. Capture и commercial observation сохраняются в той
же транзакции. Обычные native full runners и Partner-ST pilot не заменены;
они сохранены в registry для совместимости. Новый enqueue full без явного scope
блокируется для всех11; панель направляет в единый preview. Pilot и checkpoint mode
не смешиваются. Native defaults сохранены: Partner-ST4/0.20s/timeout40s,
Optimum3/0.15s/timeout35s. Новые wrapper host policies не активированы.

## Режимы

- REFRESH замораживает известные активные SQL URL и исходные external ID.
- DISCOVERY сохраняет граф sitemap/category traversal и manifest найденных
  кандидатов. Реальную identity устанавливает прежний detail parser.
  KAMI использует обычные listing/category/pagination links, без filter API.
- RESUME читает исходный immutable input и сверяет точный capture с SQL
  capture + commercial observation. COMMITTED не скачивается и не пишется
  повторно. Сохранённый, но не committed capture обрабатывается offline.
  Отдельная matching receipt позволяет завершить matching после SQL commit.

UNCERTAIN, потерянная discovery receipt, сбой checksum/namespace, смена кода
или неподтверждённое SQL состояние блокируют автоматическое продолжение.
Потерянный writer lock не удаляется автоматически. Нужна адресная сверка
владельца/host lease и исходных receipts. Завершённые страницы discovery
не запрашиваются снова. Frontier больших каталогов сохраняется полностью;
общий cap задаётся явно (1..100000), без default100. Внутренняя product
партия ограничена concurrency (≤8) и техническим batch budget1000;
discovery pages1..100000 и time≤86400s задаются в frozen scope. Время
не может превышать max_run_duration_seconds утверждённого профиля.
Ни один режим не деактивирует отсутствующие товары.

## Политика host

У девяти wrappers НЕТ default скорости или универсального floor20. До нового
запуска обязателен полный явный профиль: max_concurrency (1..8), положительный
min_request_interval_seconds, jitter_min/max_seconds, request_timeout_seconds,
max_attempts (1..3), max_run_duration_seconds. Retry-After обязателен.
Все числовые значения конечны, interval>0; более строгий robots Crawl-delay/
Request-rate вызывает STOP до утверждения подходящего профиля. Отсутствие
Crawl-delay само по себе не утверждает скорость.

Индивидуальный профиль хранится в supplier.rate_settings; crawl_settings содержит
host_policy_approval={host,reference}. В окружении worker/panel требуется точный
STERBRUST_APPROVED_HOST_POLICIES[host]=policy_digest(source,host,policy,reference).
Digest учитывает полный snapshot; любое изменение concurrency/interval/jitter/
timeout/retries/time budget требует нового утверждения. Никакой из девяти
профилей этой разработкой не выбран, не записан в БД и не активирован.
Native defaults не копируются wrappers. Первый запрос новой lease ждёт свой
утверждённый интервал. Robots, discovery, карточки, redirects/retries используют
один DomainRateLimiter и общий лимит конкурентности в существующей host lease.

## Один согласованный bounded job

В той же панели — «Один согласованный сбор», явный общий cap без default.
Можно указать50/100/500 или количество всего подтверждённого manifest.
Preview показывает known/selected, REFRESH либо DISCOVERY, policy, safety gate,
page/time budgets и test PostgreSQL. Scope фиксируется до enqueue и подтверждается
SHA256. Queue пересоздаёт preview, проверяет digest; generic full/source без cap
и preview заблокирован, RESUME — только исходного terminal job.

REFRESH выбирает LIMIT N до freeze, PK/external_id/URL и порядок immutable.
DISCOVERY фиксирует seeds и общий cap/page/time, затем сохраняет найденный
manifest (≤cap) и граф. Достижение cap не означает полноту внешнего каталога.
Весь найденный сверх cap provenance сохраняется в discovery receipts, но detail
HTTP и SQL за пределами cap не выполняются. Непройденный граф не деактивирует товары.

Первые min(safety_gate_size,selected) карточек (по умолчанию5, настраивается1..20)
проверяются в том же job. Карточка6 не планируется до точного SQL COMMITTED для
первых5. После PASS цикл продолжает оставшийся immutable snapshot автоматически.
Ошибка HTTP/парсинга/SQL останавливает продолжение; captures/terminal receipts
сохраняются. Matching по всем committed IDs использует прежний engine; receipt
одного успешного matching сохраняется одной атомарной checkpoint записью.

Deadline/STOP до входа в transport допускает NOT_REQUESTED только с явным
transport_started=false receipt. Потерянный ответ после входа остаётся UNCERTAIN
даже при attempts=0. RESUME сверяет exact SQL, пропускает COMMITTED, обрабатывает
сохранённые captures offline, продолжает оставшийся scope без расширения cap.
При смене engine fingerprint старый snapshot требует адресного review; ни один
старый пятиэлементный snapshot не расширяется и не переписывается.

403/429/anti-bot и 503 с положительным Retry-After останавливают host.
FIRST_STOP сохраняет первую причину; STOP, deadline и локальная отмена
различаются. Внешний retry после такого STOP требует нового решения.
В полёте допускается завершение уже начатого запроса в пределах timeout.
При потере lease новые запросы, SQL ingest и matching запрещены.

## Операторская панель и разрешения

Это дополнение существующей панели. Новые действия всех11 появляются только при
STERBRUST_SOURCE_RUNTIME_ENABLED=YES. Реальный worker дополнительно требует
STERBRUST_ALLOW_SUPPLIER_HTTP=YES и, для persistence,
STERBRUST_ALLOW_SOURCE_WRITES=YES. Эти flags не означают разрешение оператора.
Разработка не включает их в рабочем окружении и не активирует scheduler.

RESUME доступен для terminal partial/failed/cancelled/stale_failed задания
после read-only проверки receipts/SQL; enqueue и worker повторяют проверку.
Показаны режим, found/processed/remaining, HTTP, SQL и matching отдельно,
измеренная скорость, причина STOP и checkpoint. Неизвестные SQL counts
не показываются как доказанные нули. Полнота внешнего каталога NOT_PROVEN.

## Offline QA

Запуск: `python scripts/qa_source_runtime_offline.py` в подготовленном Python
окружении. Скрипт запрещает requests, psycopg.connect, внешний DNS и socket
connect; loopback разрешён только для локальных тестовых HTTP серверов,
известные PostgreSQL порты запрещены. Fake transport/backend используются
вместо настоящих поставщиков и БД. JUnit и временные evidence находятся
в reports/q<timestamp>/; короткий путь нужен для Windows MAX_PATH.

Покрыты реальные девять парсеров, manifest, общий pacing/redirects, два
параллельных fake worker, approval digest, STOP/robots/Retry-After/403/429,
прерывание до/после SQL commit, точный SQL readback, missing matching receipt,
отклонение manifest1501 при неразрешённом cap1 для KAMI/Vekprom, панель и регрессии F01/F03/Partner/Optimum.
SQL backend проверяется fake cursor, реальные записи не выполняются.
Это не подтверждает LIVE скорость, полноту сайтов или SQL end-to-end.

## Следующий адресный canary

Для каждого host отдельно согласовать source, режим, URL/page/product caps,
время, policy и целевую тестовую БД с разрешением HTTP/SQL writes. Сначала
консервативный bounded canary через ту же очередь, затем проверка receipts,
STOP и отдельный interrupted RESUME canary. Для KAMI/Vekprom дополнительно
проверить coverage реального sitemap/listing и сохранение большого frontier.
Ускоренный профиль требует отдельного утверждения host. Ни одного такого
запуска эта разработка не выполняет. Partner-ST LIVE50 не повторять автоматически.
