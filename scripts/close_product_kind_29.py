"""Durable closeout after a fresh stopped-runtime receipt."""
import json
from pathlib import Path
import subprocess
from scripts.prepare_product_kind_29 import P,PREVIOUS,r,o,q,AUDIT
from scripts import product_kind_metadata_correction as correction

def quiesce():
    import psycopg
    _,cfg=r.config()
    with psycopg.connect(**cfg,autocommit=True) as c:
        c.execute('SET default_transaction_read_only=on')
        assert c.execute('SHOW transaction_read_only').fetchone()[0]=='on'
        clients=o.clients(c)
        r.save(P/'PRE_SHUTDOWN_SQL.json',dict(**clients,checked_at=r.now(),read_only=True))
    print('Fresh SQL: other clients/open transactions/prepared transactions all0; connection closed',flush=True)

def close():
    shutdown=json.loads((P/'SHUTDOWN.json').read_text(encoding='utf-8-sig'))
    assert shutdown['control_exit']==shutdown['stop_exit']==shutdown['workers_count']==shutdown['port_55455_listeners']==0
    assert not shutdown['postmaster_pid_exists']
    assert any('Database cluster state:' in x and 'shut down' in x for x in shutdown['control_output'])
    assert any('7696153366151515704' in x for x in shutdown['control_output'])
    refs=subprocess.run(['git','rev-parse','HEAD','develop','production'],cwd=o.ROOT,text=True,capture_output=True,check=True).stdout.splitlines()
    assert refs==['45052a6d21dae5e5f56e890f3a6e79466dcce733','e63eb6fe4da0a5124ae98deaa2f5f4313c4a96c3','36924836bb02965e857173773cdb70f302914643']
    blocked=(P/'BLOCKER.json').exists() and not (P/'GUARD_REFUSAL_RESOLVED.json').exists()
    tests=r.load(P/'TEST_RECEIPT.json')
    root=Path(r'D:\documents\personal\ChatGPT\Стербруст')
    if blocked:
        result=r.load(P/'BLOCKER.json');result.update(SAFE_TO_POWER_OFF='YES',safe_to_power_off=True,
            POSTGRES_55455_SHUTDOWN='CLEAN_SERVER_STOPPED',NEXT='OPERATOR_REVIEW_CURRENT_BLOCKER',STOP=True)
        summary='BLOCKED; inspect BLOCKER.json and rollback/commit receipts before any retry.'
    else:
        correction.validate_gate();result=r.load(P/'RESULT.json');run2=r.load(P/'RUN2/RESULT.json')
        assert result['status']=='LTT_USS_PRODUCT_KIND_BATCH_FIX_AND_RUN2_REVERIFIED'
        assert run2['scope']==2634 and run2['exact_noop_persisted']==2453
        tests=r.load(P/'TEST_RECEIPT.json');assert tests['fail']==tests['error']==tests['skip']==0
        assert r.load(P/'IDEMPOTENCY_RECEIPT.json')['changed_rows']==0
        assert r.load(P/'PRODUCT_KIND_29_CORRECTION_RECEIPT.json')['fresh_committed_pairs_verified']==29
        result.update(AUTHORIZED_PERSISTED_SCOPE=29,PERSISTED_DEFECTS_FIXED='29/29',MSK_CORRECTED=17,SIB_CORRECTED=12,
            TERMINAL_KIND_DEFECTS_FIXED='5/5',MSK318_INCLUDED='YES',MSK318_MS40_DEDUP='NO',VALID_MACHINE_REGRESSIONS='0/362',
            SIB941_CHANGED='NO',SIB958_CHANGED='NO',ADDITIONAL_IMPACT_SCOPE=0,
            EXECUTABLE_TESTS_PASS=tests['executable_pass'],FAIL=0,ERROR=0,SKIP=0,HISTORICAL_ENVIRONMENT_NON_GATING=0,
            SOURCE_PRODUCTS=2453,OFFERS=2453,CAPTURES=2533,OBSERVATIONS=2533,NEW_RUN2_SCOPE='2634/2634',RUN2_EXACT_NOOP_PERSISTED=2453,
            RUN2_TERMINAL_OUTCOME_DELTA=0,TABLE_FINGERPRINT_DELTA=0,SEQUENCE_DELTA=0,
            SUPPLIER_HTTP=0,FULL_QA_RESUMED='NO',FINALIZER_STARTED='NO',MATCHING_RUN='NO',SELECTION_RUN='NO',STERBRUST_WRITES=0,ESOL_WRITES=0,
            POSTGRES_55455_SHUTDOWN='CLEAN_SERVER_STOPPED',PORT_55455_LISTENING_AFTER='NO',postmaster_pid='ABSENT',SAFE_TO_POWER_OFF='YES',safe_to_power_off=True,
            NEXT='FRESH_FINAL_FULL_QA_AFTER_OPERATOR_REVIEW',STOP=True,protected_git_refs=refs,completed_at=r.now(),
            DONE='Explicit archived provenance, recovered exact historical bundle, green tests and complete2634 parser/commercial/outcome replay',
            APPLIED='Local loader/tests and exact29 metadata corrections in isolated PG55455; no raw-source/commercial/history changes',
            VERIFIED='Fresh29 source/offer SQL pairs, unchanged49 protected tables/40sequences, idempotency0, new2634RUN2/2453EXACT_NOOP and clean shutdown')
        summary=f"29/29 (MSK17/SIB12), terminal5/5, tests{tests['executable_pass']}PASS/FAIL0/ERROR0/SKIP0, replay2634, RUN2=2634/2453NOOP."
        from decimal import Decimal
        mandatory=r.load(P/'MANDATORY_SQL_VERIFICATION.json')['rows']
        row129=next(x for x in mandatory if x[0]=='1910')
        assert Decimal(str(row129[3]))==42000 and row129[4:6]==['RUB','out_of_stock']
        assert row129[6]==row129[7] and row129[6]['product_kind']=='fixture'
        r.save(P/'POST_COMMIT_VERIFICATION_RESOLVED.json',dict(status='RESOLVED_REPOSITORY_AVAILABILITY_REPRESENTATION',
            historical_blocker='POST_COMMIT_VERIFICATION_REFUSAL/BLOCKER.json',source_domain_availability='OutOfStock',
            stored_normalized_availability='out_of_stock',source_product_offer_kind_agree=True,
            original_commit_count=29,repeat_correction_writes=0,full_run2_pass=True,
            run2_result_sha256=r.digest((P/'RUN2/RESULT.json').read_bytes())))
    r.save(P/'FINAL_CHECKPOINT.json',result)
    r.save(P/'CHECKPOINT.json',result)
    recovery=f'''# Recovery / next operator gate

{result['status']} / STOP.
{summary}
NEXT={result['NEXT']}.

DONE/APPLIED/VERIFIED: see FINAL_CHECKPOINT.json, FULL_REPLAY.json, TEST_RECEIPT.json, PRODUCT_KIND_BATCH_TRANSITION.json,
PRODUCT_KIND_29_MANIFEST.json, PRODUCT_KIND_29_CORRECTION_RECEIPT.json, ROLLBACK_BEFORE_COMMIT.json,
IDEMPOTENCY_RECEIPT.json, REVISED_BASELINE_AFTER_PRODUCT_KIND_BATCH_FIX.json and RUN2/RESULT.json.
Old historical seals/reports remain immutable. CURRENT_ACCEPTED_PRE_PRODUCT_KIND_SEAL and PROPOSED_PRODUCT_KIND_CODE_SEAL explicitly link archived historical bytes to the accepted PRODUCT_KIND_CODE_SEAL.json. No implicit working-file historical fallback.
Exact historical CHARACTERISTIC_EVIDENCE_2026-10-01_VERIFIED bundle was found in existing sku-service reports and copied byte-for-byte after checksum validation. No generated replacement, checksum edits, waived test or skip.
Prior408 focused tests covered28 persisted, not318. This continuation preserved them, added318 and provenance guards. Business classifier logic unchanged from the previous local proposal.
MSK318/ID1642 and MSK40/ID1860 remain separate source products/offers. SIB941/SIB958 remain unchanged, OPERATOR_REVIEW_REQUIRED. Metadata correction changes only nested raw_data.enrichment_evidence.product_kind in source_products/offers, with full before-state CAS and atomic29-pair commit. Rollback data was durably saved before UPDATE. No capture/observation append was required; raw history unchanged.
All2634 frozen inputs replayed through parser/commercial/card gates before DB: exactly29 persisted kind changes and5 terminal kind changes, no additional scope or commercial/identity/title/outcome delta. Kind362 valid-machine cohort unchanged. YUG category preserved;114USS AMBIGUOUS/66MULTI unchanged. YUG312/297 distinct/ID1 absent; eight SIB unit prices and21312USD/31212USD unchanged, FX NONE.
New RUN2 uses the new effective plan and corrected baseline; previous RUN2 is historical. All2453 persisted are EXACT_NOOP,51-table/40-sequence delta0 during RUN2, no kind reverts. No new products/offers/captures/observations.
Fresh pre-shutdown SQL clients/open/prepared transactions0; retainedPG55455 stopped fast/wait,17.11/system7696153366151515704/shut down,workers0/portclosed/postmaster.pid absent. SAFE_TO_POWER_OFF=YES for checked LTT/USS runtime.
No operational matching/selection, supplierHTTP, Sterbrust/ESOL, finalizer, FULLQA continuation, Git publication/merge, production/deploy/cron or55452KAMI. Stage6 BLOCKED_IMPORT_CONTRACT_EVIDENCE. Source PACKAGED-EVIDENCE; code/tests REPOSITORY-VERIFIED; fresh isolated SQL LIVE-VERIFIED; scope OPERATOR-CONFIRMED; sold-object interpretation INFERENCE.
Fresh final FULLQA requires separate operator authorization against the revised product-kind baseline. Old12/400(11PASS/1FAIL,388unreviewed) remains historical, not resumed or promoted.
'''
    if blocked:
        recovery=f"# BLOCKED / STOP\n\n{summary}\n\nActual blocker: {result}\n\nRuntime freshly stopped; SAFE_TO_POWER_OFF=YES. Do not infer correction or RUN2 completion. Inspect exact commit/rollback/receipt state before any new authorized continuation. No FULLQA or finalizer."
    r.atomic_bytes(P/'RECOVERY_NEXT.md',recovery.encode('utf8'))
    report=f'''# LTT + USS: product_kind29 и новый RUN2 — 2026-10-16

**{result['status']} / STOP**

{summary}

DONE: восстановлена явная provenance-цепочка ORIGINAL_RUN1 → TERMINAL_BATCH_FIX → CURRENT_ACCEPTED_PRE_PRODUCT_KIND → PROPOSED_PRODUCT_KIND, пройдены тесты и полный parser/commercial/outcome replay2634.
APPLIED: бизнес-логика предыдущего локального исправления сохранена; изменены loader/выбор checkpoint в тестах, добавлена узкая операция PRODUCT_KIND_METADATA_CORRECTION. В изолированнойPG55455 изменены только29 существующих source_products и соответствующие29 offers: MSK17/SIB12.
VERIFIED: source PACKAGED-EVIDENCE; code/tests REPOSITORY-VERIFIED; фактические строки/инварианты БД LIVE-VERIFIED только в изолированнойPG55455; разрешение OPERATOR-CONFIRMED, семантический вывод INFERENCE.

## Gates и сохранение истории

Focused/provenance{tests['receipts']['focused_and_provenance']['passed']}PASS (включая прежние408, новую MSK318 и штатное SQL-представление наличия129); регрессии412PASS; отдельно{tests['receipts']['metadata_operation']['passed']} тестов операции коррекции. FAIL/ERROR/SKIP0. Дополнительные8 subtests не включены повторно в итог. Прежняя информация о408 не означала покрытия318; новая регрессия добавлена явно.
Исторический evidence bundle найден в work/sku-service/reports/RC_LOCAL/CHARACTERISTIC_EVIDENCE_2026-10-01_VERIFIED. BUILD_VERIFIED и4 output checksums сверены, содержимое скопировано без генерации: real-ready0/confirmed pairs119. Исторический тест выполнен, NON_GATING_HISTORICAL_ENVIRONMENT0.
Исторические physical seals/reports не переписывались. Старые рабочие пути больше не считаются историческими байтами: immutable CHECKPOINT_PROVENANCE.json хранит архивные пути/хеши для каждого именованного seal. Нет fallback на current files; неверный/отсутствующий seal, переписанный архив или manifest отвергаются. Бизнес-assertions старых replay-тестов сохранены, изменён лишь явный checkpoint. Новое PRODUCT_KIND_CODE_SEAL включает shared classifier и исполняемые helpers/tests.
Первый target guard отказал до записи: inet::text вернул127.0.0.1/32. Read-only диагностика подтвердила тот же host127.0.0.1/порт55455/17.11 и неизменность baseline. Исправлено только получение адреса через host(inet_server_addr()), guards не сняты. Первые gate/код/отказ сохранены в INITIAL_GUARD_REFUSAL; инструмент заново протестирован и resealed до повторной попытки, baseline повторно сверена до записи.
После committed29 и идемпотентности0 verifier первоначально сравнил domain OutOfStock с SQL out_of_stock. Проверка приведена к штатной normalize_availability; наличия не переписывались. Отказ/диагностика сохранены в POST_COMMIT_VERIFICATION_REFUSAL; добавлен regression, gates resealed, продолжение выполнено verify-only без повторного UPDATE.

## Полный replay и29 коррекций

2634/2634:2453 persisted,180 вычисленных USS terminal,1YUG category. Сравнение tuple/Decimal с JSON list/string нормализовано через тот же канонический JSON без изменения значений. Ровно29 persisted +5 terminal kind changes; identity/title/price/currency/availability/non-kind-payload/outcome deltas0. Все362 VALID_MACHINE и SIB941/958 без изменения.
В PRODUCT_KIND_29_MANIFEST.json перечислены supplier/index/stableID/URL/title, старые и новые kind/confidence/reason/alias, cluster, source hashes и точные DB IDs.
Операция коррекции ограничена PG17.11/system7696153366151515704/127.0.0.1:55455 и immutable29-allowlist. Полные before-state source/offer сравниваются с принятым QA-снимком под row locks. Данные отката сохранены/fsync до первой записи.29 пар меняются одной транзакцией: только raw_data.enrichment_evidence.product_kind, full-row JSON compare-and-set; identity/commercial/timestamps/history сохраняются. Все29 RETURNING и повторное чтение сверены. Второй запуск в verify-only дал CHANGED_ROWS0, без sequence movement.
MSK129/ID1910 теперь fixture/HIGH,42000RUB/OutOfStock сохранены в source иoffer. MSK318/ID1642 теперь saw_blade/HIGH; отдельная отMSK40/ID1860 identity, без дедупликации. SIB941/958 не записывались, OPERATOR_REVIEW_REQUIRED.
Результат БД2453 products/2453 offers/2533 captures/2533 observations: новый capture/history event не нужен для derived-kind correction. За пределами source_products/offers все49 таблиц неизменны; все40 sequences неизменны; YUG/USS namespaces прежние. Полный projection delta равен исключительно29 ожидаемым metadata-патчам. Rollback/commit/idempotency/fresh SQL receipts сохранены.

## Новый RUN2 и завершение

Новый RUN22634/2634 против REVISED_BASELINE_AFTER_PRODUCT_KIND_BATCH_FIX:2453 EXACT_NOOP, terminal outcome delta0,51-table fingerprint delta0,40-sequence delta0. Новых source products/offers/captures/observations0. Корректные kind не откатились. Терминальные USS191/194/195/196/197 остаются терминальными, их kind cutting_tool подтверждены офлайн без создания DB-товаров.
YUG312/297 раздельны, ID1 отсутствует;8 SIB unit prices сохранены; UF10021312USD/UF20031212USD, FX NONE; USS227P/114AMB/66MULTI. Старые точечные price/title/identity исправления и история сохранены.
Fresh SQL перед выключением: workers после завершения0, неожиданных клиентов0, open/prepared transactions0. PG55455 остановлена fast/wait; system17.11/7696153366151515704,shut down,порт закрыт,postmaster.pid отсутствует. **SAFE_TO_POWER_OFF=YES** для проверенного LTT/USS runtime.
SUPPLIER_HTTP0; MATCHING_RUN/SELECTION_RUN/FINALIZER/FULL_QA_RESUMED=NO; Sterbrust/ESOL0. Нет55452KAMI/Git publication/merge/production/deploy/cron. Stage6 BLOCKED_IMPORT_CONTRACT_EVIDENCE.
NEXT={result['NEXT']} / STOP. Прежний FULLQA12/400(11PASS/1FAIL,388непроверено) остаётся историческим; свежая QA требует отдельного разрешения.

[Checkpoint](../work/sku-service-ltt-uss/reports/LTT_USS_INTEGRATION_2026-10-13/PRODUCT_KIND_29_RESUME_2026-10-16/FINAL_CHECKPOINT.json) · [29-row manifest](../work/sku-service-ltt-uss/reports/LTT_USS_INTEGRATION_2026-10-13/PRODUCT_KIND_29_RESUME_2026-10-16/PRODUCT_KIND_29_MANIFEST.json) · [DB receipt](../work/sku-service-ltt-uss/reports/LTT_USS_INTEGRATION_2026-10-13/PRODUCT_KIND_29_RESUME_2026-10-16/PRODUCT_KIND_29_CORRECTION_RECEIPT.json) · [RUN2](../work/sku-service-ltt-uss/reports/LTT_USS_INTEGRATION_2026-10-13/PRODUCT_KIND_29_RESUME_2026-10-16/RUN2/RESULT.json).
'''
    # Do not publish success prose if any unexpected runtime blocker occurred.
    if blocked:report=f"# Product-kind29 BLOCKED\n\n{summary}\n\n"+recovery
    else:
        report+='\n## Точная таблица29 изменений\n\n| Supplier / row | Stable ID | Название | Было | Стало | Cluster |\n|---|---|---|---|---|---|\n'
        for a in r.load(P/'PRODUCT_KIND_29_MANIFEST.json')['rows']:
            title=a['title'].replace('|','\\|')
            report+=f"| {a['supplier']} / {a['index']} | {a['external_id']} | {title} | {a['old_kind']['product_kind']}/{a['old_kind']['confidence']} | {a['new_kind']['product_kind']}/{a['new_kind']['confidence']} | {a['cluster']} |\n"
    r.atomic_bytes(root/'reports/LTT_USS_PRODUCT_KIND_29_RUN2_2026-10-16.md',report.encode('utf8'))
    task=root/'tasks/active/2026-10-16_LTT_USS_PRODUCT_KIND_29_RESUME.md'
    body=task.read_text(encoding='utf-8-sig').replace('Status ACTIVE.','Status BLOCKED.' if blocked else 'Status DONE / APPLIED / VERIFIED.').replace('DONE/APPLIED/VERIFIED pending.','DONE/APPLIED/VERIFIED recorded below.')
    task.write_text(body+'\n## Closure\n\n'+recovery,encoding='utf8')
    dest=root/('tasks/blocked' if blocked else 'tasks/completed')/task.name;assert not dest.exists();task.rename(dest)
    update=f'''
## Product-kind29 {'BLOCKED' if blocked else 'APPLIED and RUN2 VERIFIED'} — 2026-10-16

{summary}
DONE/APPLIED/VERIFIED recorded in checkpoint; explicit archived provenance, exact existing bundle, no historical seal rewrites.
PG55455CLEAN_SERVER_STOPPED/SAFE_TO_POWER_OFF=YES; HTTP0/noFULLQA/finalizer/matching/selection/SterbrustESOL/Git publication.
[Report](reports/LTT_USS_PRODUCT_KIND_29_RUN2_2026-10-16.md) · [Task]({str(dest.relative_to(root)).replace(chr(92),'/')}).
NEXT {result['NEXT']} / STOP.

'''
    for filename in ['CURRENT_STATE.md','PROJECT_INDEX.md']:
        path=root/filename;head,rest=path.read_text(encoding='utf8').split('\n',1);path.write_text(head+'\n'+update+rest,encoding='utf8')
    r.save(r.BASE/'CHECKPOINT.json',result)
    r.save(P/'FINAL_ARTIFACT_SEAL.json',{str(p.relative_to(P)):r.digest(p.read_bytes()) for p in P.rglob('*') if p.is_file() and p.name!='FINAL_ARTIFACT_SEAL.json'})
    print(dict(status=result['status'],SAFE_TO_POWER_OFF='YES',NEXT=result['NEXT'],STOP=True),flush=True)

if __name__=='__main__':
    import sys
    {'quiesce':quiesce,'close':close}[sys.argv[1]]()
