# YouMet operator-assisted public capture / 2026-10-12

**YOUMET_OPERATOR_CAPTURE_READY — STOP.**
Реальный операторский HTML/DOM ещё не предоставлен. Статус
YOUMET_PUBLIC_BROWSER_BASELINE_VERIFIED не присваивается по тестовым данным.

OPERATOR-CONFIRMED: вложение7792160c; source youmet / primary youmet.ru;
saved HTML, browser Save page HTML и Copy outerHTML; без cookies/session/profile,
без обхода доступа, без scheduler/AI, без чтения остальных сайтов.
Предыдущий автоматический DOM используется только для исследования структуры,
не преобразуется в OPERATOR_BROWSER_CAPTURE.

DONE: offline importer, общий YouMet adapter/normalized Page+Item pipeline,
инертная очистка HTML, явные URL/время захвата, provenance/hash, исходная навигация,
раздельные NEWS/BLOG, prices/discount mechanics, CLI и generic report integration.
APPLIED: код и документация в feature workspace. Реальная migration/import YouMet
не применены:0 операторских исходных captures и0 постоянных source-изменений.
VERIFIED: REPOSITORY-VERIFIED offline fixtures и настоящая PostgreSQL QA во
временной rollback-схеме отдельной competitor DB; не LIVE-VERIFIED YouMet baseline.

## Проверка

- 175 тестов PASS;150 предыдущих и25 новых. Один старый изменяющий source SQL
  fixture исключён; вместо него отдельная rollback-only QA нового импортёра.
- PostgreSQL fixture: первая версия создаётся; повторный HTML не создаёт
  версию/ledger; изменённый HTML создаёт следующую; более старый capture отвергается.
- Первый снимок создаёт только BASELINE_OBSERVATION. NEW_PRODUCT/NEW_PROMOTION/
  PRICE_DECREASE/PRICE_INCREASE из первой observation не создаются.
- Независимый импорт NEWS/BLOG, actual URL discovery, PUBLIC_BROWSER_EVIDENCE,
  per-row CSV provenance, сохранение первоначального HTTP403 после повторного
  импорта, очищенные входные secrets и неизменность исходного файла проверены.
- Синтетический HTML в Edge:8 проверок PASS; operator provenance и INITIAL
  BASELINE видны, прямой автоматический доступ обозначен неполным; desktop/mobile
  без горизонтального переполнения/JS errors. Все HTTP(S) запросы заблокированы.
- После rollback все fingerprints реальных таблиц/источников полностью равны;
  временная схема отсутствует. Ни один fixture не импортирован в public schema.

PACKAGED-EVIDENCE: feature reports/YOUMET_OPERATOR_CAPTURE_SUPPORT_2026-10-12/:
TEST_RESULT.xml, IMPORT_QA.json, FIXTURE_UI_QA.json, явно помеченные SYNTHETIC_INPUTS,
FIXTURE_CAPTURE_OUTPUTS и FIXTURE_PREVIEW. Синтетические файлы не являются
свидетельством о реальных ценах/акциях YouMet и не входят в общий бизнес-отчёт.

## Контракт

CLI: `python -m competitor_intel --dev-config competitor-data/dev-config.json
import-browser-capture --source youmet --url ... --file ... --capture-time ...`.
Полный пример: YOUMET_OPERATOR_CAPTURE_GUIDE.md.
Время должно быть исходным ISO8601 с timezone; нет fallback на mtime/import time.
UTF8, UTF16 с BOM и объявленный legacy charset поддерживаются; сохранённый UTF8
outerHTML корректно читается даже при старом windows-1251 meta.

Capture ledger связывает source/page-version с original_url, capture_time,
capture_method=OPERATOR_BROWSER_CAPTURE, content_hash очищенных UTF8 bytes,
source_domain=youmet.ru, fetch_status=OPERATOR_CAPTURE, PUBLIC_BROWSER_EVIDENCE.
input_sha256 оригинальных bytes сохраняется отдельно; сами исходные bytes,
resource folders, browser headers/profile/cookies не копируются.
Page payload/observation transport и HTML/CSV также сохраняют происхождение.
Версии этого канала отделены от автоматического extractor lineage, чтобы смена
происхождения сама по себе не стала дневным изменением.
Регион читается из фактически наблюдавшегося current-city элемента. Снимки
разных или неизвестных регионов не устанавливают сопоставимую историю цен.

Защитные/authenticated HTML и HAR/JSON отвергаются. Скрипты/формы/активные ресурсы,
comments, посторонние атрибуты/секретные параметры очищаются до persistence.
Неизвестные brand/model/category/availability/currency/date не выдумываются.
Одинаковый очищенный DOM при другом import/capture time не создаёт новую версию.
Оригинальный файл нельзя перезаписать через выбранную output directory.

Схема расширяет generic vocabulary OPERATOR_CAPTURED/OPERATOR_CAPTURE/
PUBLIC_BROWSER_EVIDENCE и добавляет competitor_operator_captures только при
явном импорте. Автоматический 403 и robots_disallowed сохраняются независимо.
Обнаруженная ссылка подтверждает навигацию, не содержимое дочерней страницы:
она остаётся DISCOVERED_NOT_FETCHED до отдельного operator capture.
После первого принятого capture CLI автоматически обновляет один generic HTML
на6источников; до такого capture текущий реальный отчёт не перегенерируется.

KAMI404 ERROR / ACTUAL_ACCESS_FAILURE сохранён без запроса/удаления и по новому
OPERATOR-CONFIRMED правилу не блокирует KAMI onboarding. Source_HTTP0 для всех
сайтов; source data writes0; AI0/scheduler0; Universal Supplier/KAMI/Vekprom/
Sterbrust/ESOL/production untouched; develop merge0. Feature-only publication.

Следующий шаг: один публичный HTML/DOM YouMet + исходный URL + capture time.
После его отдельной QA/import: YOUMET_PUBLIC_BROWSER_BASELINE_VERIFIED и
обновлённый общий HTML. Сейчас STOP в состоянии готовности импортёра.
