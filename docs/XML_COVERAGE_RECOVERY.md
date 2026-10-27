# Достоверное покрытие локальных XML — 27.10.2026

Пустой корректный XML — техническая валидация, **не бизнес-приёмка**.
Оба потока import_allowed=false; Stage6 BLOCKED_IMPORT_CONTRACT_EVIDENCE сохранён.

## Identity, поля и selection

Accepted/manual physical mapping сохраняет Product ID независимо от TTL цены.
Ни fuzzy matching, ни изменения mappings. Реальные REVIEW/CONFLICT/quarantine или
неоднозначный registry ID исключаются. Старый audit timestamp не отзывает accepted identity.
Неизменная materialization намеренно не обновляет pid.audited_at; revalidation доказывает
pm.last_validated_at/created_at после source success и согласованный durable verdict.

Цена/наличие/content имеют независимый TTL. Нужны HTTP200, exact supplier/product/offer/
region/capture identity, несинтетическое evidence. Цена положительная numeric_public с валютой.
Stale/unknown поле опускается, не заменяется0; last-known retained projection показывается
только в диагностике с датой, не возрождается поверх нового unknown observation.

Updates проходит существующий evaluate_offers. Именованный профиль
offer-selection-v1/receipt-preview-v2 использует per-card receipt вместо полного supplier
crawl/всех22фаз. Ranking, enabled/active, offer-kind, missed-crawl, quantity contradiction,
same-supplier ambiguity и currency guards сохранены. Stage5 policy не изменена.
READ_ONLY_NOT_PERSISTED plan и previous selection date попадают в manifest;
catalog_offer_selection не записывается. Отсутствие materialization не скрывает свежий offer.
Один выбранный offer на canonical ID; поля разных источников не смешиваются.
Свежа только цена — price/currency; только наличие — availability. ESOL-контракт не доказан.

## NEW staging и enrichment

OPEN-case для SAFE_NEW_PRODUCT_CANDIDATE не равен REVIEW_EXISTING. Локальный preview
требует current NEW/SAFE_NEW, совпадения штатного identity fingerprint и отсутствия human запрета.
Case не закрывается. REVIEW/CONFLICT/reopened source change/rejection/accessory блокируют поток.
Нет бренда/собственной модели/определённого kind, reference/accessory, multi-variant,
canonical collision или противоречие — нет допуска. Новых AUTO или Product ID не создаётся.

enabled=false продолжает блокировать collector/native updates selection. Read-only NEW
preview может показать сохранённого кандидата с collection_enabled=false/requires_operator_review=true.
Источник не включается, импорт не разрешается. Beka Mak TR исключён как reference-only.
Свежий content допускается с только известными полями; stale коммерция отсутствует.
needs_enrichment показывает необходимость актуализации коммерции/категорий. Сохраняются
name/brand/model/source categories/properties/media/aliases; source category не canonical import category.
NEWC не Sterbrust Product ID. Дедупликация — полный brand/model/kind + значимые properties;
служебные candidate/evidence/category поля не объявляются техническим противоречием.
Различающиеся полные source titles требуют execution REVIEW перед слиянием aliases:
одинаковый усечённый stored model не доказывает равенство base и NC/другого исполнения.

## Покрытие и NOT_READY

Manifest: все blocking reasons, одна primary reason, отдельные warnings, funnels/coverage
по11источникам, selection trace/versioned SHA256. Eligible+primary exclusions=candidates.
Alias — source witness, не вторая карточка. Каждый пустой stream NOT_READY; один положительный
даёт общий PARTIAL_NOT_READY/business acceptance NOT_READY/release_blocked=true.
Переход positive→zero без объяснения всех прежних записей запрещает публикацию.
Даже два положительных потока — PREVIEW_ONLY, никогда import acceptance.

## Воспроизведение только SELECT

Из feature checkout:

```powershell
..\sku-service\.venv-audit\Scripts\python.exe scripts\diagnose_xml_coverage.py --output ..\..\reports\XML_COVERAGE_RECOVERY_20261027\recheck --baseline ..\..\reports\XML_COVERAGE_RECOVERY_20261027\release\AFTER_MANIFEST.json
```

Exact55461 READ ONLY/local files. Нет HTTP/matching/ingest/enqueue/migration018/serving/Windows task.
Передавать последний сопоставимый manifest. Выход: SQL_FACTS/query, funnels, exclusions JSON/CSV,
все record reasons,20confirmed+20includedNEW+20excludedNEW+PP800F traces, versioned XML/hashes.
REFRESH_TARGETS_NOT_APPROVED.json — только будущие targets, не enqueue.

Реальная выборка18959rows включает все1282связи. Fresh price/availability по6ч/3ч TTL нет.
NEW подавлялись collector-enabled/любым OPEN-case/audited_at;1992ложных stale исправлены.
Результат206KAMI candidates без stale коммерции, не206созданных товаров. Updates0 NOT_READY.
121persisted selection/117selected сохранены. PP800F source4/external297 по-прежнему88585;
наблюдение от21октября внеTTL. Current legacy serializer также исключает117по price_type=unknown:
это иной срез, чем исторический117-feed. Совместимый117-row fixture Stage5 проходит;
unsupported-price-type guard не ослаблен ради числа117. Полное evidence в workspace
reports/XML_COVERAGE_RECOVERY_20261027/. LIVE11/16tablehashes/42sequences/1282links сохранены.

Следующий адресный этап: [COMMERCIAL_RECOVERY.md](COMMERCIAL_RECOVERY.md).
Извлечение/observation provenance исправлены offline; подготовлен отдельный exact
confirmed REFRESH1270 без повторного использования LIVE11 receipts. Новый LIVE ещё
не разрешён; реальный HTTP→SQL→matching→selection→XML PASS нельзя подменить fixtures.
