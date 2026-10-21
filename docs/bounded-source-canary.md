# Один согласованный сбор всех11 и необязательный canary

OPERATOR-CONFIRMED 2026-10-21: универсальный floor20/default20 и обязательные
отдельные партии по5 — NOT_ACCEPTED. Текущий контракт: [source-runtime.md](source-runtime.md).
Этот документ не разрешает HTTP, SQL writes, включение supplier или host policy.

Одна форма существующей панели → preview → SHA256 → одно manual задание.
Явный общий cap1..100000, без default100; REFRESH всех выбранных известных URL
либо DISCOVERY с frozen seeds/cap/page/time. Малый cap остаётся optional canary.
Для всех11 backend перед writes проверяет точную testDB/physical identity и
текущий enabled. REFRESH также проверяет PK/external_id/URL выбранной партии.

В snapshot source-bounded-job/v2 сохраняются исходный общий cap, policy,
host-bound approval, gate1..20 (default5), time/page budgets и target.
После HTTP+SQL PASS первых карточек тот же job продолжает весь snapshot.
SQL ошибка, 403/429/CAPTCHA, robots или lease failure — STOP с FIRST_STOP и
checkpoint. COMMITTED не скачивается снова; UNCERTAIN требует решения по evidence.
Старые source-canary/v1 сохраняют свой cap≤50; новый больший scope создаётся
только новым подтверждённым snapshot. Код/engine changes не обходят resume review.

Queue выбирает до N unique active/stable URLs в SQL ДО freeze (ORDER BY source PK,
LIMIT N); total известного manifest отображается отдельно. Generic full/source
без cap/preview не ставится в очередь. RESUME сохраняет исходный общий лимит.
При attempts1 и N5 runtime не отправляет шестой product GET; утверждённый attempts>1
может повторить тот же URL в пределах policy, но не добавить шестую identity.

У wrappers нет скрытой скорости. Полный rate_settings и host_policy_approval
с точным digest должны быть отдельно утверждены и установлены перед LIVE.
Robots/Retry-After, общий limiter/concurrency/jitter/timeout и safe STOP обязательны.
Target пока только127.0.0.1:55461/rc_verify_vi_20261019, PG17.11,
system7698346631476390904, PGDATA из partner_pilot.PGDATA. Другой target — отдельная
проверенная адаптация, не переменная для обхода identity guard.

## Beka Mak: evidence и причина disabled

PACKAGED-EVIDENCE: SOURCE_READINESS.json последнего read-only preflight:
supplier11 enabled=false,109active/stable knownURLs. Первые5 точно сохранены
в tests/fixtures/beka_canary_five.json с SHA исходного evidence: PK5352,5354,
5358,5359,5360 / external19240,19224,19241,19236,19237. Никаких новых GET.
Универсальный ORDER BY source PK даёт эти пять в том же порядке; отдельного
хардкода Beka Mak в runtime/queue нет. Перед LIVE сверить preview с evidence.

REPOSITORY-VERIFIED: PASSIVE_COMMERCIAL_SUPPLIERS включает Beka Mak;
ensure_supplier/_ensure_passive_commercial_supplier создают такие namespace
с enabled=false. import_stage4_commercial_to_rc.py требует false и сохраняет
это состояние. Это защитный исторический режим пассивного источника.
Точный первоначальный actor/time не устанавливался заново SQL-запросами.

## Процедура временного включения — НЕ ВЫПОЛНЕНА

1. Получить отдельное подтверждение именно beka_mak, режим и общий cap N, host profile/page/time/gate и временного
   enabled false→true→false, HTTP и SQL только в названной testDB. Проверить
   HEAD, identity/PGDATA, UTC postLIVE50 baseline,1282links, чужие клиенты,
   jobs/lease/schedules; scheduler остаётся выключенным. Создать новый backup,
   SHA256 и full decode без restore. Не использовать mainRC1/default frozen
   review connection вместо явно привязанной testDB конфигурации панели/worker.
2. До включения сохранить полный supplier11 row, enabled=false, timestamp,
   schedule state и fingerprint остальных suppliers в отдельные evidence.
   Для cap5 проверить точные fixture PK/external_id/URL/order. Для большего cap
   требуется отдельное разрешение на весь новый snapshot; старые5 не расширять.
3. Только после разрешения: под identity guard и блокировкой supplier выполнить
   compare-and-set enabled=false→true для id11/code=beka_mak, записать audit.
   Установка нового профиля rate/crawl требует отдельного разрешения с точным
   allowlist полей и digest; код сейчас её не выполняет. Не менять active offers,
   matching, schedules или другие
   supplier rows. Использовать writable подключения только этого test target,
   после identity guard; read-only default кластера не менять глобально.
4. Пересоздать preview после включения (digest меняется), подтвердить одно
   bounded задание. Проверить immutable scope N перед штатным worker. Первые
   карточки — внутренний gate, после PASS job продолжает согласованную партию.
   Без scheduler и автоповтора; DISCOVERY/RESUME допустимы только в утверждённом scope.
5. В finally после terminal receipt и освобождения lease: под тем же guard
   проверить отсутствие других jobs, compare-and-set true→исходное false,
   проверить enabled=false readback. Сохранить audit и отдельный cleanup receipt.
   Не восстанавливать весь supplier row поверх новых фактов и не подделывать
   updated_at; восстановлению подлежит исходный enabled, а не история аудита.
6. При identity mismatch/потере владения — STOP, не менять другой target и не
   выполнять blind cleanup. Сохранить ошибку восстановления и передать оператору.
   После readback и проверки клиентов остановить только собственные процессы/PG.

Ни включение, ни его SQL-шаблон/worker не запускаются этой разработкой.
Offline проверка: python scripts/qa_source_runtime_offline.py --bounded-only.
Она запрещает внешний HTTP/DNS/psycopg, разрешая loopback self-pipe TestClient.
Fake backend доказывает5card/capture writes и scoped matching5, не лимит общего
числа SQL statements/property rows. Реальный SQL end-to-end остаётся canary gate.
