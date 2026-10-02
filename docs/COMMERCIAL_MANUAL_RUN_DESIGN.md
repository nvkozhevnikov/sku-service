# Полноценный ручной коммерческий сбор — проект включения

Статус: `IMPLEMENTED, QA WRITE DISABLED BY DEFAULT`. Этот документ не
разрешает конкретный запуск записи в PostgreSQL.

## Отдельный путь

Панель создаёт только явное ограниченное задание `commercial_bounded` с полями:
supplier, целевая БД (только allowlisted Stage 4 QA), manifest SHA-256,
candidate offset/limit, catalog-page limit, пауза, режим `persist`, оператор,
время создания и idempotency key. Задание принимает отдельный worker только
после явного разрешения на запись. Scheduler не участвует.

Перед стартом worker повторно проверяет точный manifest SHA-256, fixed
host/port/database/role, disabled supplier и inactive offer. Он использует
`run_commercial_collection(..., repository=PostgresRepository)` только для
Stage 4 QA. Никакие `reconcile`, matching, selection, ESOL или canonical XML
не вызываются.

## Наблюдаемость и защита

- один advisory lock на supplier + manifest + target DB;
- журнал задания и item-level outcome: persisted/exact-noop/review/error;
- коммерческие captures/observations остаются append-only;
- повтор одного capture = no-op, новый capture = историческое observation;
- UI показывает target DB, режим записи, лимит, manifest SHA, результаты и
  диагностический XML; canonical XML остаётся отдельным;
- 403/429/challenge, изменение manifest или нарушение лимита останавливают
  только затронутый источник.

## Что уже есть

Stage 5D collector, HTTP sanitisation, adapters, manifest guards, bounded
pauses, `PostgresRepository.persist_commercial_observation`, append-only
capture/observation schema и diagnostic XML.

## Состояние локального журнала и XML

Панель сохраняет каждое изменение состояния ручного задания в локальный
`ui_runs/<run_id>/events.jsonl` с синхронизацией на диск. После перезапуска
история видна снова; прерванный запуск получает `interrupted` и **не**
возобновляется автоматически. Прогресс по карточкам, REVIEW и SQL counts
сохраняются в этом журнале. Это локальный файловый журнал QA, не таблица
production orchestrator и не доказательство успешного внешнего HTTP-запуска.

После ограниченной QA-записи панель читает текущую коммерческую проекцию из
той же фиксированной QA PostgreSQL и атомарно заменяет диагностический XML.
Если XML не обновился, задание отмечается `partial`: уже сохранённые
наблюдения не откатываются и не объявляются отсутствующими. XML по-прежнему
не является ESOL payload.

Нет автоматического повтора, scheduler или production write-path. Любая
конкретная запись требует отдельного разрешения на Stage 4 QA write и
успешной проверки именно этой базы. Реальный браузерный QA write после этих
изменений ещё не выполнен.
