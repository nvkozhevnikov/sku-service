# Bounded source canary для девяти wrappers

REPOSITORY-VERIFIED / offline: beka_mak, beka_mak_tr, intervesp, vekprom,
kami, ltt_msk, ltt_sib, ltt_yug, uss_stanko. Нативные Partner-ST pilot и
Optimum не заменены. Этот документ сам по себе не разрешает LIVE/SQL writes.

В существующей карточке поставщика: «Ограниченный тестовый запуск», количество
1..50 (default5), затем GET preview. До enqueue видны total known unique URLs,
selected count, REFRESH, max_products, точные PK/external_id/URL/порядок,
host policy и target PostgreSQL. Preview не включает disabled supplier.

Queue выбирает первые N unique active/stable известных URL по source PK:
SQL DISTINCT ON(URL), затем ORDER BY id LIMIT N. Count over window сообщает
полное количество, но в Python и immutable input передаётся только партия.
Подтверждение содержит digest полного snapshot. Queue под блокировкой supplier
повторяет отбор и отклоняет изменение preview, лимита, enabled, policy/engine
или target. При выборе5 из109 entries≤5, crawl_settings.max_products=5,
mode=REFRESH, seeds=[], persist_source_data=true, source_canary=true.

Worker повторяет структурную проверку до backend/HTTP; default100 не используется
как fallback для canary. До crawl_run/SQL writer проверяются target config,
physical cluster identity и текущие enabled/PK/external_id/URL выбранных строк.
Snapshot disabled=false никогда не обходится override. Ручной RESUME сохраняет
исходные input/limit/target/policy и сверяет SQL receipts; автоматического resume нет.
Для RESUME точные зарегистрированные ID/URL допускаются и после перехода
в inactive штатным passive ingest; это не активация товара. Свежий canary
по-прежнему требует active. COMMITTED остаётся защищён точным SQL readback.

У transport — независимый dispatch guard: ≤N товарных GET, без повторного URL,
без redirects вне партии и без discovery. Robots имеет отдельный бюджет одного
GET и тот же host limiter: итого при5 карточках до6 отправок, из них товарных≤5.
Профиль:1поток, минимум20s+jitter0..0.1, timeout20s, attempts1, budget300s.
Более медленный сохранённый min interval не уменьшается. Source policies в БД
не изменяются; более быстрые профили не активируются.

Canary target жёстко ограничен127.0.0.1:55461/rc_verify_vi_20261019,
PostgreSQL17.11/system7698346631476390904 и PGDATA из partner_pilot.PGDATA.
Новая таблица/миграция/SQL CHECK не требуются. Обычные full jobs и native
rate policies сохраняют прежние контракты; они не считаются bounded canary.

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

1. Получить отдельное подтверждение именно beka_mak, REFRESH≤5 и временного
   enabled false→true→false, HTTP и SQL только в названной testDB. Проверить
   HEAD, identity/PGDATA, UTC postLIVE50 baseline,1282links, чужие клиенты,
   jobs/lease/schedules; scheduler остаётся выключенным. Создать новый backup,
   SHA256 и full decode без restore. Не использовать mainRC1/default frozen
   review connection вместо явно привязанной testDB конфигурации панели/worker.
2. До включения сохранить полный supplier11 row, enabled=false, timestamp,
   schedule state и fingerprint остальных suppliers в отдельные evidence.
   Проверить preview5: PK/external_id/URL/order совпадают с fixture/evidence.
3. Только после разрешения: под identity guard и блокировкой supplier выполнить
   compare-and-set enabled=false→true для id11/code=beka_mak, записать audit.
   Не менять rate/crawl settings, active offers, matching, schedules или другие
   supplier rows. Использовать writable подключения только этого test target,
   после identity guard; read-only default кластера не менять глобально.
4. Пересоздать preview после включения (digest меняется), подтвердить одно
   source_canary задание. Проверить immutable5 перед штатным worker с одной
   итерацией; без scheduler, автоповтора, DISCOVERY и автоматического RESUME.
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
