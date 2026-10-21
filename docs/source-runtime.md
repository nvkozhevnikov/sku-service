# Одиннадцать collectors: runtime и безопасное продолжение

REPOSITORY-VERIFIED / APPLIED: beka_mak, beka_mak_tr, intervesp, vekprom,
kami, ltt_msk, ltt_sib, ltt_yug, uss_stanko проходят через существующие
registry, панель, crawl_jobs, PostgreSQL host lease и узкие writers.
Partner-ST / Optimum также поддерживают явные REFRESH/DISCOVERY/RESUME через
тот же runtime и SourceCheckpoint. Используются прежние native parsers и
ProductCard, прежний ingest в bounded transaction без full-catalog quality/
missing reconciliation. Capture и commercial observation сохраняются в той
же транзакции. Обычные native full runners и Partner-ST pilot не заменены;
они выбираются, когда collection_mode отсутствует. Pilot и checkpoint mode
не смешиваются. Native defaults сохранены: Partner-ST4/0.20s/timeout40s,
Optimum3/0.15s/timeout35s; новый HTTP budget по умолчанию300s ограничивает
вызов, не меняя исходный policy snapshot. Новые host policies не активированы.

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
один product batch ограничен 1000, discovery batch — 1000 узлами.
Ни один режим не деактивирует отсутствующие товары.

## Политика host

Для девяти wrappers без отдельного утверждения: один поток, минимум 20 секунд между стартами,
jitter 0..0.1 секунды, timeout 20 секунд, одна попытка, HTTP budget 300 секунд.
Первый запрос новой host lease также ждёт интервал, чтобы не сблизиться
с последним запросом предыдущего задания. Robots, discovery, детали,
redirects и retries проходят через один DomainRateLimiter.

Настройки индивидуальны в rate_settings каждого поставщика. Для concurrency
выше 1 либо интервала ниже 20 секунд нужны host_policy_approval с host/reference
и соответствующий digest в STERBRUST_APPROVED_HOST_POLICIES. Digest привязан
к source, host, полному policy snapshot и reference. Изменение параметров
аннулирует approval. Автоматического ускорения нет. 20 секунд — временная
защита, а не доказанный обязательный лимит сайтов. Более строгий robots
останавливает запуск до согласования политики.

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
frontier 1501 URL для KAMI/Vekprom, панель и регрессии F01/F03/Partner/Optimum.
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
