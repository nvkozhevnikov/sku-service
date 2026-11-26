"""Seal completed read-only inventory and record stopped runtime."""
from scripts.audit_uss_terminal_sweep import *

def main():
    pre=r.load(OUT/'PREFLIGHT.json');assert r.code_hashes()==pre['current_accepted_code']
    old=r.load(q.OUT/'FINAL_ARTIFACT_SEAL.json')
    for key,value in old.items():
        path=q.OUT/Path(key.replace('\\','/'))
        assert r.digest(path.read_bytes())==value,(key,'old evidence drift')
    shutdown=__import__('json').loads((OUT/'SHUTDOWN.json').read_text(encoding='utf-8-sig'))
    assert shutdown['control_exit']==0 and shutdown['workers_count']==0 and shutdown['port_55455_listeners']==0 and not shutdown['postmaster_pid_exists']
    assert any('Database cluster state:' in l and 'shut down' in l for l in shutdown['control_output'])
    assert any('7696153366151515704' in l for l in shutdown['control_output'])
    shutdown.update(status='CLEAN_SERVER_STOPPED',SAFE_TO_POWER_OFF='YES',clients=0,open_transactions=0,prepared_transactions=0,prepared_transactions_evidence='PACKAGED-EVIDENCE prior SQL + shut down/max_prepared_xacts=0; no fresh SQL in this scope',prior_clean_stop=str((q.OUT/'SHUTDOWN.json').relative_to(o.ROOT)))
    r.save(OUT/'SHUTDOWN.json',shutdown)
    a=r.load(OUT/'USS_AMBIGUOUS_122_DECISIONS.json');m=r.load(OUT/'USS_MULTI_VARIANT_66_DECISIONS.json')
    allrows=a['rows']+m['rows'];assert len(allrows)==len({d['index'] for d in allrows})==188
    for d in allrows:
        assert d['semantic_reason'] and d['self_canonical']
        assert r.digest((OUT/d['source_evidence']['facts_path']).read_bytes())==d['source_evidence']['facts_sha256']
        if d['decision']=='VALID_MULTI_VARIANT':
            assert len(set(d['article_anchors']))>=2 or d['index']==131
    defects=r.load(OUT/'USS_CONFIRMED_DEFECTS.json')['rows'];assert len(defects)==8
    clusters=r.load(OUT/'USS_DEFECT_CLUSTERS.json')
    assert sorted(i for c in clusters['clusters'] for i in c['indices'])==sorted(d['index'] for d in defects)
    for c in clusters['clusters']:
        c['repository_evidence']=[dict(path='universal_supplier/adapters/uss_stanko.py',sha256=pre['current_accepted_code']['universal_supplier/adapters/uss_stanko.py'],lines='63-113' if 405 in c['indices'] else '116-146; commercial value extraction'),dict(path='universal_supplier/ltt_uss_capture.py',sha256=pre['current_accepted_code']['universal_supplier/ltt_uss_capture.py'],lines='160-173')]
    r.save(OUT/'USS_DEFECT_CLUSTERS.json',clusters)
    validation=dict(status='PASS',checked_at=r.now(),decision_rows=188,unique_rows=188,ambiguous_rows=122,multi_variant_rows=66,all_407_shape_audit=True,corrected_seven_excluded=True,code_seal_unchanged=True,old_qa_artifact_count_checked=len(old),old_qa_all_sealed_files_unchanged=True,semantic_decisions_not_inferred_from_gate_alone=True,all_decision_facts_hashes_verified=True,tests_run_this_scope=False,new_supplier_http=0,db_started=False,source_mutations=0)
    r.save(OUT/'INVENTORY_VALIDATION.json',validation)
    result=r.load(OUT/'USS_TERMINAL_SEMANTIC_SWEEP.json')
    result.update(status='LTT_USS_TERMINAL_SEMANTIC_SWEEP_VERIFIED',DONE='188 individual frozen semantic decisions and 407 table-shape audits',APPLIED='Local inventory/report/checkpoint only; no source repair',VERIFIED='Coverage/hash invariants and stopped runtime; frozen semantic evidence, no fresh supplier/SQL proof',VALID_AMBIGUOUS=108,FALSE_AMBIGUOUS=8,OPERATOR_REVIEW_AMBIGUOUS=6,VALID_MULTI_VARIANT=66,FALSE_MULTI_VARIANT=0,OPERATOR_REVIEW_MULTI_VARIANT=0,CONFIRMED_PARSER_DEFECT_ROWS=8,DEFECT_CLUSTERS=3,ROW53_CONFIRMED='YES',ROW53_FIXED='NO',DB_WRITES=0,PARSER_CHANGES=0,SUPPLIER_HTTP=0,BACKFILL='NO',RUN2='NO',FULL_QA_VERIFIED='NO',FINALIZER_STARTED='NO',SAFE_TO_POWER_OFF='YES',NEXT='OPERATOR_REVIEW_DEFECT_INVENTORY_AND_AUTHORIZE_BATCH_FIX',STOP=True,completed_at=r.now())
    r.save(OUT/'USS_TERMINAL_SEMANTIC_SWEEP.json',result)
    r.save(OUT/'FINAL_CHECKPOINT.json',dict(**result,baseline_counts_evidence_class='PACKAGED-EVIDENCE',baseline_counts=[2445,2445,2525,2525],baseline_not_revised=True,sql_not_started=True,shutdown_receipt='SHUTDOWN.json',validation='INVENTORY_VALIDATION.json'))
    recovery='''# Recovery / next operator gate

LTT_USS_TERMINAL_SEMANTIC_SWEEP_VERIFIED. STOP.
NEXT=OPERATOR_REVIEW_DEFECT_INVENTORY_AND_AUTHORIZE_BATCH_FIX.

DONE: 122/122 AMBIGUOUS and 66/66 MULTI_VARIANT individually reviewed; all407 frozen USS pages inspected for column-header shape.
APPLIED: only local inventory/helper/report/checkpoint files. No parser repair, persistence, backfill, baseline replacement, RUN2, FULL QA or finalizer.
VERIFIED: coverage and evidence hashes, unchanged accepted11 code files and all prior sealed FULL QA artifacts; PG55455 remains cleanly stopped, workers0, port closed, postmaster.pid absent, SAFE_TO_POWER_OFF=YES.

Read USS_CONFIRMED_DEFECTS.json and USS_DEFECT_CLUSTERS.json: eight confirmed omissions20/53/61/64/65/297/305/405 in three technical clusters.
Read OPERATOR_REVIEW_ROWS.json:1/12/17 family-page identity policy;306/307/318 datasheet-only admission policy. These six are not confirmed defects.
Row53 remains unfixed and its historical FAILED regression remains unchanged. Row405 has a fresh falseAMB decision based on primary SSK2 versus explicitly additional accessories.
Price65 remains missing, not zero/POR; preserve raw currencies/prices, independently purchasable variants and distinct source IDs. Do not use model or -rub suffix for dedupe.
Corrected23/24/368/369/374/377/384 remain excluded; do not reopen without contradictory evidence.
All semantic source evidence is PACKAGED-EVIDENCE; interpretations are labelled INFERENCE, row53 operator finding OPERATOR-CONFIRMED. No new supplierHTTP or fresh SQL verification.
Production/ESOL/import/matching/selection/Git publication/cron/deployment/55452 KAMI are outside scope. Stage6 remains BLOCKED_IMPORT_CONTRACT_EVIDENCE.
Before any future work, require exact new operator scope; do not automatically repair or start FULL QA.
'''
    r.atomic_bytes(OUT/'RECOVERY_NEXT.md',recovery.encode('utf8'))
    root=o.ROOT.parents[1] if False else Path(r'D:\documents\personal\ChatGPT\Стербруст')
    report='''# USS: завершённая инвентаризация терминальных решений — 2026-10-15

**LTT_USS_TERMINAL_SEMANTIC_SWEEP_VERIFIED / STOP**

DONE: семантически рассмотрены 122/122 AMBIGUOUS и 66/66 MULTI_VARIANT. Форма таблицы с колонками артикул/цена/наличие/заказ проверена на всех 407 сохранённых страницах USS. Нерассмотренных терминальных строк нет.
APPLIED: только локальные решения, доказательства, отчёт и checkpoint. Исправлений парсера и адаптера нет.
VERIFIED: полнота/уникальность решений, хеши доказательств, неизменность 11 принятых файлов кода и всех файлов предыдущего FULL QA seal, остановленное состояние PG55455. Источники — PACKAGED-EVIDENCE, семантическая интерпретация — INFERENCE; это не новая LIVE-проверка и не приёмка FULL QA.

| Результат | AMBIGUOUS | MULTI_VARIANT |
|---|---:|---:|
| Обосновано |108|66|
| Подтверждённый дефект |8|0|
| Решение оператора |6|0|
| Всего |122|66|

## Подтверждённые дефекты

1. Строки53,61,64,65,297,305: один собственный товар с единственной коммерческой строкой в таблице с колонками. Парсер ищет вертикальные строки и пропускает значения. У65 цена пустая и должна остаться неизвестной; у53 набор из4 фиксаторов — один фиксированный набор.
2. Строка20: один основной пистолет Alu SPA и отдельно продаваемое уплотнение в общей таблице. Необходима принадлежность строки основному изделию; выбор первой строки без guards недопустим.
3. Строка405: одна камера SSK2, 6204001 /49980 RUB /под заказ; затем явно отдельные дополнительные принадлежности. Глобальные ограничения _own_h2 теряют собственный заголовок из-за второй таблицы/чужих заказов. Требуется локальная проверка основного раздела, не снятие guards.

Технические основания REPOSITORY-VERIFIED: текущий код и его точные хеши плюс сохранённый DOM/текущий результат каждой строки. Возможность общего будущего исправления — INFERENCE и не разрешение его применять. Строка53 OPERATOR-CONFIRMED; ROW53_FIXED=NO. Историческая FAILED регрессия сохранена без ослабления/xfail/перезапуска ради зелёного результата.

## Решения оператора

1,12,17: H1 страницы семейства при одном текущем основном H2; требуется политика идентичности дочернего товара на URL семейства. Строка12 не признана дефектом только по похожей таблице.
306,307,318: технические страницы DPA21/DPA22/DRO5 с однозначной моделью, но без артикула/цены/наличия/заказа; требуется политика допуска datasheet без коммерческого предложения.

Все66 MULTI_VARIANT имеют самостоятельные коммерческие исполнения/предложения; одно лишь количество строк, технические параметры или состав комплекта не использованы как доказательство. Для131 отдельные модельные колонки CDS6250B/CDS6250С содержат свои «по запросу»/«на складе» без кнопок заказа. Для285 питание220/380В и разные артикулы подтверждают варианты GU1, а не дополнительные лампы/патрубки. Для каталогов принадлежностей основание — собственные независимые заказы нескольких изделий.
Исправленные23/24/368/369/374/377/384 не включены повторно. Canonical всех188 совпадает с собственной сохранённой страницей. source_id в решениях обозначает статическую страницу, не выдуманный идентификатор варианта. Одинаковая модель/URL-суффикс не используются для слияния.

## Границы и завершение

SUPPLIER_HTTP=0; DB_WRITES=0; PARSER_CHANGES=0; BACKFILL=NO; RUN2=NO; FULL_QA_VERIFIED=NO; FINALIZER_STARTED=NO. Тесты текущего этапа не запускались. База не запускалась; количества2445/2445/2525/2525 приведены только как PACKAGED-EVIDENCE последнего принятого SQL-снимка, новая база не сформирована.
Свежая проверка pg_controldata: system identifier7696153366151515704, состояние shut down; порт55455 закрыт, postmaster.pid отсутствует, рабочих процессов0. SAFE_TO_POWER_OFF=YES для проверенного LTT/USS runtime.

NEXT=OPERATOR_REVIEW_DEFECT_INVENTORY_AND_AUTHORIZE_BATCH_FIX. Исправления и новая FULL QA требуют следующего разрешения. Stage6 остаётся BLOCKED_IMPORT_CONTRACT_EVIDENCE.

Артефакты: work/sku-service-ltt-uss/reports/LTT_USS_INTEGRATION_2026-10-13/USS_TERMINAL_SWEEP_2026-10-15/. Решения содержат точные URL/source_id, заголовки, модели/артикулы, таблицы/цены/наличие/кнопки, canonical, причины текущего парсера и семантического решения, хеши исходных материалов. Семантические выводы относятся к замороженному корпусу, не текущему состоянию сайтов.
'''
    r.atomic_bytes(root/'reports/LTT_USS_TERMINAL_SEMANTIC_SWEEP_2026-10-15.md',report.encode('utf8'))
    task=root/'tasks/active/2026-10-15_LTT_USS_TERMINAL_SWEEP.md'
    task.write_text(task.read_text(encoding='utf8')+'\n## Completion\n\n'+recovery,encoding='utf8')
    text=task.read_text(encoding='utf8').replace('Status ACTIVE;','Status DONE / VERIFIED;').replace('DONE/APPLIED/VERIFIED pending.','DONE/APPLIED/VERIFIED recorded below.').replace('NEXT complete188decisions/407shapeinventory/clusters/report/checkpoint/provePGstopped/STOP.','Initial execution plan completed; see completion gate below.')
    task.write_text(text,encoding='utf8');dest=root/'tasks/completed'/task.name
    assert not dest.exists();dest.parent.mkdir(exist_ok=True);task.rename(dest)
    update='''\n## USS terminal semantic sweep VERIFIED — 2026-10-15\n\nDONE:122AMB+66MULTI individual decisions,407shape audit. APPLIED:local inventory only.\nPACKAGED-EVIDENCE/INFERENCE:108validAMB,8confirmed coverage defects,6operator rows;66validMULTI.\nREPOSITORY-VERIFIED:accepted code and old QA evidence unchanged; row53 remainsunfixed.\nPG55455stopped/currentcontrolchecked,workers0/portclosed/pidabsent;SAFE_TO_POWER_OFF=YES.\nNo supplierHTTP/DBwrites/parserrepair/newbaseline/RUN2/FULLQAacceptance/finalizer.\n[Report](reports/LTT_USS_TERMINAL_SEMANTIC_SWEEP_2026-10-15.md) · [Task](tasks/completed/2026-10-15_LTT_USS_TERMINAL_SWEEP.md).\nNEXT OPERATOR_REVIEW_DEFECT_INVENTORY_AND_AUTHORIZE_BATCH_FIX / STOP.\n\n'''
    for name in ['CURRENT_STATE.md','PROJECT_INDEX.md']:
        p=root/name;body=p.read_text(encoding='utf8');first,rest=body.split('\n',1);p.write_text(first+'\n'+update+rest,encoding='utf8')
    r.save(r.BASE/'CHECKPOINT.json',r.load(OUT/'FINAL_CHECKPOINT.json'))
    seal={str(p.relative_to(OUT)):r.digest(p.read_bytes()) for p in OUT.rglob('*') if p.is_file() and p.name!='FINAL_ARTIFACT_SEAL.json'}
    r.save(OUT/'FINAL_ARTIFACT_SEAL.json',seal)
    print(dict(status=result['status'],old_qa_files_unchanged=len(old),new_artifacts_sealed=len(seal),SAFE_TO_POWER_OFF='YES'))

if __name__=='__main__':main()
