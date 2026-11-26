"""Seal first material FULL QA blocker after clean shutdown; no repairs."""
from pathlib import Path
import json
from scripts import qa_ltt_uss_latest as q
r,o,OUT=q.r,q.o,q.OUT

def close():
    blocker=r.load(OUT/'BLOCKER.json');shutdown=json.loads((OUT/'SHUTDOWN.json').read_text(encoding='utf-8-sig'))
    assert shutdown['stop_exit']==shutdown['control_exit']==0 and shutdown['workers_count']==shutdown['port_55455_listeners']==0 and not shutdown['postmaster_pid_exists']
    assert any('Database cluster state:' in x and 'shut down' in x for x in shutdown['control_output']) and any('7696153366151515704' in x for x in shutdown['control_output'])
    q.history();before=r.load(OUT/'BEFORE_QA.json');after=r.load(OUT/'AFTER_QA.json')
    assert all(r.encoded(before[k])==r.encoded(after[k]) for k in ['fingerprints','sequences','supplier_namespaces','product_offer_projections','special_products','sib_units','totals'])
    assert len(after['fingerprints'])==51 and len(after['sequences'])==40
    protected=r.load(OUT/'PROTECTED_STATE_COMPARISON.json');assert protected['clients']==dict(other_clients=0,open_transactions=0,prepared_transactions=0)
    shutdown.update(status='CLEAN_SERVER_STOPPED',clients=0,open_transactions=0,prepared_transactions=0,SAFE_TO_POWER_OFF='YES',sql_receipt='BLOCKER_DB_VERIFICATION.json')
    r.save(OUT/'SHUTDOWN.json',shutdown)
    start_path=OUT/'PG_START.json';start=json.loads(start_path.read_text(encoding='utf-8-sig'))
    if not (OUT/'PG_START_RAW_NATIVE_RECEIPT.json').exists():r.atomic_bytes(OUT/'PG_START_RAW_NATIVE_RECEIPT.json',start_path.read_bytes())
    if 'checked_at' in start:start['native_receipt_completed_at']=start.pop('checked_at')
    start['actual_start_time_from_log']=(OUT/'postgres.log').read_bytes().splitlines()[0].decode('ascii','replace').split('[')[0].strip();start['timestamp_explanation']='Windows native start output was delivered on inherited pipe closure; actual startup timestamp comes from server log.';r.save(start_path,start)
    cause=dict(evidence_class='REPOSITORY-VERIFIED',diagnosis='source_evidence sends full sold title to classify_product_kind; compatibility markers include для/под/к but not на; the own heading therefore retains target станок in the sold head and machine_name branch returns machine/HIGH. Source-description evidence independently proves an installed clamping accessory.',files=[dict(path='universal_supplier/adapters/ltt_uss_html.py',lines='74-87',sha256=r.digest((o.ROOT/'universal_supplier/adapters/ltt_uss_html.py').read_bytes())),dict(path='sterbrust_matching/product_identity.py',lines='74-80;171-188;206-217',sha256=r.digest((o.ROOT/'sterbrust_matching/product_identity.py').read_bytes()))],matching_pipeline_executed=False,fix_applied=False,wider_scope='UNKNOWN_NOT_INVESTIGATED_AFTER_FIRST_BLOCKER')
    r.save(OUT/'BLOCKER_ROOT_CAUSE.json',cause)
    tests=r.load(OUT/'TEST_RECEIPT.json')
    result=dict(blocker)
    result.update(root_cause=cause['diagnosis'],tests=tests,current_db_counts=after['totals'],POSTGRES_55455_SHUTDOWN='CLEAN_SERVER_STOPPED',PORT_55455_LISTENING_AFTER='NO',postmaster_pid='ABSENT',SAFE_TO_POWER_OFF='YES',
        DONE='Fresh all2634 structural/all2453 automated; fresh400 selected; first material semantic blocker recorded;180terminal mappings and6operator/9riskrechecks completed',
        APPLIED='Only local QA evidence/report/checkpoint; no source/parser/DB repair',
        VERIFIED='343PASS;12fresh semantic decisions(11PASS/1FAIL);51tables40sequences and namespaces unchanged; runtime cleanly stopped',
        safe_to_power_off=True,next='OPERATOR_REVIEW_PRODUCT_KIND_BLOCKER_AND_AUTHORIZE_SCOPED_FIX',closed_at=r.now())
    for name in ['FINAL_CHECKPOINT.json','FINAL_FULL_QA_REPORT.json','CHECKPOINT.json']:r.save(OUT/name,result)
    recovery='''# Fresh final FULL QA — BLOCKED / STOP

LTT_USS_FULL_QA_BLOCKED. SOURCE=ltt_msk ROW=129 SOURCE_ID=1910.
URL=https://ltt-msk.ru/katalog/raprodazha/pb-3000-pnevmobalka-na-formatno-raskroechnyij-stanok
QA_LAYER=FRESH_PERSISTED_SEMANTIC_PRODUCT_KIND_SAFETY. SEVERITY=QA_BLOCKER_HIGH.
The sold PB3000/ПБ3200 pneumatic clamping beam fits into the machine carriage T-slot. It is an accessory/fixture, not a complete machine. Current parsed and stored source_product185/offer185 raw enrichment.product_kind=machine/HIGH, matched_alias=станок, source=machine_name.
Read BLOCKER_EVIDENCE.json, BLOCKER_OWN_CARD.html, BLOCKER_DB_VERIFICATION.json, BLOCKER_ROOT_CAUSE.json and FRESH_400_SEMANTIC_RESULTS.json.
Confirmed scope exactly one source row. Possible wider accessory-title compatibility risk is INFERENCE/UNKNOWN; no expansion after first blocker. DB_CORRECTION_REQUIRED=YES for wrong kind metadata in source_products/offer raw_data, but NO correction authorized/applied by this QA. Source ID/price42000RUB/OutOfStock/links are coherent.

Structural2634/2634 and persisted automated2453/2453 PASS. Tests343PASS/0FAIL/ERROR/SKIP (+4pytest subtests passed). Fresh deterministic sample100each/400selected; actual reviewed12(allMSK):11PASS/1FAIL,388NOT_REVIEWED_AFTER_FIRST_BLOCKER. No old semantic PASS carried. The manifest/packets/facts are preparation, not approval.
114AMB and66MULTI current decision mappings verified;6operator freshchecks PASS,9riskMULTI freshchecks PASS including131/285. Current180terminal source_products absent in freshSQLsnapshot;405 is PERSISTED. Mandatory15USSfixes and SIB8units were selected/structurally traced/tested but their fresh semantic sample review was not reached. Do not claim FULLQA success or sample400complete.

Authoritative accepted input still TERMINAL_BATCH_FIX_CODE_SEAL and REVISED_RUN1_BASELINE_AFTER_TERMINAL_BATCH_FIX, totals2453/2453/2533/2533. Prior baseline/RUN2/seals/failed QA artifacts unchanged. FULLQA verdict BLOCKED does not rewrite historical no-op proof.
All51tablefingerprints/40sequences/allnamespaces/projections unchanged. SourceHTTP0, DBwrites0, parserchanges0, adapterschanges0, backfillNO, matchingNO, selectionNO, SterbrustESOL0, finalizerNO, Gitpublication/merge/deploy/cronNO, no55452/KAMI. Stage6 BLOCKED_IMPORT_CONTRACT_EVIDENCE.
PG55455 cleanly stopped:system7696153366151515704/shutdown,workers0/clients0/openTx0/preparedTx0,portclosed,postmaster.pidabsent. SAFE_TO_POWER_OFF=YES for checkedLTT/USSruntime.
NEXT=OPERATOR_REVIEW_PRODUCT_KIND_BLOCKER_AND_AUTHORIZE_SCOPED_FIX. Do not repair or resume semantic review without operator decision. After a separately authorized correction/new baseline, a new freshFULLQA must independently cover400rows; do not recycle these11PASS as substitutes.
'''
    r.atomic_bytes(OUT/'RECOVERY_NEXT.md',recovery.encode('utf8'))
    report='''# LTT/USS — свежая финальная FULL QA BLOCKED / 2026-10-15

**LTT_USS_FULL_QA_BLOCKED / QA_BLOCKER_HIGH / STOP**

В свежей семантической выборке подтверждён дефект типа товара: **LTT_MSK, строка129, stable source ID1910**, товар185/предложение185.
URL: https://ltt-msk.ru/katalog/raprodazha/pb-3000-pnevmobalka-na-formatno-raskroechnyij-stanok

DONE: новая QA по последнему принятому batch-baseline, без продолжения старого blockedQA. Выполнен полный структурный аудит2634строк, автоматическая проверка2453товаров и свежая детерминированная выборка400(100каждого источника); остановка на первом существенном семантическом дефекте.
APPLIED: только локальные QA-артефакты, отчёт/checkpoint. Parser/adapter/persistence/данныеБД не исправлялись.
VERIFIED:343PASS/0FAIL/ERROR/SKIP (+4pytestsubtestsPASS); фактически рассмотрены12товаров(11PASS/1FAIL). Подготовленные400пакетов не являются400семантическимиPASS.

## Доказанный блокер

Источник продаёт **PB-3000 пневмобалка … 2-х секционная мод.ПБ3200**. В собственном описании: «Пневмобалка подходит для любых форматно-раскроечных станков с длиной каретки3200мм. Она устанавливается в Т-образный паз на каретке». Собственные характеристики описывают прижим:5цилиндров,2зоны,масса30кг. Это устанавливаемая на станок прижимная принадлежность/fixture, а не целый форматно-раскроечный станок.
EXPECTED: accessory/fixture, not complete machine. ACTUAL: parser и свежие source_products.raw_data.enrichment_evidence.product_kind и offers.raw_data.enrichment_evidence.product_kind содержат **machine / HIGH**, matched_alias=«станок», source=machine_name.
Собственный miniShop2 ID1910, название, цена42000RUB, OutOfStock и связиoffer/capture/observation корректны. Блокер относится именно к семантике продаваемого объекта; каноническое matching не выполнялось.

PACKAGED-EVIDENCE: исходный сохранённый DOM, response/evidenceSHA, originalobservationtime и собственная productформа1910. REPOSITORY-VERIFIED: свежая readonlySQL-выборка товара185/offer185, сохранённых parsedpayload и исходников цепочки классификации. INFERENCE с прямой опорой на описание: accessory/fixture является продаваемым объектом.
Техническая причина: source_evidence передаёт полное имя классификатору. COMPATIBILITY_MARKERS содержат «для/под/к», но не «на». Поэтому слово«станок» из описания совместимости остаётся в soldhead и веткаmachine_name возвращаетmachine/HIGH. Код только прочитан; matchingpipeline и исправление не запускались.
CONFIRMED_SCOPE=1строка129/ID1910. WIDER_SCOPE=UNKNOWN: аналогичные названия принадлежностей могут иметь тот же риск; после STOP область не расширялась. DB_CORRECTION_REQUIRED=YES для ошибочной kindmetadata в товаре/предложении. Никакая коррекция этим разрешением не выполнена.

## Фактическое покрытие

| Проверка | Результат |
|---|---|
| Структурные строки |2634/2634 PASS|
| Persisted automated trace |2453/2453 PASS|
| Тесты |343PASS /0FAIL /0ERROR /0SKIP|
| Новая sample |400выбрано:MSK100/SIB100/YUG100/USS100|
| Свежие semanticdecisions |12/400:11PASS/1FAIL,всеMSK|
| Не рассмотрено послеSTOP |388|
| AMBIGUOUS decisionmapping |114/114 PASS|
| Свежая проверка operatorAMB |6/6 PASS|
| MULTI_VARIANT decisionmapping |66/66 PASS|
| Свежие рискованныеMULTI |9/9 PASS,включая131/285|
| 15историческихUSSfixes |Выбраны/автоматическипроверены; свежая semanticreview не достигнута|
| SIB8unitrows |Выбраны/структурнопроверены/регрессиитестированы; свежая semanticreview не достигнута|

Все114AMB/66MULTI сопоставлены с единственным действующим решением и заново проверенными исходнымиSHA/currentparser/effectiveoutcome. Семантические recheck6операторскихстрок и9рискMULTI проведены по свежимDOMfacts.131: две модели CDS6250B/С с отдельнымиPOR/наличием без кнопок;285: собственные220/380В,артикулы/цены/двазаказаGU1,не вывод только из аксессуаров. Для317 повторённый HTMLanchor источника не превращён в единственный товар: разные articlecells/заказы сохраняютMULTI.
YUG312/297 раздельны иID1отсутствует в структурном/DBtrace; UF10021312USD/UF20031212USD и8SIBunits подтверждены текущими тестами и SQLsnapshot. Полная новая semanticacceptance этих специальных случаев не заявляется после остановки. FALSE_AMBIGUOUS/FALSE_MULTI_VARIANT не найдены в выполненном терминальном слое; общий FULLQA остаётсяBLOCKED.

## Неизменность и завершение

Входной seal: TERMINAL_BATCH_FIX_CODE_SEAL, SHA2565e381f49520980cc0b06a6d6b8019965063185c1dd6e6e2cf6c5ef921965119b. Полная provenancechain и все прежние seals/QA сохранены. Baseline2453товара/2453offers/2533captures/2533observations не изменён.
До/после QA совпали все51publictablefingerprints,40sequencevalues,всеsuppliernamespaces/проекции и46защищённыхтаблиц. FOREIGN_MUTATIONS=0, DB_WRITES=0, SOURCE_WRITES=0, SUPPLIER_HTTP=0. ReadonlySQLguard/serverdefault_transaction_read_only=on и persistence/HTTPguards действовали.
FINALIZER_STARTED=NO; MATCHING_RUN=NO; OFFER_SELECTION_RUN=NO; STERBRUST_WRITES=0; ESOL_WRITES=0. Нет commit/push/merge/production/deploy/cron/55452/KAMI. Stage6 BLOCKED_IMPORT_CONTRACT_EVIDENCE.
Перед закрытием SQL:clients0/openTx0/preparedTx0; затем fast/waitshutdownPG55455. Свежий pg_controldata:system7696153366151515704,shut down; workers0,портзакрыт,postmaster.pidABSENT. **SAFE_TO_POWER_OFF=YES** для проверенногоLTT/USSruntime.

NEXT=OPERATOR_REVIEW_PRODUCT_KIND_BLOCKER_AND_AUTHORIZE_SCOPED_FIX. FULLQA не принята, finalizer/featurecommit не разрешены этим результатом. STOP.

Артефакты: work/sku-service-ltt-uss/reports/LTT_USS_INTEGRATION_2026-10-13/FULL_QA_TERMINAL_BASELINE_2026-10-15/. Основные: BLOCKER_EVIDENCE.json, BLOCKER_OWN_CARD.html, BLOCKER_DB_VERIFICATION.json, BLOCKER_ROOT_CAUSE.json, STRUCTURAL_AUDIT.json, PERSISTED_AUTOMATED_AUDIT.json, FRESH_400_SAMPLE_MANIFEST.json, FRESH_400_SEMANTIC_RESULTS.json, AMBIGUOUS_DECISION_VERIFICATION.json, MULTI_VARIANT_DECISION_VERIFICATION.json, HISTORICAL_FIX_REGRESSION_AUDIT.json, SPECIAL_CASE_AUDIT.json, TEST_RECEIPT.json, DB_INTEGRITY.json, PROTECTED_STATE_COMPARISON.json, HTTP_ZERO_PROOF.json, FINAL_FULL_QA_REPORT.json, FINAL_CHECKPOINT.json, RECOVERY_NEXT.md, SHUTDOWN.json.
'''
    root=Path(r'D:\documents\personal\ChatGPT\Стербруст')
    r.atomic_bytes(OUT/'FINAL_FULL_QA_REPORT.md',report.encode('utf8'));r.atomic_bytes(root/'reports/LTT_USS_LATEST_FULL_QA_BLOCKED_2026-10-15.md',report.encode('utf8'))
    task=root/'tasks/active/2026-10-15_LTT_USS_FRESH_FINAL_FULL_QA_LATEST.md'
    task.write_text(task.read_text(encoding='utf8').replace('Status ACTIVE.','Status BLOCKED / material semantic product-kind defect.').replace('DONE/APPLIED/VERIFIED pending.','DONE/APPLIED/VERIFIED recorded below.')+'\n## Completion\n\n'+recovery,encoding='utf8')
    dest=root/'tasks/blocked'/task.name;assert not dest.exists();task.rename(dest)
    update='''\n## Latest-baseline fresh FULL QA BLOCKED — 2026-10-15\n\nDONE:2634structural/2453automated;343testsPASS;new400sample selected.\nFresh semantic12reviewed=11PASS/1FAIL;388unreviewed after first material blocker.\nLTT_MSKrow129/sourceID1910 PB3000pneumaticclampingbeam storedas machine/HIGH\nin sourceproduct185/offer185; ownsource proves installed accessory/fixture.\n114AMB+66MULTImappingPASS;6operator/9riskMULTIfreshrechecksPASS.\nAPPLIED:QAevidenceonly;DB/source/codewrites0,all51tables40sequencesunchanged.\nPG55455cleanlystopped,SAFE_TO_POWER_OFF=YES;FULLQA notaccepted/finalizerNO.\n[Report](reports/LTT_USS_LATEST_FULL_QA_BLOCKED_2026-10-15.md) · [Task](tasks/blocked/2026-10-15_LTT_USS_FRESH_FINAL_FULL_QA_LATEST.md).\nNEXT operatorreview productkindblocker/scopedfixauthorization / STOP.\n\n'''
    for name in ['CURRENT_STATE.md','PROJECT_INDEX.md']:
        p=root/name;first,rest=p.read_text(encoding='utf8').split('\n',1);p.write_text(first+'\n'+update+rest,encoding='utf8')
    r.save(r.BASE/'CHECKPOINT.json',result)
    seal={str(p.relative_to(OUT)):r.digest(p.read_bytes()) for p in OUT.rglob('*') if p.is_file() and p.name!='FINAL_ARTIFACT_SEAL.json'};r.save(OUT/'FINAL_ARTIFACT_SEAL.json',seal)
    print(dict(status=result['status'],source='ltt_msk',row=129,source_id='1910',SAFE_TO_POWER_OFF='YES',sealed_files=len(seal)))

if __name__=='__main__':close()
