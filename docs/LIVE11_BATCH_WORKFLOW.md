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
