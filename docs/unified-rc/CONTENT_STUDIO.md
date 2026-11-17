# Content Studio в Unified RC

17.11.2026. DONE / APPLIED LOCAL PRIMARY / VERIFIED LOCAL AND FINAL LINUX RESTORE.

## Текущий локальный статус

OPERATOR-CONFIRMED: proposal2ca2f8f31c34edafdcc9fd954e07dd879d9af0c131ef84b39f8fff85105aafeb,
native PLAN b33cd4ff599ba51dd93270ea57736037664b861240c0c8611f6f215389180ed9.
APPLIED на codeHEAD80ed48e: schema/import и включение общей панели
http://127.0.0.1:58161/content-studio/. Runtime rc_runtime, source6/25/4/8 и9таблиц
точны. Legacy ADMIN-only/project_access0; source SQLite/media не удалялись.
Внешние вызовы NO; master/API keys не создавались, APIrefs не расшифровывались.

LIVE-VERIFIED LOCAL:45smoke checks, оба существующих ADMIN проходят парольный вход
с сохранённой forced-change policy. Для чтения Studio использована120секундная
подписанная QA-сессия существующего rc-review; это не доказательство его парольного
входа. Roles/passwords/session versions не менялись, пользователей не создавали.
Шесть историй,4media SHA/ZIP/preview/CSRF/external-call denial/3viewport проверены;
оригиналы не редактировались. Public business12/1282/monitoring11/XML-LKG19/proposals
точны после switch. Обычный auth audit/last_login и Studio audit ожидаемы.

Новый dump после switch восстановлен на LinuxPG17.11:81таблица/52sequence states/
1282links точны, затем44HTTP/browserchecks и два настоящих ADMIN password login.
Runtime не owner/superuser, legacyACL0. Старые dump/code/QA пакеты не перезаписаны.
Code execution/QA HEAD80ed48e; последующее обновление документации не меняет engine.

Выявленная разница native receipt/general ledger закрыта отдельно утверждённой
reconciliation: proposaldf3fe8dfe18b9349526574240cfbbab35676b332d7b767c3f86df7488e90be04,
ровно1INSERT в public.schema_migrations для020с неизменным
SHA1fbbcc94c77c2f487ea8cec57080070947f745934d8bf45288c889e3e1e6f24e.
DDL/import не повторялись; все20ledger hashes совпадают с файлами. В LinuxQA
общий runner проверил20/20без повторного создания Studio;9таблиц/1282точны.
Для будущего нового server bootstrap общий мигратор применяется до data import,
а native importer использует уже созданную схему (новый plan с apply_schema=false).
Автоматическая регистрация ledger в самостоятельном --apply-schema остаётся
отдельным улучшением tooling, не незакрытым состоянием текущей БД.
Реальный deployment и свежие коммерческие XML ещё не выполнены. Доказательства —
workspace reports/CONTENT_STUDIO_INTEGRATION_XML_20261116/resume-20261117/.

REPOSITORY-VERIFIED: native `/content-studio/`, общий SessionSigner/app_users/
session_version/CSRF, action permissions и project ACL. До отдельной миграции
основной БД — migration-required503, без пустой Studio или SQLite fallback.
Исходная `work/content-studio` не меняется. Это функциональная интеграция,
проверенная на QA и теперь активированная в основной локальной панели.

## Данные и права

Migration020 и importer сохраняют9таблиц, IDs, payloads, revision hashes и медиа.
Legacy материалы ADMIN-only до явной owner map; первый вошедший не получает их.
Новые проекты получают ACL создателя в той же транзакции. Старые limited-профили
не получают новые права автоматически. ADMIN управляет секретами; editing/media/
export/AI/VK проверяются отдельно. Runtime rc_runtime не owner/superuser, без CREATE
в content_studio и без доступа к migration_receipts. Конструктор не выполняет DDL.

## Сервер и секреты

Dockerfile.server включает Content Studio/static/CLI; UID10001. compose.release.yml
по умолчанию CONTENT_STUDIO_ENABLED=NO, CONTENT_STUDIO_EXTERNAL_CALLS=NO.
Нет автоматической миграции, scheduler, VK, AI или сбора сайтов.

- CONTENT_STUDIO_DATA_DIR=/app/content-data: mount runtime-data/content-studio,
  persistent media, доступ UID10001; SQLite fallback отсутствует.
- CONTENT_STUDIO_SECRETS_DIR=/app/content-secrets: mount runtime-secrets/content-studio,
 0700, encrypted refs0600, доступ UID10001.
- CONTENT_STUDIO_KEY_FILE=/run/secrets/content-studio.key: отдельно предоставленный
 master key0400/0600. Не создаётся запуском и не входит в ZIP/Git/БД.

DPAPI refs сохраняются как история, Linux требует повторного подключения.
Ключи не расшифровывались и не переносились. Включение UI не разрешает реальные
внешние вызовы: CONTENT_STUDIO_EXTERNAL_CALLS остаётся NO до отдельного допуска.

## Перенос и откат

scripts/migrate_content_studio.py по умолчанию PLAN_ONLY. Нужны согласованный source
snapshot, private target config, owner-map либо explicit legacy-admin-only,
точные database/system identifier и persistent media path. Hash включает source
rows/media, target и owner/schema policy. Изменение параметров требует нового плана.
Primary apply требует одновременно --apply --allow-primary --confirm-plan-sha256
с отдельно одобренным SHA. Один --apply-schema без --apply не выполняет миграцию.

Повтор — ALREADY_IMPORTED_NOT_REPLAYED; другой owner plan или непустой target
не перезаписываются. Ошибка файла откатывает schema/rows/receipt и удаляет только
новые файлы данной попытки. VK replay отсутствует. Потеря dispatch lease переводит
SENDING в UNCERTAIN только когда другой worker не держит lease; повтор запрещён.

После отдельного включения проверить общий login/обе ADMIN, forced-change/revoke,
limited ACL,6legacy материалов/25редакций/4медиа/8файлов, CAS200/409, ZIP и sandbox
preview. До/после сверить1282/business/news/XML/LKG. SQLite/снимок не удалять.
При сбое выключить Studio и сохранить данные для разбора; не восстанавливать всю
общую БД поверх новых живых данных. Основная миграция этим этапом не выполнялась.

## QA и реальные ограничения

LIVE-VERIFIED ISOLATED QA: Linux PostgreSQL17.11, перенос6/25/4/8 с hashes,
runtime-only web, ACL/CAS/media/export/revocation, rollback/repeat/leases/vault modes.
Chromium: общий login, prefix, PG reload/HTML/ZIP,1440/960/390px и browser module в
отдельном QA-профиле с intercepted fixture; source/VK/AI HTTP0.
REPOSITORY-VERIFIED:1026LinuxPASS/4SKIP,12Windows OS-monitoring PASS,
4PostgreSQL integration tests PASS. Точные receipts/logs — workspace
reports/CONTENT_STUDIO_INTEGRATION_XML_20261116/resume-20261117/.

Браузерный RED: no-referrer давал Origin:null для form POST. Исправлено на
same-origin; off-site referrer не передаётся, Origin:null/чужой origin всё ещё403.
Проверка не ослаблена. Browser module допускает четыре owned HTTPS домена,
точные локальные paths58161/58162 и standalone58206/58207. В пользовательский
браузер не устанавливался.

Шесть Partner source IDs15/24/25/35/117/193 исключены из свежего экспорта по
конкретному предложению. Другие поставщики того же Sterbrust ID допустимы;
LKG/история не перезаписываются.52Vekprom не применены,9+478REVIEW сохранены.
Primary migration/switch APPLIED LOCAL. Свежие коммерческие XML, новые LIVE grants,
реальный server/deployment/импорт NOT_APPLIED. RELEASE_READY=NO до этих gates
перед deployment. Учёт020в текущем primary general ledger уже согласован/проверен.
