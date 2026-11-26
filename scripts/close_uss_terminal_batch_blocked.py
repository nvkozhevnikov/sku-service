"""Close failed test gate without database start or further code correction."""
from scripts import uss_terminal_batch_fix as b
r,o,P=b.r,b.o,b.PHASE

def main():
    import json
    shutdown=json.loads((P/'SHUTDOWN.json').read_text(encoding='utf-8-sig'))
    assert shutdown['control_exit']==0 and shutdown['workers_count']==0 and shutdown['port_55455_listeners']==0 and not shutdown['postmaster_pid_exists']
    assert any('Database cluster state:' in x and 'shut down' in x for x in shutdown['control_output'])
    assert any('7696153366151515704' in x for x in shutdown['control_output'])
    shutdown.update(status='CLEAN_SERVER_STOPPED',SAFE_TO_POWER_OFF='YES',clients=0,open_transactions=0,prepared_transactions=0,transaction_proof='Server shut down; no live backend. No SQL access in this scope. max_prepared_xacts=0 control setting.')
    r.save(P/'SHUTDOWN.json',shutdown)
    result=r.load(P/'BLOCKER.json');impact=r.load(P/'IMPACT_COMPARISON_407.json')
    b.history()
    assert r.code_hashes()==r.load(P/'NEW_CODE_SEAL_NOT_ACCEPTED.json')
    inventory=r.load(P/'TERMINAL_INVENTORY_OPERATOR_RESOLVED.json');ops=[]
    for d in inventory['rows']:
        if d['index'] not in b.OPERATOR:continue
        d['historical_review_reason']=d['semantic_reason']
        d['semantic_reason']=('Статический URL семейства с H1 не доказывает устойчивую идентичность текущего дочернего H2. Не создавать дочерний товар по названию/модели.' if d['index'] in {1,12,17} else 'Техническая страница без артикула/цены/наличия/покупки; контракт создания коммерческого предложения из datasheet не одобрен.')
        ops.append(dict(index=d['index'],source_id=d['source_id'],url=d['url'],decision=d['decision'],reason=d['semantic_reason'],evidence_class='OPERATOR-CONFIRMED'))
    r.save(P/'TERMINAL_INVENTORY_OPERATOR_RESOLVED.json',inventory)
    r.save(P/'OPERATOR_DECISIONS.json',ops)
    result.update(safe_to_power_off=True,SAFE_TO_POWER_OFF='YES',POSTGRES_55455_SHUTDOWN='CLEAN_SERVER_STOPPED',ROW53_FIXED='LOCAL_CODE_ONLY_NOT_BACKFILLED_NOT_ACCEPTED',FULL_QA_RESUMED='NO',FINALIZER_STARTED='NO',RUN2='NOT_STARTED',DONE='Three generic local implementations and diagnostic evidence; operator decisions recorded',APPLIED='Local code/tests/fixtures/docs only; no DB corrections',VERIFIED='New106cases passed including eight exact rows; overall gate failed9. 407 impact recorded, old evidence unchanged, PG remains stopped',historical_evidence_unchanged=True,code_changes_accepted=False,protected_git_refs=dict(HEAD='45052a6d21dae5e5f56e890f3a6e79466dcce733',develop='e63eb6fe4da0a5124ae98deaa2f5f4313c4a96c3',production='36924836bb02965e857173773cdb70f302914643'),completed_at=r.now())
    for name in ['FINAL_CHECKPOINT.json','CHECKPOINT.json','BLOCKER.json']:r.save(P/name,result)
    r.save(P/'NOT_PERFORMED.json',dict(reason='Failed regression test gate; explicit operator STOP contract',database_start=False,backfill=False,revised_baseline=False,revised_effective_plan=False,new_full_run2=False,full_qa=False,finalizer=False))
    recovery='''# Recovery: failed regression gate / STOP

LTT_USS_TERMINAL_BATCH_FIX_BLOCKED. STAGE=REGRESSION_TEST_GATE.
294PASS /9FAIL /0ERROR /0SKIP. 106 new batch regressions passed, including exact eight saved rows, operator terminals, all66 MULTI, historical corrected seven and adversarial ownership cases. Prior row53 correctness test now passes unchanged; historical FAILED evidence untouched.
All9 failures originate in run_ltt_uss_run2_offline.frozen_run(), which asserts current code equals original RUN1 CODE_SEAL. Current accepted pre-batch code already differs in USS adapter/card gate. This is static pre-batch hash evidence; no unchanged-baseline test reproduction was run. Do not relabel failures as PASS, weaken the seal assertion, xfail, or discard them.

Read TEST_RECEIPT.json, TESTS.xml, LEGACY_SEAL_MISMATCH.json and LOCAL_IMPLEMENTATION.diff. Three generic cluster transitions are local/not accepted. Only USS adapter production code changed; two row405 test expectations updated to operator-confirmed correctness, originals archived. No change to row53 correctness assertions.
Read IMPACT_COMPARISON_407.json for exact diagnostic old/new payload and gate comparison. It was collected read-only after the failed gate and is not permission to start PG or write DB.
Read OPERATOR_DECISIONS.json:1/12/17 family URL child identity unproven;306/307/318 datasheet no commercial offer. Keep all six AMBIGUOUS. Current accepted population stays USS219P/122AMB/66MULTI; no revised baseline or effective plan created.

DB_STARTED=NO; DB_CORRECTION_APPLIED=NO; DB_WRITES=0; SUPPLIER_HTTP=0; RUN2=NOT_STARTED; FULL_QA_RESUMED=NO; FINALIZER_STARTED=NO.
Retained PG55455 remains shut down, system7696153366151515704; workers0,clients0,open/preparedTx0,portclosed,postmaster.pidabsent. SAFE_TO_POWER_OFF=YES (checked LTT/USS runtime).
Recovery requires an operator decision on the failed test gate and authorization for a checkpoint-aware harness transition that preserves original RUN1 acceptance and all failed evidence. Do not silently weaken the old seal or rerun to hide failures. Then repeat required regression/407 gates before any retainedPG start/backfill; continue exact8 only, and full2634 RUN2 only after a verified revised baseline.
No production/Git publication/develop merge/matching/selection/Sterbrust/ESOL/55452/KAMI/deploy/cron. Stage6 remains BLOCKED_IMPORT_CONTRACT_EVIDENCE.
'''
    r.atomic_bytes(P/'RECOVERY_NEXT.md',recovery.encode('utf8'))
    root=b.Path(r'D:\documents\personal\ChatGPT\Стербруст')
    report=f'''# USS terminal batch fix — BLOCKED / 2026-10-15

**LTT_USS_TERMINAL_BATCH_FIX_BLOCKED / REGRESSION_TEST_GATE / STOP**

DONE: локально реализованы три общих механизма извлечения; добавлены14 точных сохранённых fixtures и106 новых регрессий, выполнено диагностическое сравнение всех407 страниц. Шесть решений оператора зафиксированы.
APPLIED: только локальный USS adapter, тесты, fixtures и документы. PostgreSQL не запускался; backfill, новая база/план и RUN2 не выполнялись. Код не принят как новый baseline.
VERIFIED (REPOSITORY-VERIFIED/PACKAGED-EVIDENCE):294PASS /9FAIL /0ERROR /0SKIP. Новые106 тестов, включая точные8 строки, прошли; общий gate не пройден. Старые артефакты, 11-file prior seal и исторические FAILED evidence сохранены. Из11 файлов рабочего кода изменён только USS adapter; старые версии двух тестов405 архивированы, проверки заменены на явно разрешённую семантику основного SSK2. Тест53 не ослаблен и прошёл.

## Блокер

Все9 FAIL относятся к проверке `run_ltt_uss_run2_offline.frozen_run()` о равенстве текущего кода **исходному** seal RUN1 до выполнения целевых утверждений. До текущего batch принятый код уже отличается от исходного RUN1 в USS adapter и card gate (LEGACY_SEAL_MISMATCH.json). Это статическое доказательство расхождения; повторное воспроизведение тестов на неизменённом прежнем baseline не выполнялось. Исторический guard не ослаблен, FAIL не переименованы в PASS/xfail, повторного прогона нет.

По разделу25 разрешения при провале тестов STOP. Продолжение до БД не выполнено.

## Локальная реализация и влияние

A: явные колонки, прямоугольная строка, собственный singular H1, статья/anchor и собственный заказ, единственная основная позиция; пустая цена65 остаётсяNULL/MISSING. Набор53 — одна цена1440RUB за полный комплект, без деления на4.
B: уникальное совпадение собственного H2 с основной строкой; остальные строки имеют явную зависимую роль уплотнения/запчасти. Перестановка строк не меняет выбор, две основные строки/чужой заголовок блокируются.
C: основной H2 и коммерческая таблица в локальном блоке до явной границы дополнительных принадлежностей. Исходные guards применяются к доказанному основному разделу; произвольный первый H2 не допускается.

Диагностическое влияние407: изменились строки **{','.join(map(str,impact['changed_indices']))}**; новая область **{impact['unexpected_indices']}**. Payload изменений: ранееPERSISTED={impact['existing_persisted_payload_changes']}, допустимыхAMB={impact['valid_ambiguous_payload_changes']}, MULTI={impact['multi_variant_payload_changes']}, шестиоператорскихAMB={impact['operator_accepted_ambiguous_payload_changes']}. Диагностика после провала gate не даёт разрешения на БД. Сохранены все точные old/new payload SHA и affected manifest с исходными URL/ID/hash/time/raw evidence.

OPERATOR-CONFIRMED:1/12/17 остаются AMBIGUOUS — family URL не доказывает устойчивую дочернюю идентичность;306/307/318 остаются AMBIGUOUS — нет контракта коммерческого offer для datasheet. Все114 не исправляемыхAMB и66MULTI сохраняются; текущий принятый baseline всё ещё219P/122AMB/66MULTI, totals2445/2445/2525/2525 только PACKAGED-EVIDENCE, без новой SQL-проверки. В спецификации108+6 трактуется как114 согласно исходному точному инвентарю.

## Безопасное завершение

SUPPLIER_HTTP=0; DB_STARTED=NO; DB_WRITES=0; DB_CORRECTION_APPLIED=NO. FULL_QA_RESUMED=NO; FINALIZER_STARTED=NO; matching/selection/Sterbrust/ESOL не запускались. Git HEAD/develop/production неизменны; публикации/merge/deploy/cron нет.
Свежая проверка pg_controldata: system7696153366151515704, shut down; порт55455 закрыт, postmaster.pid отсутствует, workers0, активных клиентов/транзакций нет. PostgreSQL оставлен остановленным, stop-команда не требовалась. **SAFE_TO_POWER_OFF=YES** для проверенного LTT/USS runtime.

Следующее действие: рассмотрение проваленного gate и разрешение перехода тестового загрузчика на актуальный принятый checkpoint с сохранением исходного seal и всей истории. Не ослаблять старую проверку. После этого повторить regression/407 gates, затем только разрешённый backfill8 и полный RUN2. FULL QA требует отдельного разрешения. Stage6 остаётся BLOCKED_IMPORT_CONTRACT_EVIDENCE.

Доказательства: work/sku-service-ltt-uss/reports/LTT_USS_INTEGRATION_2026-10-13/USS_TERMINAL_BATCH_FIX_2026-10-15/ — TESTS.xml, TEST_RECEIPT.json, LEGACY_SEAL_MISMATCH.json, LOCAL_IMPLEMENTATION.diff, CLUSTER_TRANSITIONS.json, IMPACT_COMPARISON_407.json, AFFECTED_ROWS.json, OPERATOR_DECISIONS.json, BACKFILL_RECEIPT.json(NOT_STARTED), NOT_PERFORMED.json, FINAL_CHECKPOINT.json, RECOVERY_NEXT.md, SHUTDOWN.json.
'''
    r.atomic_bytes(root/'reports/LTT_USS_TERMINAL_BATCH_FIX_BLOCKED_2026-10-15.md',report.encode('utf8'))
    task=root/'tasks/active/2026-10-15_LTT_USS_TERMINAL_BATCH_FIX.md'
    task.write_text(task.read_text(encoding='utf8').replace('Status ACTIVE.','Status BLOCKED / failed regression gate.').replace('DONE/APPLIED/VERIFIED pending;','DONE/APPLIED/VERIFIED recorded in completion;')+'\n## Completion\n\n'+recovery,encoding='utf8')
    dest=root/'tasks/blocked'/task.name;assert not dest.exists();task.rename(dest)
    update='''\n## USS terminal batch fix BLOCKED — 2026-10-15\n\nDONE/APPLIED:three local parser mechanisms,106new regression cases; no DB change.\nREPOSITORY-VERIFIED:294PASS/9FAIL/0ERROR/SKIP; historical frozen_run code-seal\nassertion failed before behavior checks. No assertion weakening/retest.\n407 impact evidence saved; sixoperator rows explicitly acceptedAMB.\nDB neverstarted/backfillNO/newbaselineNO/RUN2NO/FULLQANO; PG55455stillstopped,SAFE_TO_POWER_OFF=YES.\n[Report](reports/LTT_USS_TERMINAL_BATCH_FIX_BLOCKED_2026-10-15.md) · [Task](tasks/blocked/2026-10-15_LTT_USS_TERMINAL_BATCH_FIX.md).\nNEXT operator review failed gate/checkpoint-aware harness authorization / STOP.\n\n'''
    for name in ['CURRENT_STATE.md','PROJECT_INDEX.md']:
        p=root/name;first,rest=p.read_text(encoding='utf8').split('\n',1);p.write_text(first+'\n'+update+rest,encoding='utf8')
    r.save(r.BASE/'CHECKPOINT.json',result)
    seal={str(p.relative_to(P)):r.digest(p.read_bytes()) for p in P.rglob('*') if p.is_file() and p.name!='FINAL_ARTIFACT_SEAL.json'}
    r.save(P/'FINAL_ARTIFACT_SEAL.json',seal)
    print(dict(status=result['status'],tests={k:result['tests'][k] for k in ['passed','fail','error','skip']},SAFE_TO_POWER_OFF='YES',sealed_artifacts=len(seal)))

if __name__=='__main__':main()
