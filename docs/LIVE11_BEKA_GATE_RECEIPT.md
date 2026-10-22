# Один Beka terminal isolation gate внутри LIVE11

Это уже существующий шаг6 execution plan: successful Beka block + проверка изоляции
до остальных source blocks. Код не подменяет проверку изоляции простым job=succeeded.
Новые SQL QA/Partner LIVE50 не нужны; используются разрешённые before/after receipts.
Сейчас нет LIVE и реального receipt. Offline тесты используют явно synthetic hashes.

После будущего разрешённого Beka R+D оператор проверяет сохранённые baseline/after
evidence: текущая target identity, business outsiders/other10, сохранность старых
Product IDs/mappings/manual choices и допустимые новые scoped AUTO. Контроль не
разрешает cleanup вне scope и не превращает REVIEW/CONFLICT в accepted mappings.

Receipt — JSON ровно со следующими11полями:

| Поле | Значение |
|---|---|
| format | `live11-beka-isolation/v1` |
| batch_id | UUID уже созданного batch |
| head | HEAD frozen package |
| beka_job_ids | два фактических latest successful phase job IDs: R и D; при RESUME — latest children |
| target | точный `source_canary.TARGET`, включая system_identifier/PGDATA |
| outside_before_sha256 | SHA256 сопоставимого scoped outsiders evidence до Beka |
| outside_after_sha256 | SHA256 того же сопоставимого представления после; должен совпасть |
| mapping_preservation | `PASS`, подтверждение оператора на основании реальных read-only evidence |
| evidence_class | `OPERATOR-CONFIRMED` |
| operator | фактический оператор |
| reference | ссылка/путь к сохранённому read-only отчёту контроля |

Файл и его SHA передаются `scripts/source_batch.py confirm-beka`. Queue проверяет
SHA/target/HEAD/T0/current role и actual latest Beka statuses/job IDs, сохраняет
receipt в **existing audit_events** и только тогда выдаёт остальные host jobs.
Scope snapshots не переписываются. Чужой job/изменённый hash/неуспешный block/истёкший
пакет блокируют gate. Проверка content hash не является самостоятельным вычислением
всех outsiders или mappings: это явно подтверждение оператора, не новая LIVE-VERIFIED
метка. UI Grok может показать `beka_gate_confirmation_required` и вызвать тот же API.

Один пакет разрешает этот terminal control заранее. Никаких новых согласований после
каждых5, изменения скорости, запуска cron, нового источника или расширения cap нет.
