# Мониторинг конкурентов — локальная версия / 12.10.2026

COMPETITOR_MONITORING_UI_LOCAL_READY — STOP.

## 1. Реализовано

DONE/APPLIED локально: настоящая вкладка основного FastAPI/Jinja приложения,
общая навигация и дизайн-система. Старый generated HTML не встроен через iframe.
Read-only слой использует существующие normalized_dataset, build_facts и
change_detection. Нет новой параллельной БД/ingestion/parser архитектуры.
VERIFIED: локальный браузер, профильные unit/API tests и read-only PostgreSQL.
Операторское визуальное принятие ещё не получено.

## 2. Экраны

Обзор: период/обновление/6источников/события24ч и7дней, компактные7KPI,
детерминированное «Требует внимания», карточки конкурентов, новости/блог.
Изменения: таблица50строк, период сегодня/7/30/custom, конкурент, тип, бренд,
поиск; человеческие подписи, цены/дельты/источник. JSON API также ограничен50.
Цены: настоящие снижения/рост между наблюдениями и сортировки по%/сумме/дате.
Пока сравнений нет — пустое состояние, а не выдуманные скидки за день.
Акции:29сохранённых PROMOTION/CAMPAIGN, известные/неизвестные сроки.
NEWS/BLOG не смешаны с акциями. Предложение с дедлайном из новости показано
отдельно в «Требует внимания», не выдаётся за NEW_PROMOTION.
Ассортимент:26строкбрендов, только наблюдавшиеся уникальные товары конкурентов.
«Сравнение со Sterbrust пока недоступно»;исторические5264seedв количества не входят.
Источники: AUTO/MANUAL, обновление/последнеесобытие/страницы/товары/регион,
capture method/evidence/HTTPошибки под expandable diagnostics.

## 3. Данные и read layer

Только локальная competitor DB127.0.0.1:55454 / sterbrust_competitor_intel.
READ ONLY / REPEATABLE READ, явные столбцы, только accepted текущая extractor
lineage, bounded history/items,30srequest-driven cache,безN+1SQL. Frontendне
получает всю историю,rawHTML,seedpayload или десятки тысяч строк.
Аудит:98pages/347versions/11428storeditems/461observations;98acceptedlineage
versions/471201normalizedcharacters; текущие2993карточныхконтекста.
Лимиты:1000pages/versions,20000itemcontexts,5000observations,500diagnostic/capture
rows;превышение fail-closed. Новые indexes/migrations не потребовались.
Source данные и все реальные table fingerprints равны до/после QA.

## 4. Intentionally не реализовано

UI-upload HTML:disabled action,работающий CLI importer не переделан.
Нет Sterbrust/Vekprom подключения/сравнения,matching/commonSKU,полныхкаталогов,
AI/новогосбора/scheduler/cron. Нет нового BLOG event type — backend такого
события не предоставляет;BLOG материалы отдельно. Не придумывается refresh
cadence: без объявленного расписания старый capture не становится CRITICAL.

Локальный review-профиль использует тот же application factory/base/routes,
но supplierDB не подключается. Остальные меню показывают понятное уведомление
о scope; записи/сбор/импорт заблокированы. Это профиль просмотра feature,
не изменение production runtime/config.

## 5. YouMet и KAMI

YouMet: MANUAL / «Ручной мониторинг», Anti-bot / HTTP403,
Operator HTML Capture, последнийснимок «Не предоставлен»,baseline
«Не подтверждён». YOUMET_PUBLIC_BROWSER_BASELINE_VERIFIED не присваивался.
Реальный operatorcapture0;fixturesне попадают в бизнесовые показатели.
KAMI: «Работает», отдельный404 /spec/skidka-do-30-kley/ сохранён как
ERROR / ACTUAL_ACCESS_FAILURE;не удалён/не перечитан и не блокирует источник.
Другие sourceданные/YouMetHTTPfailure/captureledger/provenance не изменены.

## 6. Tests / ограничения regression

Профильныйgate:185PASS =175ранее принятыхcompetitor tests +10новыхread/API/UI
проверок;1старыйизменяющийsourceSQLfixtureисключёнбезослабленияassertions.
PostgreSQL QA:6PASS,всеtablefingerprintsравны,SourceHTTP0/datawrites0.
NativeEdgeQA:27PASS на1920×1080 и1366×768:вкладка,6источников,фильтры,
таблицы,empty/validationstates,YouMetmanual,KAMI404,нетdocumentoverflow/JSerrors.
Отдельныйявнопомеченныйfixturelocalhost:12PASS:PRICE_DECREASE−14%,NEW_PROMOTION,
длинныеназвания/URL,sourceERROR,databaseunavailable. Fixtureсерверостановлен;
реальнаяDBнеизменена. Всеnon-localHTTPблокируются.

Расширенныестарые supplier suites проверены дополнительно:41PASS/31FAIL на
неизменённомHEAD и тотже31FAILпослеUIизменений;новыхfailures0. Причины:
отсутствующиеignoredOPTIMUM_IDENTITY_AUDIT.csv/PARTNER_ST_IDENTITY_AUDIT.csv/
SUMMARY.txt и ранее существующее ожидание порта58080 в admin-previewcompose.
Это не зелёный полныйlegacy-suite. Assertions/productionconfig/fixturesне
подменялись радиPASS. Evidence:BASELINE_REGRESSION.xml/.log,
LEGACY_BASELINE_COMPARE.json. Профильныеaccepted175testsостаютсяPASS.

## 7. Локальный запуск

UI уже запущен скрыто наloopback,PID20676. Ниworker,ниscheduler,ниDocker,
нидругаяБДне запускаются. ЗакрытиеCodex/ПКможет завершитьлокальныйсервер.
Для повторного запуска из feature workspace:

```powershell
& '.\.venv\Scripts\python.exe' -m scripts.run_competitor_monitoring_ui
```

Зависимости existingrequirements установлены только в локальную .venv.
Launcher не запускает PostgreSQL;при недоступной competitorDB показывает
понятное empty/error state без traceback. Ужеинициализированный competitor
cluster обслуживается существующей локальной инструкцией отдельно.

## 8. URLs

http://127.0.0.1:58124/ → основное приложение / вкладка
http://127.0.0.1:58124/competitor-monitoring
http://127.0.0.1:58124/competitor-monitoring/changes
http://127.0.0.1:58124/competitor-monitoring/prices
http://127.0.0.1:58124/competitor-monitoring/promotions
http://127.0.0.1:58124/competitor-monitoring/assortment
http://127.0.0.1:58124/competitor-monitoring/sources
http://127.0.0.1:58124/api/competitor-monitoring/changes

## 9. Git safety

Branch: feature/competitive-intelligence-kuvalda-metalmaster
HEAD: b24a6aab14b4b861645db849564d62c2bed76b9d (не менялся).
Commit0/push0/merge0/PR0/deploy0. Изменения оставлены локально для просмотра.
Remote featureb24a6aa/develope63eb6fe/production36924836 совпадают с исходным
checkpoint; Git только читался. Production/develop/config/DB не изменены.

Git status / полный список изменённых файлов:

```
 M universal_supplier/control_plane/templates/base.html
 M universal_supplier/control_plane/web.py
?? competitor_intel/monitoring.py
?? docs/COMPETITOR_MONITORING_UI_AUDIT.md
?? docs/COMPETITOR_MONITORING_UI_LOCAL_REPORT.md
?? scripts/qa_competitor_monitoring_local.py
?? scripts/qa_monitoring_fixture_visual.py
?? scripts/qa_monitoring_readonly.py
?? scripts/run_competitor_monitoring_ui.py
?? tests/test_competitor_monitoring_ui.py
?? universal_supplier/control_plane/competitor_ui.py
?? universal_supplier/control_plane/static/competitor_monitoring.css
?? universal_supplier/control_plane/templates/competitor_monitoring.html
?? universal_supplier/control_plane/templates/monitoring_local_scope.html
```

SourceHTTP0 / AI0 / scheduler0 / productionwrites0 / sourcewrites0.
UniversalSupplier/KAMI/Vekprom/Sterbrust/ESOL storage/worktree state untouched.
Knowledgebase report/task/spec сохранены отдельно в корневом workspace.
Следующее разрешённое действие — операторский визуальный review. Публикация
требует отдельного явного разрешения. STOP.
