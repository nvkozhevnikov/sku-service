# Минимальная коммерческая проверка — подготовка без запуска

17.11.2026. Base090cc682. DONE разбор сохранённых ответов; LIVE NOT_APPLIED,
новые запросы только после отдельного подтверждения точного SHA/окна/HEAD.

PACKAGED-EVIDENCE + REPOSITORY-VERIFIED OFFLINE PARSE: Partner-ST PP-U3, stableID293,
артикул502502, цена106083RUB, наличие и количество17 из ответа200от16.11.2026.
SHA HTML/исходный capture fingerprint проверены. Native observation восстанавливается
с оригинальным observed_at, не с текущим временем. SQLcapture0. Это техническая
возможность восстановления в отдельном согласованном scope, не выполненный перенос
и не свежая цена. Розничный/иной тип цены явно не назван.

Partner PP-600F имеет UNCERTAIN без полного подтверждённого ответа: восстановление
не доказано; автоматический повтор запрещён. Шесть Optimum500 содержат страницу
ошибки скрипта Bitrix, а не карточки. Коммерческих значений нет. Checkpoint/STOP/
старый consumed grant сохраняются.

## Новый scope

Purpose release-commercial-verification/v1 использует те же Session/queue/worker/
receipts/STOP/robots/rate/native SQL guards. Это не новый движок. Ровно1Partner и
1Optimum, остальные9SAVED_ONLY. Legacy20+20 и1270 ограничения сохранены. Запрещены
активация выключенных источников, discovery, resume, retries, robots exceptions,
matching apply и recovery старого окна. Оба checkpoint172/173 привязаны SHA256.
Все8уже запрошенных URL исключены, включая captured/UNCERTAIN/failed/quarantine;
шесть semantic holds не обходятся. HEAD/engine/SHA/window/settings/SQLidentity/
1282 остаются точными. Первая карточка проходит gate min(5, размер партии).

Partner: source4 / external297 / PP-800F508002 / accepted target88585.
Optimum: source3299 / external851 / OPTIsawCS275 / article3302275, настоящий станок,
не аксессуар. LIVE-VERIFIED LOCAL SELECT: Optimum сейчас REVIEW с открытой проверкой,
подтверждённой связи нет. Свежая цена не превращает REVIEW в NEW или подтверждение.
Проверка не обещает ненулевой XML новых товаров. Решение по конкретной идентичности
требует доказательств/отдельного согласования;52предложенияВекпром не входят.

## Подготовка и приёмка

`python -B scripts/supplier_system.py prepare --release-verification
--confirmed-commercial-targets TARGETS_MINIMAL_NOT_AUTHORIZED.json
--not-before <UTC-начало> --execution-until <UTC-конец> --output <новый-файл>`

Manifest/SHA, время Москвы, четыре планируемых URL (две карточки и robots двух hosts)
и команда run находятся в новом внешнем approval receipt. JSON не разрешает запуск.
Старый consumed e75cb4... не запускать. После нового точного допуска проверить реальные
ответы, наблюдения SQL/capture/offer/sourceID, диагностическое matching без apply,
оба XML и построчные исключения, сравнить LAST KNOWN GOOD. Непригодные значения
остаются NOT_READY; прошлые цены не подменяют свежие. REVIEW/holds/Product ID ради
положительного счётчика не менять. Stage6ESOL BLOCKED_IMPORT_CONTRACT_EVIDENCE.

QA текущего native пути и Studio отделён от исторического LIVE11 batch/recovery:
попытка последнего остановилась на старом MATRIX_SHA против reissued matrix. Runtime
pin не меняли, попытку не объявляют PASS; новый daily/replay не разрешён. Raw logs
и ограничения включены в evidence package. Это не обход guards нового допуска.
