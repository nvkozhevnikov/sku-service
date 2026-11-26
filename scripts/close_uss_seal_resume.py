"""Durable success/blocker closeout, only after clean retained-server shutdown."""
from scripts import resume_uss_terminal_batch as t
from pathlib import Path
import json
r,o,P=t.r,t.o,t.P

def main():
    shutdown=json.loads((P/'SHUTDOWN.json').read_text(encoding='utf-8-sig'))
    assert shutdown['control_exit']==0 and shutdown['workers_count']==0 and shutdown['port_55455_listeners']==0 and not shutdown['postmaster_pid_exists']
    assert any('Database cluster state:' in x and 'shut down' in x for x in shutdown['control_output'])
    assert any('7696153366151515704' in x for x in shutdown['control_output'])
    assert shutdown['stop_exit']==0
    success=(P/'RESULT.json').exists() and not (P/'BLOCKER.json').exists()
    result=r.load(P/('RESULT.json' if success else 'BLOCKER.json'))
    t.history();prep=r.load(P/'OFFLINE_PREPARATION.json')
    assert r.code_hashes()==prep['new_code_hashes']
    assert all(r.digest((o.ROOT/k).read_bytes())==v for k,v in prep['harness_hashes'].items())
    if success:
        assert result['status']=='LTT_USS_TERMINAL_BATCH_FIX_AND_RUN2_REVERIFIED'
        assert result['run2_scope']==2634 and result['run2_exact_noop_persisted']==2453
        assert result['clients']==dict(other_clients=0,open_transactions=0,prepared_transactions=0)
        assert all(result[k]==0 for k in ['run2_terminal_delta','run2_new_source_products','run2_new_offers','run2_new_captures','run2_new_observations','table_fingerprint_delta','sequence_delta','foreign_mutations','identity_overwrite','duplicate_external_ids','duplicate_offer_links','unexplained_missing'])
    # Windows native output pipeline delivered pg_ctl's start result only when
    # the server closed its inherited pipe. Keep the raw receipt; do not label
    # the delayed delivery timestamp as the actual start time.
    start_path=P/'PG_START.json'
    start=json.loads(start_path.read_text(encoding='utf-8-sig'))
    assert start['exit_code']==0
    r.atomic_bytes(P/'PG_START_RAW_NATIVE_PIPE_RECEIPT.json',start_path.read_bytes())
    start['receipt_completed_at']=start.pop('started_at')
    start['actual_start_time_from_log']=(P/'postgres.log').read_bytes().splitlines()[0].decode('ascii','replace').split('[')[0].strip()
    start['timestamp_explanation']='Native pg_ctl output pipe remained inherited by the server; receipt timestamp is delivery, actual start is in PostgreSQL log.'
    r.save(start_path,start)
    shutdown.update(status='CLEAN_SERVER_STOPPED',SAFE_TO_POWER_OFF='YES',database_started_this_scope=True,clients=0,open_transactions=0,prepared_transactions=0,sql_closeout_receipt='RESULT.json' if success else 'BLOCKER.json')
    r.save(P/'SHUTDOWN.json',shutdown)
    result.update(safe_to_power_off=True,SAFE_TO_POWER_OFF='YES',POSTGRES_55455_SHUTDOWN='CLEAN_SERVER_STOPPED',PORT_55455_LISTENING_AFTER='NO',worker_stopped=True,
        original_run1_seal=t.seals.provenance()['original_seal'],current_accepted_pre_batch_seal=t.seals.provenance()['accepted_pre_batch_seal'],batch_code_seal=dict(path=str((P/'TERMINAL_BATCH_FIX_CODE_SEAL.json').relative_to(o.ROOT)),sha256=r.digest((P/'TERMINAL_BATCH_FIX_CODE_SEAL.json').read_bytes())),
        DONE='Explicit seal-loader provenance; exact8 historical backfill; all-source reconciliation; full2634 RUN2' if success else 'Gated resume stopped at recorded blocker',
        APPLIED='Local replay infrastructure + append-only8USS source/offer/capture/observations only' if success else 'See durable applied rows',
        VERIFIED='9+18+303 tests; only8/407 impact; fresh51table40sequence baseline;2453 exactNOOP;runtime cleanly stopped' if success else 'See blocker and shutdown proof',
        next='FRESH_FINAL_FULL_QA_AFTER_OPERATOR_REVIEW' if success else 'OPERATOR_REVIEW_RECORDED_BLOCKER',completed_at=r.now())
    for name in ['FINAL_CHECKPOINT.json','CHECKPOINT.json']:r.save(P/name,result)
    root=Path(r'D:\documents\personal\ChatGPT\Стербруст')
    if success:
        after=r.load(P/'RUN2/AFTER.json');baseline=r.load(P/t.BASELINE_NAME)
        assert after['totals']==dict(source_products=2453,offers=2453,supplier_http_captures=2533,offer_commercial_observations=2533)
        assert len(after['fingerprints'])==51 and len(after['sequences'])==40
        assert all(r.encoded(baseline[k])==r.encoded(v) for k,v in after.items() if k!='checked_at')
        plan=r.load(P/'EFFECTIVE_ROW_PLAN.json')['rows'];assert len(plan)==2634
        for e in plan:assert r.digest((o.ROOT/e['journal']).read_bytes())==e['journal_sha256']
        r.save(P/'RUN2/INPUT_SEALS.json',dict(code_seal=dict(path='TERMINAL_BATCH_FIX_CODE_SEAL.json',sha256=r.digest((P/'TERMINAL_BATCH_FIX_CODE_SEAL.json').read_bytes())),baseline=dict(path=t.BASELINE_NAME,sha256=r.digest((P/t.BASELINE_NAME).read_bytes())),effective_plan=dict(path='EFFECTIVE_ROW_PLAN.json',sha256=r.digest((P/'EFFECTIVE_ROW_PLAN.json').read_bytes())),accepted_transition=dict(path='BATCH_CODE_TRANSITION.json',sha256=r.digest((P/'BATCH_CODE_TRANSITION.json').read_bytes())),provenance_sha256=t.seals.PROVENANCE_SHA256,all2634_journal_hashes_verified=True))
        r.save(P/'FINAL_DB_VERIFICATION.json',dict(evidence_class='REPOSITORY-VERIFIED_RETAINED_ISOLATED_SQL',totals=after['totals'],tables=51,sequences=40,run2_table_delta=0,run2_sequence_delta=0,baseline_counts=[2445,2445,2525,2525],backfill_delta=[8,8,8,8],existing_projections_unchanged=True,protected_namespaces_unchanged=True,historical_regressions_unchanged=['YUG312/297 distinct; ID1 absent','SIB8explicitunit','USS23/24','USS368/369/374/377/384','UF10021312USD/UF20031212USD'],fx='NONE',unreviewed_ambiguous=0,unreviewed_multi_variant=0))
        recovery='''# Recovery / accepted terminal batch checkpoint

LTT_USS_TERMINAL_BATCH_FIX_AND_RUN2_REVERIFIED / STOP.
NEXT=FRESH_FINAL_FULL_QA_AFTER_OPERATOR_REVIEW.

DONE/APPLIED/VERIFIED: explicit historical/current loader and pinned accepted transition; exact8 saved-source historical backfills; revised baseline2453products/2453offers/2533captures/2533observations; full2634offlineRUN2/2453actualwriterEXACT_NOOP/181stableterminals;51tablefingerprints/40sequencesdelta0.
Tests: previous9PASS; loader18PASS; exact regression303PASS/0FAIL/ERROR/SKIP. Business batch mechanisms unchanged during seal-gate step. Legacy original-RUN1 working-code guard still executable with explicit ORIGINAL_RUN1; missing/unknown/ambiguous/tampered selection fails closed.
SEAL_PROVENANCE.json records original RUN1 -> accepted commercial table correction -> local H2 FAILED historical node -> H2/sourceURL handoff accepted -> current accepted prebatch -> pinned proposedbatch. BATCH_CODE_TRANSITION.json separately accepts the pinned proposal only after green regression and fresh407 impact. Never rewrite old seals/failed tests/transitions.
Current accepted code: TERMINAL_BATCH_FIX_CODE_SEAL.json. Harness separately pinned in BATCH_CODE_TRANSITION/OFFLINE_PREPARATION. This frozen regression loader explicitly selects CURRENT_ACCEPTED_PRE_BATCH_SEAL + pinned TERMINAL_BATCH_FIX_PROPOSAL. New replay uses the accepted batch seal and revised effective plan; do not silently select whatever current files contain.
Read REVISED_RUN1_BASELINE_AFTER_TERMINAL_BATCH_FIX.json, EFFECTIVE_ROW_PLAN.json, REVISED_TERMINAL_INVENTORY.json, BACKFILL_RECEIPT.json, RUN2/RESULT.json, FINAL_DB_VERIFICATION.json, SHUTDOWN.json.
USS227P/114AMB/66MULTI=407; all114AMB and66MULTI already reviewed, no undecided terminals. Operator1/12/17 remain acceptedAMB family identity not proven;306/307/318 acceptedAMB datasheet no commercial offer. No parser changes to these or valid66MULTI.
Rows20/53/61/64/65/297/305/405 now persisted once. OriginalAMB journals/hash/time preserved, historical source time reused; no fake live event. Row53fixedsetprice1440RUB, no division; row65NULL/MISSING; row20primaryAluSPA excludes seal;405primarySSK2 isolated from explicitly additional accessories.
SupplierHTTP0. No production/matching/selection/Sterbrust/ESOL/55452/KAMI/develop merge/Gitpublish/deploy/cron. FULL_QA_RESUMED=NO; FINALIZER_STARTED=NO. Stage6 BLOCKED_IMPORT_CONTRACT_EVIDENCE.
PG55455/system7696153366151515704 cleanly stopped; clients0/openTx0/preparedTx0/workers0/portclosed/postmaster.pidabsent. SAFE_TO_POWER_OFF=YES for checked LTT/USS runtime.
Fresh finalFULLQA requires next explicit operator authorization. Do not automatically start400sample or finalizer.
'''
        report='''# LTT/USS terminal batch fix и новый RUN2 — VERIFIED / 2026-10-15

**LTT_USS_TERMINAL_BATCH_FIX_AND_RUN2_REVERIFIED / STOP**

DONE: явный переход тестового загрузчика между историческим seal и текущим принятым checkpoint, исправление8 подтверждённых терминальных строк, сверка всех источников и новый полный offline RUN2.
APPLIED: локальная инфраструктура воспроизведения и ровно8 append-only записей USS в каждой из четырёх разрешённых сущностей. Три механизма batch-парсера не менялись при исправлении загрузчика. Остальные исходные товары/предложения не переписаны.
VERIFIED / REPOSITORY-VERIFIED: предыдущие9FAIL теперь9PASS; отдельные18loader-тестов PASS; прежний точный regression gate303PASS/0FAIL/ERROR/SKIP. Дополнительные18проверок запущены отдельно, поэтому количество прежнего gate остаётся303. Изменены только явные аргументы выбора seal в прежних9бизнес-тестах; бизнес-утверждения и fixtures сохранены.

## Seal provenance

ORIGINAL_RUN1_SEAL: FULL_RUN1_2026-10-14/CODE_SEAL.json, SHA256 **20bb8e221f9e1c43f8b2f575914d55c019caeb9b0a8c698764fe330e704af1f3**.
CURRENT_ACCEPTED_PRE_BATCH_SEAL: USS_H2_URL_HANDOFF_2026-10-15/NEW_CODE_SEAL.json, SHA256 **ac2117eec4d17759afc84928fb259d5ffc498137b098cfdcc80e86676c1f8056**.
TERMINAL_BATCH_FIX_CODE_SEAL: текущая папка/TERMINAL_BATCH_FIX_CODE_SEAL.json, SHA256 **5e381f49520980cc0b06a6d6b8019965063185c1dd6e6e2cf6c5ef921965119b**.
PROVENANCE_CHAIN_VALID=YES. Исходный seal и исторические FAILED/принятые переходы не изменены. Локальный H2-переход, не прошедший focusedgate, явно сохранён как FAILED; принятый H2 появился после sourceURLhandoff и повторного RUN2. Принятый prebatch доказан baseline2445/2445/2525/2525 и RUN2 2634/2445EXACT_NOOP/terminaldelta0.
Загрузчик требует явный baseline; исторический guard сравнения текущего кода с исходным RUN1 остался исполняемым. Для текущих регрессий выбран CURRENT_ACCEPTED_PRE_BATCH_SEAL и отдельно закреплённый предложенный batch-переход. Пропущенный/неизвестный выбор, неразрешённый переход, неожиданные файлы или переписанная история блокируются. Предложение принято отдельным BATCH_CODE_TRANSITION.json только после тестов и свежего407gate. Инфраструктурные файлы закреплены отдельными хешами.

## Исправления и baseline

Повторно рассмотрены407сохранённых страниц. Изменились только20,53,61,64,65,297,305,405; additionalscope=0; payloaddelta существующих219PERSISTED/114неисправляемыхAMB/66MULTI/6operatorAMB=0.
До первой DB-записи PostgreSQL17.11/system7696153366151515704 проверен свежим SQL. Принятый baseline совпал полностью:51таблица,40последовательностей, suppliernamespaces и защищённые проекции.
Backfill ровно8ранее отсутствующих товаров/offer/capture/observation, обычным проверенным writer. Исходные URL, stablecanonicalpathID, response/evidenceSHA, originalobservationtime и старыеAMBjournals сохранены. Никаких новых fetch или фиктивных LIVE-событий.

| Метрика | До | После |
|---|---:|---:|
| SOURCE_PRODUCTS |2445|2453|
| OFFERS |2445|2453|
| CAPTURES |2525|2533|
| OBSERVATIONS |2525|2533|
| USS PERSISTED |219|227|
| USS AMBIGUOUS |122|114|
| USS MULTI_VARIANT |66|66|

Прирост+8каждой сущности объяснён восемью append-only historical backfill; старые проекции, внешние namespaces и защищённые таблицы неизменны. SOURCEID не выводился из модели/суффикса URL.
53: один набор4фиксаторов5100004,1440RUB за весь набор, PreOrder, без деления.65: пустая ценаNULL/MISSING, без нуля/POR.20: основнойAluSPA2102200/2100RUB/PreOrder, уплотнение не смешано.405: основнойSSK2/6204001/49980RUB/PreOrder, дополнительные принадлежности отделены локальной границей с сохранением guards.

OPERATOR-CONFIRMED:1/12/17 остаютсяAMB — familyURL не доказывает дочернюю идентичность;306/307/318 остаютсяAMB — datasheet без одобренного коммерческого offer. Все114AMB и66MULTI покрыты прежними семантическими решениями; UNREVIEWED_AMBIGUOUS=0, UNREVIEWED_MULTI_VARIANT=0. Новая семантическая выборка400 не запускалась.

## Новый полный offline RUN2

2634/2634: MSK369, SIB1266, YUG592(591товар+1категория), USS407.
2453PERSISTED прошли фактический обычный writer через NoMutationConnection: EXACT_NOOP, ingestcounts0, observation_created=false, projection_updated=false.181терминальная строка стабильна(114AMB+66MULTI+1непродуктоваякатегория); USS semanticdecision сохранено для каждой.
RUN2_NEW_SOURCE_PRODUCTS/OFFERS/CAPTURES/OBSERVATIONS=0. TABLE_FINGERPRINT_DELTA=0 для51таблицы; SEQUENCE_DELTA=0 для40последовательностей; TERMINAL_DELTA=0.
DUPLICATE_EXTERNAL_IDS=0; DUPLICATE_OFFER_LINKS=0; IDENTITY_OVERWRITE=0; FOREIGN_MUTATIONS=0; UNEXPLAINED_MISSING=0; BROKEN_REFERENCES=0.
Исторические регрессии сохранены: YUG312/297 раздельны, ID1отсутствует;8SIBunitprice неизменны; USS23/24 и368/369/374/377/384 неизменны; UF10021312USD, UF20031212USD, FX=NONE.

## Завершение и границы

SUPPLIER_HTTP=0. FULL_QA_RESUMED=NO; FINALIZER_STARTED=NO; MATCHING_RUN=NO; OFFER_SELECTION_RUN=NO; STERBRUST_WRITES=0; ESOL_WRITES=0. DB55452/KAMI/production/deploy/cron не затрагивались, публикацииGit/merge нет. Stage6 остаётся BLOCKED_IMPORT_CONTRACT_EVIDENCE.
До закрытия SQL-клиента otherclients/openTx/preparedTx=0; затем retainedPG55455 остановлен fast/wait. Свежий pg_controldata подтвердил shut down и systemID; workers0,порт55455закрыт,postmaster.pidотсутствует. **SAFE_TO_POWER_OFF=YES** для проверенного LTT/USS runtime.
NEXT=FRESH_FINAL_FULL_QA_AFTER_OPERATOR_REVIEW. Этот этап не является приёмкой FULLQA; следующая свежаяFULLQA требует разрешения оператора. STOP.

Все доказательства: work/sku-service-ltt-uss/reports/LTT_USS_INTEGRATION_2026-10-13/USS_SEAL_LOADER_RESUME_2026-10-15/. Основные: SEAL_PROVENANCE.json, LEGACY_RUN1_SEAL_PROOF.json, CURRENT_ACCEPTED_SEAL.json, PREVIOUS_9_TESTS.xml, LOADER_TESTS.xml, REGRESSION_TESTS.xml, IMPACT_COMPARISON_407.json, BATCH_CODE_TRANSITION.json, BACKFILL_RECEIPT.json, REVISED_RUN1_BASELINE_AFTER_TERMINAL_BATCH_FIX.json, EFFECTIVE_ROW_PLAN.json, REVISED_TERMINAL_INVENTORY.json, RUN2/RESULT.json, FINAL_DB_VERIFICATION.json, FINAL_CHECKPOINT.json, RECOVERY_NEXT.md, SHUTDOWN.json.
'''
    else:
        recovery='# Blocked resume / STOP\n\n'+json.dumps(result,ensure_ascii=False,indent=2)+'\n\nNo automatic repeat. Preserve applied journals; inspect blocker and last SQL state before new authorization. PG cleanly stopped; SAFE_TO_POWER_OFF=YES. No FULLQA/finalizer.\n'
        report='# USS seal-loader resume BLOCKED\n\n'+recovery
    r.atomic_bytes(P/'RECOVERY_NEXT.md',recovery.encode('utf8'))
    name='LTT_USS_TERMINAL_BATCH_FIX_RUN2_REVERIFIED_2026-10-15.md' if success else 'LTT_USS_SEAL_LOADER_RESUME_BLOCKED_2026-10-15.md'
    r.atomic_bytes(root/'reports'/name,report.encode('utf8'))
    task=root/'tasks/active/2026-10-15_LTT_USS_SEAL_LOADER_RESUME.md'
    task.write_text(task.read_text(encoding='utf8').replace('Status ACTIVE.','Status DONE / APPLIED / VERIFIED.' if success else 'Status BLOCKED.').replace('DONE/APPLIED/VERIFIED pending. Database not started.','DONE/APPLIED/VERIFIED recorded below. Database started and cleanly stopped.')+'\n## Completion\n\n'+recovery,encoding='utf8')
    dest=root/('tasks/completed' if success else 'tasks/blocked')/task.name;assert not dest.exists();task.rename(dest)
    update=f'''\n## USS seal-loader / terminal batch — {result['status']} — 2026-10-15\n\nDONE/APPLIED/VERIFIED: explicit original/currentseal provenance,9+18+303testsPASS;\nonly8/407changed, exact8backfill, revised2453/2453/2533/2533,USS227P114AMB66MULTI.\nFull2634offlineRUN2:2453EXACT_NOOP,51table/40sequencedelta0,180USSterminalsreviewed.\nSupplierHTTP0; FULLQANO/finalizerNO/SterbrustESOL0. PG55455cleanlystopped,SAFE_TO_POWER_OFF=YES.\n[Report](reports/{name}) · [Task]({dest.relative_to(root).as_posix()}).\nNEXT FRESH_FINAL_FULL_QA_AFTER_OPERATOR_REVIEW / STOP.\n\n''' if success else f'\n## USS seal-loader resume BLOCKED\n\n[Report](reports/{name}); SAFE_TO_POWER_OFF=YES. STOP.\n\n'
    for name2 in ['CURRENT_STATE.md','PROJECT_INDEX.md']:
        p=root/name2;first,rest=p.read_text(encoding='utf8').split('\n',1);p.write_text(first+'\n'+update+rest,encoding='utf8')
    r.save(r.BASE/'CHECKPOINT.json',result)
    seal={str(p.relative_to(P)):r.digest(p.read_bytes()) for p in P.rglob('*') if p.is_file() and p.name!='FINAL_ARTIFACT_SEAL.json'}
    r.save(P/'FINAL_ARTIFACT_SEAL.json',seal)
    print(dict(status=result['status'],SAFE_TO_POWER_OFF='YES',sealed_artifacts=len(seal),report=str(root/'reports'/name)))

if __name__=='__main__':main()
