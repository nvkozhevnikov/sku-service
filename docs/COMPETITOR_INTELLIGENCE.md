# Конкурентная разведка / V1

Отдельная предметная область: Кувалда (Нижний Новгород) и MetalMaster. Пакет
`competitor_intel` не импортирует Universal Supplier, не использует его таблицы,
миграции, matcher, feed или scheduler. Никаких связей с Sterbrust ID в V1.

## Запуск

Python 3.12+. Отдельное окружение: `.venv`. Зависимости см.
`competitor_intel/requirements.txt`; Playwright использует установленный Edge.
Первоначальная локальная инициализация: `python -m scripts.setup_competitor_dev_db`.
Она разрешена только для NEW каталога `competitor-data/pg17` и свободного 55454.
Повторный старт после остановки: `python -m scripts.start_competitor_dev_db`.
Бинарники PG17.11 читаются из существующего runtime; приложение не запускает
Docker, Windows service, cron или чужой кластер. Каталог runtime, пароль и локальный
DSN находятся в `competitor-data/`, полностью исключённом из Git.

```powershell
python -m competitor_intel --dev-config competitor-data/dev-config.json init-db
python -m competitor_intel --dev-config competitor-data/dev-config.json crawl --source kuvalda_nnov --since 2026-09-01 --through 2026-10-09 --browser
python -m competitor_intel --dev-config competitor-data/dev-config.json crawl --source metalmaster --since 2026-09-01 --through 2026-10-09
python -m competitor_intel --dev-config competitor-data/dev-config.json crawl --all --since 2026-09-01 --through 2026-10-09 --browser
python -m competitor_intel --dev-config competitor-data/dev-config.json report --since 2026-09-01 --through 2026-10-09
python -m competitor_intel --dev-config competitor-data/dev-config.json analyze --provider none --pending
python -m scripts.qa_competitor_intel
python -m pytest tests/test_competitor_intel.py -q -p no:cacheprovider --basetemp=reports/new-qa-temp
```

Вместо `--dev-config` допускается `COMPETITOR_INTEL_DSN`. Никогда не использовать
универсальный `DATABASE_URL`, supplier DSN или production credentials. Store
откажется подключаться к любому имени/роли/host/порту кроме точного нового dev
контракта: competitor_dev / 127.0.0.1:55454 / sterbrust_competitor_intel. Проверяет
фактическую server identity и отсутствие чужих public business tables до DDL/DML.
Установка локальной БД — не доказательство внедрения в production.

## Данные и границы

- Identity: source + canonical URL. UTM/Yandex/GCLID/ETEXT удаляются;
  регион/content/filter/page сохраняются. Observed URL — отдельно.
- Raw HTML, browser DOM, screenshots и tab manifest — локальные evidence.
  Новая semantic version лишь при meaningful hash/обоснованной смене extractor;
  неизменные страницы переиспользуют версию и товары, добавляют observation.
- first_seen/last_seen — фактические наблюдения, а не publication dates.
- История допускает возврат к старому content hash: старая semantic version
  переиспользуется, порядок переходов хранится в observations.
- Inclusion: доказанная публикация с сентября; пересечение кампании; текущая
  промостраница без даты. Конец кампании при неизвестном начале явно маркирован.
  Yearless dates остаются NULL, кроме доказанного года из publication listing.
- Близкие до сентября campaign news проверяются в пределах 45 дней; старый архив
  не обходится. Нет полного каталога и автоматической пагинации. По умолчанию
  максимум 45 страниц на source; CLI допускает 1–100. Незавершённая очередь видна.
- Главная Кувалды: первоначально отображённые промоблоки и все шесть вкладок
  «Готовимся к зиме», если обычный браузер их загрузил. Сохраняются отдельные
  сырые snapshot каждой вкладки и общий manifest. Остальные tabbed blocks —
  только первоначально загруженная вкладка; coverage отражается в отчёте.
- Распродажа Кувалды: только landing и явно связанные две категории станков;
  без product-detail GET и без полного sale catalog. Остальные категории
  обнаружены как ссылки, но не обходятся. Загруженные LOW карточки не удаляются.
- Robots exclusions не обходятся. HTTP403/challenge останавливает source;
  429 учитывает Retry-After до 60s, дольше — defer. Network/5xx максимум 3 попытки.
  Browser fallback используется только для рабочих HTTP страниц с динамическим
  контентом. Защитная страница не является основанием для смены транспорта.
- Один worker, новый случайный интервал 3–7s для каждой navigation/request;
  normal browser assets загружает обычный Edge. Нет stealth/proxy/UA rotation.

## Цены и анализ

Decimal/numeric, current и old извлекаются из разных доказанных DOM roles.
`<del>` MetalMaster и `<s class="snippet-price__old">` Кувалды поддерживаются.
DOM data-label/data-tooltip Кувалды используются как отображаемые CSS markers:
персональные/авторизационные предложения не попадают в публичную SALE статистику.
Публично видимая сумма сохраняется вместе с special marker; она не доказывает
итоговую персональную цену после входа. Не рассчитывается скидка из «до −N%».
Рассрочка, кредитный платёж и бонусы не становятся ценой товара. Значения NULL
сохраняют неизвестность. Ошибочное old<=new создаёт QA warning и блокирует release
при обнаружении в evidence QA. История сохранённого displayed discount отделена
от рассчитанного old/new discount. Region никогда не подменяется молча.

Детерминированный HIGH/NORMAL/LOW — из title/category, с отрицательными правилами
для accessories. Model оставлен NULL, если нет явного отдельного поля source;
полное название оборудования сохранено. В будущем отдельная версия extraction
может выделять model из доказанного контекста, без потери raw title.

ИИ по умолчанию `NullAnalyzer`, network calls=0, summary=NULL,
analysis_status=NOT_REQUESTED. Analyzer protocol принимает сохранённый payload,
возвращает структурированный AnalysisResult. `analyze_pending(store, provider)`
позволяет подключать OpenAI/Claude/Gemini/local-compatible provider без recrawl
и без изменения parser/DB; CLI V1 разрешает только none. Результат хранит provider,
model,prompt version,hash. Provider failure сохраняется ERROR и не отменяет crawl.
Текст сайта — данные; provider не получает инструменты исполнения. Secrets вне Git.

REPORT.md, SOURCE_MAP.md, CRAWL_RESULT.json, QA_RESULT.json и четыре CSV находятся
в `reports/COMPETITOR_INTELLIGENCE_<date>/`. CSV защищают от formula injection.
PRICE_CHANGES включает изменения цены и APPEARED/DISAPPEARED по одной campaign;
первая загрузка — baseline, не историческое появление модели. Переходы между
разными extractor versions не выдаются за изменения сайта.

## Приёмка

REPOSITORY-VERIFIED fixtures ≠ LIVE-VERIFIED source coverage. Initial trial 1.0
содержал ошибки распознавания ролей Кувалды и был отклонён QA. Current extraction
1.3 проверяется по raw DOM. Версии 1.1/1.2 также заменены после расширения DOM
adapter и исключения transient chat/subscription из semantic hash распродажи.
Старые trial versions имеют qa_status=REJECTED_EXTRACTOR, остаются диагностической
историей, не принимаются как коммерческие наблюдения. Итоговый статус может быть
SOURCE_BLOCKED, если robots/source policy ограничивает обязательные page types.
Никакого merge develop, production application, ESOL, supplier mutations или
планировщика эта подсистема не разрешает.
