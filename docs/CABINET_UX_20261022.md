# Кабинет оператора — запись 2026-10-22

Рабочая запись UX. Код панели, очередь, PostgreSQL, cron и deployment этим файлом не меняются.
System-of-record дерева: `feature/sterbrust-unified-rc-20261019`, HEAD `34739cb`.
Полная спецификация: [CABINET_UX_SPEC_20261022.md](CABINET_UX_SPEC_20261022.md).
Макет вёрстки, не снимок БД: [cabinet-ux-prototype/index.html](cabinet-ux-prototype/index.html).

Чужие незакоммиченные файлы не редактировались и в эту запись не входят как diff:

- `scripts/qa_source_runtime_offline.py`
- `universal_supplier/control_plane/queue.py`
- `universal_supplier/control_plane/source_backend.py`
- `universal_supplier/control_plane/source_runtime.py`
- `config/live11_approval_matrix.csv`
- `docs/LIVE11_BATCH_WORKFLOW.md`
- `docs/LIVE11_BEKA_GATE_RECEIPT.md`
- `scripts/source_batch.py`
- `tests/test_source_batch.py`
- `universal_supplier/control_plane/source_batch.py`

Повторных проверок runtime, matching, SQL QA и LIVE11 не было. Их статус берётся из уже лежащих документов.

## Как читать два документа вместе

Спецификация кабинета описывает перекомпоновку существующих шаблонов на чистом `34739cb`: обзор, поставщики, один ручной снимок `source-canary` + `POST /suppliers/{id}/start`, честные колонки HTTP и SQL, товар, REVIEW/CONFLICT, конкуренты, отчёт run. Она не добавляет `POST /batch` и не переносит грязные hunks очереди.

`docs/LIVE11_BATCH_WORKFLOW.md` — документ другого исполнителя, не на HEAD. Его раздел «Границы и отчёт для будущего UX» задаёт payload `source_batch_status()` для экрана пакета: source/phase/job/attempts, шесть русских состояний, processed/remaining, SQL COMMITTED, HTTP и matching по попыткам, `sql_counts`, изменения цены и наличия как события истории, REVIEW/CONFLICT/NEW в scoped matching. Unknown остаётся null. Cumulative RESUME не суммируется. Полнота внешнего каталога — `NOT_PROVEN`.

Пока этот payload не на commit, кабинет его не вызывает и второй batch API не проектирует. После commit Codex экраны заданий и итогового отчёта читают этот payload. Поля HTTP и SQL по-прежнему не схлопываются: отсутствующий ключ — «нет данных», присутствующий `0` остаётся нулём.

## Дорожная карта до свежего сбора 11

Цель: один разрешённый свежий сбор всех 11 поставщиков, обработка, проверка качества, передача разработчику. Контур конкурентов остаётся отдельным. Stage 6 import и ESOL остаются закрытыми.

1. Код пакетного сбора 11 — ведёт Codex в незакоммиченном `source_batch`. Не HEAD.
2. Отдельное разрешение оператора на enable 11, host policy и target. `LIVE11_BATCH_WORKFLOW.md` прямо говорит: без принятого addendum LIVE остаётся BLOCKED. Исторический B0 SQL PASS не повторять. Роли PostgreSQL не менять.
3. Разрешённый fresh11: receipts, coverage, `external coverage NOT_PROVEN`, пока traversal это не доказал.
4. Адресный RESUME и решения REVIEW/CONFLICT. Scope не расширяется.
5. Проверка качества по уже существующим правилам matcher. Новых правил эта запись не задаёт.
6. Handoff разработчику. Merge в `develop`, cron и deployment — только отдельное разрешение.

## DONE / APPLIED / VERIFIED / BLOCKED / NEXT

DONE: спецификация восьми экранов и макет вёрстки. Ревью спецификации закрыто с 0 открытых замечаний до копирования в дерево. Шаблоны панели не переписывались.

APPLIED: только эти три новых пути документации. `docs/CABINET_UX_20261022.md`, `docs/CABINET_UX_SPEC_20261022.md`, `docs/cabinet-ux-prototype/index.html`. В `base.html`, очередь, adapters, миграции и БД ничего не внесено.

VERIFIED: сверка спецификации с маршрутами и allowlist прогресса на HEAD `34739cb` выполнена в том ревью. Этот заход заново тесты не гонял. `git status --short` на 2026-10-22 подтвердил тот же HEAD и тот же список чужих грязных путей, плюс неотслеживаемый `docs/LIVE11_BEKA_GATE_RECEIPT.md`. LIVE HTTP, SQL write и свежесть 11 поставщиков этой записью не подтверждаются.

BLOCKED:

- Свежий сбор 11. Нет отдельного разрешения оператора на HTTP, SQL writes, enable, смену ролей, cron и deployment. Документ LIVE11 сам держит LIVE в BLOCKED до решения по enable всех 11.
- Встройка кабинета в шаблоны, пока Codex держит `queue.py`, `source_backend.py`, `source_runtime.py` и `source_batch.py`.
- Вызов `source_batch_status()` из UI, пока метода нет на HEAD.
- Импорт sterbrust.com и ESOL. Stage 6 остаётся `BLOCKED_IMPORT_CONTRACT_EVIDENCE`.

NEXT: Codex доводит пакет до commit, не смешивая его с этим UX-diff. Оператор отдельно решает addendum enable 11. После commit кабинет показывает payload статуса пакета на уже описанных экранах заданий и отчёта, без второго API и без подписи LIVE у offline QA. До этого разрешения сбор не запускать.
