# Public supplier TLS — 29.10.2026

## Поправка после первого реального запуска 29Oct

Первое исправление167d3cf было неполным: urllib.HTTPSHandler создавал Windows
default context до PublicHTTPSConnection. Поэтому certifi-контекст connection
не использовался фактическим opener. У job169/run60 один robots network attempt
завершился URLError/SSLCertVerificationError, verify_code19; товарных requests0,
ingest0, CP terminal0/frontier320/inflight0. Это доказанный дефект первого fix.

PublicHTTPSHandler теперь получает certifi context явно до base constructor.
CERT_REQUIRED/hostname проверяются до и после constructor, включая base options.
Offline regression проверяет фактический build_opener и путь
UrllibPublicHttpClient.get_validated, не только connection. Manifest3deec0c...
использован; повтор требует нового exact HEAD/SHA. Остальные guards сохранены.

Основной Unified RC сохраняет одну БД127.0.0.1:55461/rc_verify_vi_20261019,
system7698346631476390904. Поставщики public/rc_runtime; конкуренты
competitor/competitor_runtime. Интеграция52f3ece и receipt reuse08fdd5d сохранены.

## Исправление

Воспроизведённый28Oct отказ Intervesp verify_code19 был вызван отсутствием
SSL.com TLS RSA Root CA2022 в default CA set этого Windows/Python runtime.
Независимый установленный certifi2026.7.22 содержит exact root SHA256
8faf7d2e2cb4709bb8e0b33666bf75a5dd45b5de480f8ea8d4bfe6bebc17f2ed.
В отдельно разрешённой28Oct TLS-only диагностике та же цепочка/hostname прошли
CERT_REQUIRED проверку; это historical evidence, а не новый HTTP29Oct.

public_connection.public_tls_context теперь явно загружает существующий certifi
bundle. PublicHTTPSConnection/urllib и public_async_transport/httpx используют
этот context. Requests продолжает свой прежний certifi verified contract.
CERT_REQUIRED и check_hostname обязательны, supplied unverified context отвергается.
Missing/unreadable bundle даёт STOP, fallback на CERT_NONE/пустое хранилище отсутствует.
CA не устанавливаются в Windows, peer root не становится доверенным автоматически.
public_tls_identity даёт version/SHA256/verification flags для exact approval.

SSRF/public DNS→literal-IP pinning/SNI, redirect scope, robots, host rate limits,
SQL/identity/checkpoint guards не изменены. Никакого глобального SSL monkeypatch,
нового HTTP client/scheduler/queue или запуска парсеров в UI.

## Приёмка этапа1

146 focused offline PASS, включая exact root membership, hostname/cert enforcement,
missing CA fail-closed, SNI, SSRF, unsafe redirects, source-local robots failure,
receipt reuse и интеграционный role/runtime regression. Network/SQL test doubles
не являются реальным supplier HTTP доказательством. Первый новый robots/product
HTTP допускается только внутри следующего separately approved execution window.

Свежий29Oct primary SELECT подтвердил:1282exact, старые3009 и новые597 durable
receipts,353Q/320NOT_REQUESTED,active0/settingsrestored. Таблицы:source_products18959,
catalog_products59427,product_matches25905,review_cases17673,captures23473,
observations23473,history3784,jobs167,runs52. В этом этапе SQLbusinesswrites0.

Evidence/task: workspace reports/UNIFIED_SUPPLIER_COMPLETION_20261029/ и
tasks/active/2026-10-29_UNIFIED_SUPPLIER_COMPLETION.md.
Stage6 import guard остаётся BLOCKED_IMPORT_CONTRACT_EVIDENCE.
