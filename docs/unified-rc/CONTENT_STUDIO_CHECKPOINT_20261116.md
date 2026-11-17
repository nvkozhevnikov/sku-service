# Content Studio + XML — рабочая точка продолжения, НЕ готовый релиз

17Nov follow-up: продолжение code/isolated QA завершено. Актуальная инструкция —
[CONTENT_STUDIO.md](CONTENT_STUDIO.md). Ниже сохранён исторический checkpoint.
Primary migration/switch по-прежнему требуют отдельного разрешения.

16.11.2026. Оператор попросил за10минут зафиксировать работу перед выключением ПК и продолжить завтра. Ветка feature/sterbrust-autonomous-system-20261026, исходный HEAD875de69132bad7a38567941a8b6c133bf3567142. Зафиксированная реализация ниже остаётся IN_PROGRESS; не развёртывать и не считать общей БД уже переключённой.

## Что сохранено

- Существующая Studio перенесена в content_studio/, исходная work/content-studio не менялась. Сохранены HTML/тексты/правила/ИИ/ВК/медиа/история/preview и исходные276регрессий в tests/content_studio/.
- Native mount /content-studio/ в общей панели: общий SessionSigner/пользователи/session_version, CSRF, namespace/action permissions и проектные ACL. Новые права не выдаются старым limited-профилям автоматически. Отдельного integrated-логина/iframe нет.
- PostgreSQL adapter + migration020_content_studio.sql,9исходных таблиц и отдельные access/audit/receipt таблицы. Схема не создаётся на старте. Исходный SQLite/DPAPI не используется как скрытый fallback.
- explicit plan/apply CLI scripts/migrate_content_studio.py с точной идентичностью БД/source/approval SHA, защитой от перезаписи/повтора и rollback только своих новых медиа. По умолчанию dry-run. Основной target требует отдельного allow-primary и точного confirm-plan; ЭТО ЕЩЁ НЕ РАЗРЕШЕНО.
- Linux secret-store на Fernet, master key вне БД/пакета. Старые DPAPIrefs сохраняются, но показывают необходимость повторного подключения. Ключи не расшифровывались.
- Шесть Partner-ST source IDs15,24,25,35,117,193 блокируются в native eligibility/preview/validation по конкретному предложению, не по Sterbrust ID. Остальные поставщики того же товара могут быть выбраны. Связи и LKG не менялись;52Vekprom proposal не применён,9+478REVIEW сохранены.

## Проверено к checkpoint

Windows:438PASS/1SKIP/1Starlette warning — auth/accounts/session, XML/continuity/Stage5/automation и276перенесённыхStudio тестов. Синтаксис9JS проверен node --check. Исправлен устаревший строковый source-inspection тест session_version: теперь проверяет чтение/отзыв сессии по поведению. Неизменённый admin_store.py не переписывался.

Реальный перенос9таблиц/6проектов/25редакций/4медиа/8файлов проверен в **studio_integration_qa_windows_20261116_a** на существующем локальном PostgreSQL17.11. Повтор: ALREADY_IMPORTED_NOT_REPLAYED. Основная rc_verify_vi_20261019 не мигрировалась. Это отдельная разрешённая QA-база, не второй production PostgreSQL.

20реальных shared-session/PG smoke checks: общий login, только RCcookie, CSRF/запрет встроенного входа,6исходных проектов, native base-path, PGcreate/save, конкурентный CAS200/409, фото, HTML/style/CSP, окноpreview, история/ZIP, двеworkerlease. Проверка использовала **QA owner connection**, потому что существующий pg_hba правильно запрещает rc_runtime другую БД. pg_hba/пароли/глобальные роли НЕ менялись. Это НЕ доказательство least-privilege runtime роли; её надо проверить в отдельном LinuxQA. Синтетический проект/фото удалены только из QA, оригинальные6 не правились.

Linux/Python3.12 network-none контрольный запуск выполнялся; точный финальный результат смотреть в workspace report, не переносить старый неуспешный лог как текущий. Новых реальных VK/AI/site/supplier вызовов нет.

## Что НЕ завершено — следующий сеанс

1. Довести полный shared-auth/ACL набор негативных тестов, включая inactive/forced-change/revocation и прямые IDs/экспорты/preview; UI-проверку на общем origin и ширинах.
2. Полный PostgreSQL/Linux restore/import через runtime rc_runtime в **изолированном** контейнере/сервисе; не ослаблять pg_hba основной системы. Проверить rollback/параллельный dispatch/crash UNCERTAIN/role grants.
3. Dockerfile.server/COPYcontent_studio, persistent media и server-secret config, полный server entrypoint/standalone portability. Эти release Dockerfiles ещё не адаптированы. Образ Dockerfile.content-studio-qa — только окружение тестов.
4. Адаптировать browser_import для localhost58161/общего prefix, не расширяя доступ на все сайты и не устанавливая модуль в пользовательский браузер без решения. Исходный модуль пока58206/7root.
5. Завершить code/security review и общую supplier/matching/XML/monitoring регрессию. Не повторять18959matching, не применять52/9/478.
6. Собрать полноценный релизный пакет. Текущий пакет — WIPcheckpoint, а не RELEASE_READY.
7. Только после отдельного разрешения — финальный согласованный SQLite snapshot, явная owner policy (legacy покаADMIN-only, не первому попавшемусяпользователю), точный plan SHA, primary migration020/data import и переключение CONTENT_STUDIO_ENABLED. Основная панель не перезапускалась этим checkpoint, её запущенный процесс ещё может обслуживать старый код. Не включать записывающий Studio backend на основной БД автоматически.

## Где продолжать

Полный workspace progress/evidence:
`D:\documents\personal\ChatGPT\Стербруст\reports\CONTENT_STUDIO_INTEGRATION_XML_20261116\PROGRESS.md`

Root task:
`D:\documents\personal\ChatGPT\Стербруст\tasks\active\2026-11-16_CONTENT_STUDIO_INTEGRATION_XML.md`

Изолированный source snapshot/restore/config/media: `reports/CONTENT_STUDIO_INTEGRATION_XML_20261116/private-qa/` в workspace root; это **приватные тестовые данные**, не Git/публичныйZIP. SOURCE_BEFORE.json, PRIMARY_BEFORE.json, PRIMARY_CHECKPOINT.json, WINDOWS_MIGRATION_* и WINDOWS_SHARED_PG_SMOKE.json — безопасные fingerprints/receipts рядом. Before-read подтвердил1282/12business/11monitoring/19XML/active0/schemaNULL. Финальное сравнение — в checkpoint report.

Старый handoff остаётся картой всех исходников/приёмки. Старые данные не удалять, mainDB/production/develop/cron/ESOL не менять. На продолжении сначала проверить текущие ветку/HEAD/dirty state и отчёт, не начинать с нового полного анализа.
