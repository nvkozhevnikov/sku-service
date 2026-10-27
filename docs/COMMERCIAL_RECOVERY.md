# Коммерческие наблюдения и отдельный confirmed REFRESH — 27.10.2026

ТЗ: workspace `specs/approved/2026-10-27_COMMERCIAL_REFRESH_RECOVERY.md`.
Результаты и точная пара HEAD/SHA: workspace `reports/COMMERCIAL_RECOVERY_20261027/REPORT.md`.
Подготовка и offline QA разрешены. **Новый HTTP, ingest и matching ещё не разрешены.**

## Достоверность полей

Цена берётся из собственного блока товара, с исходным значением и путём селектора.
Нулевая цена, запрос цены, отсутствие, ошибка и неоднозначность — разные состояния.
Отсутствие цены не означает нулевую цену. Наличие не выводится из цены.
Валюта требует собственного priceCurrency/data-currency/явного символа; отсутствие
валюты не заменяется RUB. Конвертации валют и предположений о НДС нет.

Числовая публичная цена сама по себе не доказывает retail/RRP/dealer/wholesale/promo.
`price_semantics.price_type_proof` принимает только однозначный собственный текст
типа цены или явный data-price-type. Доказательство содержит исходный label/selector/rule.
Неоднозначность остаётся unknown. Существующий CHECK migration015
`offer_commercial_observations.price_type='unknown'` сохранён: business type записывается
в существующий extraction_evidence JSON, а доказанный тип — в projection offers.
NEW preview читает именно текущий observation proof; старый тип предложения не подставляется.
Никакой миграции, изменения ACL или массовой переклассификации старых данных.

Положительное/отрицательное schema.org наличие читается в собственном DOM.
Нормализация сохраняет InStock/OutOfStock/PreOrder/Incoming/BackOrder/Discontinued;
неизвестные и противоречивые значения остаются unknown. Нет глобального поиска по
рекомендациям/корзине. Неизвестный остаток не превращается в 0.

Native writer сохраняет price_raw/price_source/business proof, включая price_on_request.
KAMI zero/request не сохраняют положительную старую цену как новую. Цены «от»,
групповые и variant/group observations хранятся, но не становятся точной XML-ценой.

## Один точный REFRESH

`scripts/supplier_system.py prepare --confirmed-commercial-targets ...` выполняет
только SELECT и создаёт draft. Новое purpose `confirmed-commercial-refresh/v1`
использует существующие approval Session, PostgresJobQueue, Scheduler, Orchestrator,
SourceBackend, SourceCheckpoint и native RESUME. Второго scheduler/очереди/панели нет.

Scope: 1270 существующих подтверждённых source products, 1215 различных Product ID:

| Source | URL |
|---|---:|
| partner_st | 117 |
| optimum | 0 |
| beka_mak | 14 |
| beka_mak_tr | 0, reference-only |
| intervesp | 320 |
| vekprom | 32 |
| kami | 783 |
| ltt_msk | 2 |
| ltt_sib | 1 |
| ltt_yug | 0 |
| uss_stanko | 1 |

Нулевой scope не создаёт задание. Он не разрешает дополнить каталог.
Новый допуск явно задаёт своё окно; истёкшие LIVE11/R12/R13 команды не используются.
Старый batch, его T0/deadline, задания, 3009 COMMITTED и checkpoints не меняются.
Обычный scheduled grant по-прежнему заблокирован незавершённым LIVE11. Этот отдельный
purpose разрешается только новым точным HEAD/engine/SHA и полным pinned target list.

Подготовка сверяет supplier/source/offer/URL/Product ID, effective identity, active и quarantine.
Перед запуском guard повторно сверяет БД 55461, PG17.11, System ID7698346631476390904,
PGDATA, rc_runtime NOSUPERUSER/NOBYPASSRLS, 1282 исходные связи и frozen old job hashes.
Queue snapshot содержит ровно утверждённый поднабор, а не первые N товаров каталога.
На каждом persist под блокировками проверяются исходные source PK/catalog ID/URL,
текущий running job и его approval. На confirmed target выполняется существующая
прямая semantic/F01/F03 проверка; новый fuzzy target не ищется.

`commercial_only` сохраняет price/stock capture, observation, seen и реальную commercial
history, оставляя имя, SKU, описания, свойства, media, options, identity и mappings прежними.
Он не вызывает `_replace_details`/DELETE и не выдаёт старым свойствам свежие field receipts.
Фактический runtime ACL уже содержит DELETE на пяти таблицах (см. прежний
RC_LIVE11_DML_ACL_20261023/REPORT.md:45); этот режим их не использует и ACL не меняет.
Для Intervesp точный прежний url-sha256 key сохраняется только при том же URL и
подтверждённом собственном numeric detail ID. Это не миграция identity.

Индивидуальные политики взяты из существующей LIVE11 CSV matrix; SHA matrix заморожен.
Максимум два задания на разных host; каждый host сохраняет свой limiter/lease/robots,
паузы, jitter, число попыток, breaker и job time budget. Никакого cap20/50/100.
Первые пять SQL receipts — внутренний автоматический gate того же полного задания.
Для восьми непустых scopes max_attempts=1: повтор wire запроса не разрешён.
До трёх queue attempts с тем же frozen input допускают только безопасный native RESUME.

## STOP, восстановление, настройки

Отдельная плохая карточка изолируется существующим durable quarantine с capture/reason.
Семантический конфликт допускает карантин только после доказанного SQL rollback/absence;
mapping не изменяется. Permission/SQL/receipt/identity failure, breaker, 403/429,
неизвестный HTTP outcome и истечение окна остаются STOP. Нельзя автоматически
повторять UNCERTAIN или скрывать массовые ошибки под quarantine.

Команда STOP создаёт только запрос остановки. Дождаться FINAL, нулевых active jobs и
SETTINGS_RESTORED.json. Не завершать PostgreSQL/worker принудительно посреди транзакции.
Окно прекращает dispatch за пять минут до hard limit. До завершения действуют SQL
timeouts и bounded HTTP. Внезапное питание сохраняет durable PG/CP, но не является
доказательством исхода последнего HTTP/commit; автоматического restart нет.

Временные approved enabled/rate/host settings применяются после approval; исходные
сохраняются в плане. CAS restore не перезаписывает параллельное изменение оператора;
при неудаче получается UNVERIFIED, а не ложный PASS. После восстановления — SELECT.
У zero-target источников настройки не меняются. ACL/roles/schema не меняются.

Повторный явный `run ... --resume-safe` в том же окне использует тот же SHA и
native checkpoint/SQL probes. COMMITTED не скачиваются/не записываются повторно,
cached responses обрабатываются offline; история и matching receipts сохраняются.
Прерванная операция требует этого явного режима. UNCERTAIN, semantic/systemic STOP,
исчерпание трёх attempts, новая версия кода или истечение окна требуют решения;
не следует вручную менять status/checkpoint или расширять допуск.

## Selection, freshness, XML

Существующий matching engine проверяет подтверждённые связи без rebind. Пишется
реальный receipt; 1282 mapping baseline сверяется до и после. Старые matching pending
LIVE11 не входят в этот новый scope и не закрываются им автоматически.

По завершении существующий XmlExportService создаёт отдельный локальный preview,
selection plan и включения/исключения по каждому source/URL. Selection profile
`offer-selection-v1/receipt-preview-v2` остаётся read-only; persisted selection не подменяется.
Цена TTL6h и наличие TTL3h проверяются независимо по HTTP200/SQL observation и identity.
Свежая цена допустима без наличия и наоборот. Stale/unknown поле опускается.
Наличие коммерческого receipt не обновляет TTL сохранённого content.
Empty updates даёт COMMERCIAL_REFRESH_NO_USABLE_UPDATES, а не бизнес-PASS.
Число URL не равно числу XML записей: дубликаты supplier offers, REVIEW/CONFLICT,
карантин, отсутствие полей и TTL могут уменьшить охват. Верхняя граница данного
scope —1215 различных Product ID, не обещание1215 пригодных XML записей.

Все XML помечены local preview/import_allowed=false. Совместимость с ESOL и импорт
блокируются Stage6 BLOCKED_IMPORT_CONTRACT_EVIDENCE. Production/Bitrix/cron/deploy/push
не разрешены. Сквозная фактическая HTTP→SQL→matching→selection→XML приёмка остаётся
pending до отдельного exact approval и реального выполнения.
