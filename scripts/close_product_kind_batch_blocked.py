"""Preserve failed gates and close the unaccepted local code proposal safely."""
import json
import subprocess
from pathlib import Path
from collections import Counter
import xml.etree.ElementTree as ET
from scripts.audit_ltt_uss_product_kind import OUT as AUDIT, r, o, q

P=r.BASE/'PRODUCT_KIND_BATCH_FIX_2026-10-16'

def receipt(filename):
    root=ET.parse(P/filename).getroot();cases=root.findall('.//testcase')
    return dict(total=len(cases),passed=sum(not any(c.find(t) is not None for t in ['failure','error','skipped']) for c in cases),
        fail=sum(c.find('failure') is not None for c in cases),error=sum(c.find('error') is not None for c in cases),
        skip=sum(c.find('skipped') is not None for c in cases),file=filename,sha256=r.digest((P/filename).read_bytes()),
        failed=[dict(classname=c.get('classname'),name=c.get('name'),message=c.find('failure').get('message')) for c in cases if c.find('failure') is not None])

def prepare():
    pre=r.load(P/'PREFLIGHT.json');proposal=r.load(P/'CODE_PROPOSAL.json');assert proposal['new']==r.code_hashes()
    focused=receipt('FOCUSED_TESTS.xml');regression=receipt('REGRESSION_TESTS.xml')
    assert focused['passed']==408 and focused['fail']==focused['error']==focused['skip']==0
    assert regression['passed']==392 and regression['fail']==19 and regression['error']==0 and regression['skip']==1
    r.save(P/'TEST_RECEIPT.json',dict(gate='FAILED',focused=focused,regression=regression,xfail=0,
        aggregate_pass=focused['passed']+regression['passed'],aggregate_fail=19,aggregate_error=0,aggregate_skip=1,
        extra_suite_subtests_passed=8,assertions_weakened=False,tests_rewritten_or_retried=False))
    # New code proposal includes the shared classifier, which the historical
    # 11-file ingestion seal did not cover. This is explicitly NOT accepted.
    old=dict(pre['old_code_hashes']);old['sterbrust_matching/product_identity.py']=pre['classifier_sha256']
    new=dict(proposal['new']);new['sterbrust_matching/product_identity.py']=proposal['new_classifier_sha256']
    r.save(P/'PRODUCT_KIND_BATCH_FIX_PROPOSED_CODE_SEAL.json',new)
    changed=[name for name in new if old[name]!=new[name]]
    assert set(changed)=={'sterbrust_matching/product_identity.py','universal_supplier/adapters/ltt.py','universal_supplier/adapters/ltt_uss_html.py'}
    for name in changed:assert r.digest((P/'BEFORE_CODE'/name).read_bytes())==old[name]
    r.save(P/'PRODUCT_KIND_BATCH_TRANSITION.json',dict(status='REJECTED_TEST_GATE_NOT_ACCEPTED',
        OLD='TERMINAL_BATCH_FIX_CODE_SEAL',NEW_REQUESTED='PRODUCT_KIND_BATCH_FIX_CODE_SEAL',
        old_code_hashes=pre['old_code_hashes'],old_classifier_sha256=pre['classifier_sha256'],
        proposed_code_hashes=new,changed_paths=changed,
        old_11_file_seal_extended_with_shared_classifier=True,accepted_seal_created=False,
        test_gate='19FAIL_1SKIP',old_seals_rewritten=False,working_changes_preserved=True))
    historical=[]
    for phase in [AUDIT,q.OUT,q.accepted.P]:
        sealpath=phase/'FINAL_ARTIFACT_SEAL.json';seal=r.load(sealpath)
        for name,sha in seal.items():assert r.digest((phase/name).read_bytes())==sha,(phase,name)
        historical.append(dict(phase=str(phase.relative_to(o.ROOT)),files_checked=len(seal),seal_sha256=r.digest(sealpath.read_bytes()),status='UNCHANGED'))
    assert pre['baseline_sha256']==r.digest(q.BASELINE.read_bytes()) and pre['plan_sha256']==r.digest(q.PLAN.read_bytes())
    r.save(P/'HISTORICAL_EVIDENCE_VALIDATION.json',dict(status='PASS',phases=historical,
        baseline_and_effective_plan_unchanged=True,old_code_bytes_preserved_in_before_code=True,
        distinction='Historical artifact files unchanged. Old provenance reconstruction references active ltt.py/ltt_uss_html.py, whose authorized working edits cause SEAL_ARCHIVED_CODE_REWRITTEN. No archive was rewritten.'))
    r.save(P/'PRODUCT_KIND_DB_CORRECTION_RECEIPT.json',dict(status='NOT_STARTED_TEST_GATE_FAILED',
        db_started=False,db_correction_applied=False,corrected_rows=0,new_source_products=0,new_offers=0,
        new_captures=0,new_observations=0,source_writes=0,db_writes=0,fresh_sql_verified=False,
        latest_counts_evidence_class='PACKAGED-EVIDENCE_PREVIOUS_ACCEPTED_BASELINE'))
    r.save(P/'REVISED_BASELINE_STATUS.json',dict(status='NOT_CREATED',reason='DB and correction gates not reached'))
    r.save(P/'RUN2_STATUS.json',dict(status='NOT_STARTED',scope_executed=0,exact_noop_verified=False,reason='Tests failed; no accepted code transition or revised baseline'))
    r.save(P/'CORRECTION_CONTRACT_INSPECTION.json',dict(status='GATE_NOT_REACHED_NO_WRITER_SELECTED',
        evidence=[dict(path='universal_supplier/postgres.py',lines='179-228',finding='materialize_kami_saved_content is explicitly KAMI-only, not an LTT/USS correction path'),
            dict(path='universal_supplier/postgres.py',lines='230-275',finding='ordinary passive writer returns EXACT_NOOP immediately on existing capture; it does not refresh derived kind metadata'),
            dict(path='universal_supplier/commercial_persistence.py',lines='239-240',finding='enrichment_evidence copied into raw_data'),
            dict(path='scripts/resume_uss_terminal_batch.py',lines='79-125',finding='previous batch path inserts previously absent USS products, not existing-kind corrections')],
        ad_hoc_update_implemented=False,metadata_correction_method_implemented=False,db_started=False,
        next_requirement='Resolve test gates first, then determine reviewed safe metadata-only correction contract before any SQL writes. KAMI writer not invoked.'))
    print('Failed tests and proposal transition preserved; historical artifacts unchanged',flush=True)

def close():
    tests=r.load(P/'TEST_RECEIPT.json');impact=r.load(P/'PRODUCT_KIND_POSTFIX_IMPACT.json')
    shutdown=json.loads((P/'SHUTDOWN.json').read_text(encoding='utf-8-sig'))
    assert shutdown['control_exit']==shutdown['workers_count']==shutdown['matching_postgres_count']==shutdown['port_55455_listeners']==0
    assert not shutdown['postmaster_pid_exists'] and not shutdown['database_started_this_scope']
    assert any('Database cluster state:' in x and 'shut down' in x for x in shutdown['control_output'])
    assert any('7696153366151515704' in x for x in shutdown['control_output'])
    assert tests['gate']=='FAILED'
    r.save(P/'PARENT_CHECKPOINT.json',r.load(r.BASE/'CHECKPOINT.json'))
    refs=subprocess.run(['git','rev-parse','HEAD','develop','production'],cwd=o.ROOT,capture_output=True,text=True,check=True).stdout.splitlines()
    assert refs==['45052a6d21dae5e5f56e890f3a6e79466dcce733','e63eb6fe4da0a5124ae98deaa2f5f4313c4a96c3','36924836bb02965e857173773cdb70f302914643']
    result=dict(status='LTT_USS_PRODUCT_KIND_BATCH_FIX_BLOCKED',STAGE='TEST_GATE_AND_ADDITIONAL_KIND_IMPACT_BEFORE_DB',
        BLOCKER='29 persisted kind changes instead of authorized28 (additional MSK318/ID1642);19 failed regression tests and one skipped saved-artifact test',
        ROOT_CAUSE='Generic saw-blade precedence also corrects distinct source MSK318/ID1642, omitted by previous candidate policy. Prior provenance reconstruction uses active ltt.py/ltt_uss_html.py paths; authorized edits trip SEAL_ARCHIVED_CODE_REWRITTEN. Extra saved characteristic-evidence bundle absent.',
        CONFIRMED_SCOPE=dict(persisted=28,by_supplier=dict(ltt_msk=16,ltt_sib=12),terminal=5),
        NEW_SCOPE=dict(persisted=impact['additional_persisted'],terminal=impact['additional_terminal']),
        DB_STARTED='NO',DB_CORRECTION_APPLIED='NO',DB_WRITES=0,SOURCE_WRITES=0,SUPPLIER_HTTP=0,
        ROW129_FIXED='LOCAL_CODE_ONLY_NOT_STORED',PERSISTED_DEFECTS_FIXED='0/28_IN_DB',
        VALID_MACHINE_REGRESSIONS=len(impact['valid_machine_changes']),
        OPERATOR_REVIEW_ROWS_UNCHANGED='SIB941,SIB958',code_proposal_accepted=False,
        tests=dict(focused_pass=408,regression_pass=392,regression_fail=19,error=0,skip=1),
        full_impact_diagnostic= {k:v for k,v in impact.items() if k not in {'rows','network'}},
        baseline_counts=[2453,2453,2533,2533],baseline_counts_evidence_class='PACKAGED-EVIDENCE_NO_SQL_THIS_SCOPE',
        revised_baseline_created=False,RUN2='NOT_STARTED',FULL_QA_RESUMED='NO',FINALIZER_STARTED='NO',
        MATCHING_RUN='NO',OFFER_SELECTION_RUN='NO',STERBRUST_WRITES=0,ESOL_WRITES=0,
        POSTGRES_55455_SHUTDOWN='CLEAN_SERVER_STOPPED',PORT_55455_LISTENING_AFTER='NO',postmaster_pid='ABSENT',
        SAFE_TO_POWER_OFF='YES',safe_to_power_off=True,protected_git_refs=refs,
        DONE='Generic local kind proposal, all408 focused cases, existing regression run and full frozen impact diagnostic',
        APPLIED='Three local source files and new tests/fixtures/evidence/docs only; no database correction or accepted transition',
        VERIFIED='408 focusedPASS; broader tests failed; full impact diagnostic and stopped runtime. No fresh SQL or supplier verification',
        RECOVERY='Operator must review additional MSK318 scope, preserve distinct IDs and authorize any expansion; resolve old/proposed seal provenance without rewriting history or weakening tests and missing artifact gate; no retry/DB until new scope.',
        NEXT='OPERATOR_REVIEW_ADDITIONAL_KIND_SCOPE_AND_FAILED_TEST_GATE',STOP=True,closed_at=r.now())
    r.save(P/'FINAL_CHECKPOINT.json',result)
    recovery=f'''# Recovery / STOP

LTT_USS_PRODUCT_KIND_BATCH_FIX_BLOCKED.
STAGE=TEST_GATE_AND_ADDITIONAL_KIND_IMPACT_BEFORE_DB.
DONE: local generic classifier proposal and own-description corroboration;408 focusedPASS.
APPLIED: only three source files plus new tests/fixture/helpers/docs. DB not started, DB corrections0/28. No accepted new seal, baseline or RUN2. Old database row129 still machine/HIGH.
VERIFIED: broader regression392PASS/19FAIL/0ERROR/1SKIP (+8 passing subtests). No xfail/assertion weakening, no retries to conceal failures. Historical sealed audit/QA/accepted batch files and old code archives unchanged.

All19 failures arise before business assertions at SEAL_ARCHIVED_CODE_REWRITTEN. Old SEAL_PROVENANCE reconstruction points to active ltt.py and ltt_uss_html.py (each appears in two reconstructions), so the authorized working edits are treated as archive drift. Their exact old bytes exist in BEFORE_CODE. Classifier was not in old11-file seal; proposed new seal records it as a12th dependency. Do not rewrite prior seals or admit arbitrary working code. Existing replay tests were left unchanged.
One extra characteristic-evidence SavedArtifactTest skipped because its local saved bundle is unavailable. Do not fabricate a bundle or remove the check to report SKIP0.

Full classification-input diagnostic, not accepted gate: persisted2453, computed terminals180; additional persisted{len(impact['additional_persisted'])}, additional terminal{len(impact['additional_terminal'])}; valid machine changes{len(impact['valid_machine_changes'])}. It changes29 persisted instead of28: additional MSK318/ID1642, a separate saw-blade source card with the same title as MSK40. Actual new parser independently confirms the extra kind change. Do not merge distinct IDs or introduce a row exception to conceal impact. See ADDITIONAL_IMPACT_MSK318.json, PRODUCT_KIND_POSTFIX_IMPACT.json and PROPOSED_28_ROW_CORRECTION_MANIFEST.json.
The extra full-parser diagnostic did not complete: its first terminal price comparison directly compared dataclass tuple/Decimal with JSON list/string. FULL_PARSER_DIAGNOSTIC_FAILURE.json preserves the failure; the first price is equal after JSON normalization. The replacement diagnostic proves classification-input scope only; empty commercial/outcome-delta lists are NOT zero-delta proof. No RUN2/fresh SQL proof or full commercial replay acceptance.
Keep SIB941/SIB958 stored kinds unchanged, OPERATOR_REVIEW_REQUIRED. Do not infer by model/price/mass/replaceable knives. Source IDs, currencies, commercial evidence and observations remain authoritative and untouched.

Inspect TEST_GATE_BLOCKER.json, TEST_RECEIPT.json, PRODUCT_KIND_BATCH_TRANSITION.json and HISTORICAL_EVIDENCE_VALIDATION.json. The transition is REJECTED_TEST_GATE_NOT_ACCEPTED, not an active code baseline.
Next operator must review/authorize extra MSK318 scope and review the audit candidate blind spot. Resolve old/new checkpoint-aware test loader provenance and missing-artifact gate under explicit scope; then rerun required unchanged checks. Only after green tests and exact authorized impact, determine safe existing-metadata correction contract. Ordinary replay exact-capture branch is a no-op; KAMI-only materializer is not an LTT/USS writer. No ad-hoc SQL was added.

PG55455 remained stopped throughout; fresh SHUTDOWN.json proves17.11/system7696153366151515704/shut down,workers0/listeners0/postmaster.pid absent. SAFE_TO_POWER_OFF=YES for checked LTT/USS runtime.
No supplierHTTP, matching/selection execution, Sterbrust/ESOL, finalizer, FULLQA, Git publication/merge, production/deploy/cron/55452KAMI. Stage6 BLOCKED_IMPORT_CONTRACT_EVIDENCE.
NEXT=OPERATOR_REVIEW_ADDITIONAL_KIND_SCOPE_AND_FAILED_TEST_GATE / STOP.
'''
    r.atomic_bytes(P/'RECOVERY_NEXT.md',recovery.encode('utf8'))
    root=Path(r'D:\documents\personal\ChatGPT\Стербруст')
    report=f'''# LTT + USS: исправление product_kind остановлено до БД — 2026-10-16

**LTT_USS_PRODUCT_KIND_BATCH_FIX_BLOCKED / STOP**

DONE: подготовлена общая локальная коррекция по28 подтверждённым persisted-строкам и5 терминальным страницам. APPLIED: три локальных файла кода, новые тесты/fixture и материалы проверки; в БД0/28. VERIFIED:408 focusedPASS; общий тестовый gate не пройден —392PASS/19FAIL/0ERROR/1SKIP. Дополнительно8 subtestsPASS, не включены повторно в число тестов.

## Локальная реализация и проверки

Классификатор различает проданный предмет и упоминание станка-хозяина; сохранён приоритет заголовка полноценного станка. Добавлены явные концепты оснастки/деталей/инструмента и самостоятельных насосов/чиллера. Размытые модели, цена, масса и сменные ножи не задают тип. Для ручного подрезателя требуется подтверждение собственным назначением; MODX передаёт только scoped #product_descrption [itemprop=description], исключая связанные товары. Поля идентичности/цены/валюты/наличия не менялись. В shared taxonomy component учитывается как семейство принадлежностей; pumps/chiller остаются equipment.

Focused408 покрывают все28+5+2+362 строк, а также11 общих проверок и полноту cohort.362 типа машин сохранены; SIB941/SIB958 без изменения. Интерпретация источников PACKAGED-EVIDENCE/INFERENCE, требуемые границы OPERATOR-CONFIRMED; выполнение тестов REPOSITORY-VERIFIED. Это локальное предложение, не принятая коррекция.

## Почему общий gate остановил работу

Запущен прежний343-тестовый набор LTT/USS плюс текущие проверки shared-kind/false-match protections (stage6b, optimum identity, characteristic evidence, cross-source reconciliation). Все19 FAIL связаны с историческим provenance-loader: SEAL_ARCHIVED_CODE_REWRITTEN. До изменения кода цепочка прошла preflight; после изменения два рабочих пути ltt.py и ltt_uss_html.py, записанные в старой reconstruction как источники исторического кода, перестали совпадать со старыми хешами. Старые физические seals, отчёты и архивы не переписаны; исходные байты трёх изменённых файлов сохранены в BEFORE_CODE. Точная диагностика и XML сохранены. Ни guard, ни тесты не ослаблялись и не перезапускались ради зелёного результата.

Отдельный SKIP: tests.test_characteristic_evidence.SavedArtifactTests.test_built_offline_bundle_checksums_and_real_ready_zero — отсутствует сохранённый локальный evidence bundle. Это дополнительный к прежним343 тест; отсутствие не переименовано в PASS. Обязательное условие FAIL/ERROR/SKIP0 не выполнено.

## Диагностическое влияние и отсутствие применения

После отказа выполнено чтение frozen-входов классификатора:2453 persisted +180 computed terminal inputs, одна категория YUG без типа. Изменённых persisted={impact['changed_persisted']}, терминальных={impact['changed_terminal']}; дополнительный persisted scope={len(impact['additional_persisted'])}, terminal={len(impact['additional_terminal'])}; изменений362 valid machines={len(impact['valid_machine_changes'])}. Несовпадений с28 целевыми kind нет; две operator-review строки не изменились. Полная таблица входного сравнения и28-строчный proposed manifest сохранены. Это CLASSIFICATION_INPUT_DIAGNOSTIC_ONLY_TEST_GATE_FAILED_NOT_ACCEPTED, не зелёный gate и не RUN2.

**Дополнительная строка MSK318 / ID1642:** пила узла торцовки104х22х3.0/2.0х30Т, отдельные sourceproduct/offer372. Старый machine/HIGH → новый saw_blade/HIGH, что подтверждено фактическим текущим парсером. Заголовок совпадает с MSK40/ID1860, но стабильные ID различаются: KEEP BOTH, без дедупликации по модели/названию и без ID-исключений. Старая risk policy не включила318: «пила» считалась достаточным машинным существительным, форма «узла» не покрывалась узел*, а описание не содержало installation-маркера. Это новый scope за пределами разрешённых28; исправление БД запрещено до нового решения оператора.

Дополнительная полная перепроверка parser/commercial/outcome не завершена: диагностический helper сравнил tuple/Decimal из dataclass с list/string из JSON и остановился на первом terminal price. Ошибка сохранена отдельно; для первой строки значения равны после JSON-нормализации. Классификационный диагностический результат не выдаётся за полный replay или доказательство нулевых коммерческих/terminal-outcome изменений. Старые тесты/guards не ослаблялись.

Существующий passive writer возвращает exact-noop при уже сохранённом capture; KAMI-only saved-content materializer не является LTT/USS-контрактом исправления. Проверка безопасного контракта остаётся впереди; ad-hoc UPDATE не добавлялся. PG55455 не запускалась, SQL не выполнялся, DB/SOURCE_WRITES0. Базовые2453/2453/2533/2533 приведены исключительно как PACKAGED-EVIDENCE прошлого принятого снимка.

Row129/ID1910 исправлена только локальным кодом в frozen-тесте; сохранённая БД остаётся machine/HIGH. Коммерческие42000RUB/OutOfStock и идентичность не изменялись.28/28 DB-коррекция, revised baseline и новый RUN2 не начинались. Переход code seal отклонён тестовым gate, принятого PRODUCT_KIND_BATCH_FIX_CODE_SEAL нет; предложенный seal включает classifier, которого не было в прежнем11-файловом seal.

## Закрытие и восстановление

Свежая проверка PG55455:17.11/system7696153366151515704/shut down; workers0, порт закрыт, postmaster.pid отсутствует. База всё время оставалась выключенной, stop-команда не требовалась. **SAFE_TO_POWER_OFF=YES** для проверенного LTT/USS runtime.

SUPPLIER_HTTP0; MATCHING_RUN/SELECTION_RUN/FINALIZER/FULL_QA_RESUMED=NO; Sterbrust/ESOL0; Git refs прежние; публикации/merge/production/deploy/cron/55452KAMI не было. Synthetic unit tests не являются operational matching run. Старый FULLQA12/400(11PASS/1FAIL,388непроверено) не продолжался. Stage6 BLOCKED_IMPORT_CONTRACT_EVIDENCE.

NEXT: решение оператора по дополнительной MSK318 и пробелу прежнего risk-аудита; затем checkpoint-aware continuation с сохранением guards/истории, решение missing-artifact gate и повторная проверка. Новый старт БД допустим только после всех обязательных gates, точного разрешённого impact и безопасного correction contract.

[Checkpoint](../work/sku-service-ltt-uss/reports/LTT_USS_INTEGRATION_2026-10-13/PRODUCT_KIND_BATCH_FIX_2026-10-16/FINAL_CHECKPOINT.json) · [Тесты](../work/sku-service-ltt-uss/reports/LTT_USS_INTEGRATION_2026-10-13/PRODUCT_KIND_BATCH_FIX_2026-10-16/TEST_RECEIPT.json) · [Влияние](../work/sku-service-ltt-uss/reports/LTT_USS_INTEGRATION_2026-10-13/PRODUCT_KIND_BATCH_FIX_2026-10-16/PRODUCT_KIND_POSTFIX_IMPACT.json).
'''
    r.atomic_bytes(root/'reports/LTT_USS_PRODUCT_KIND_BATCH_FIX_BLOCKED_2026-10-16.md',report.encode('utf8'))
    task=root/'tasks/active/2026-10-16_LTT_USS_PRODUCT_KIND_BATCH_FIX.md'
    text=task.read_text(encoding='utf-8-sig').replace('Status ACTIVE.','Status BLOCKED.').replace('DONE/APPLIED/VERIFIED pending.','DONE/APPLIED/VERIFIED recorded below.')
    task.write_text(text+'\n## Closure\n\n'+recovery,encoding='utf8');dest=root/'tasks/blocked'/task.name
    assert not dest.exists();task.rename(dest)
    update=f'''
## Product-kind batch fix BLOCKED before DB — 2026-10-16

DONE/APPLIED:generic local code proposal,408focusedPASS; no accepted transition/DB corrections.
REPOSITORY-VERIFIED:regression392PASS/19FAIL/0ERROR/1SKIP; historical-loader active-path hashes and missing saved-artifact bundle block gate.
Readonly impact diagnostic:2453+180,changed persisted{impact['changed_persisted']}/terminal{impact['changed_terminal']},additional{len(impact['additional_persisted'])}+{len(impact['additional_terminal'])},valid-machine changes{len(impact['valid_machine_changes'])}. Not accepted gate/RUN2.
DBneverstarted/writes0,row129storedstillmachine/HIGH; historical artifacts unchanged. PG55455stopped/SAFE_TO_POWER_OFF=YES.
[Report](reports/LTT_USS_PRODUCT_KIND_BATCH_FIX_BLOCKED_2026-10-16.md) · [Task](tasks/blocked/2026-10-16_LTT_USS_PRODUCT_KIND_BATCH_FIX.md).
Additional scope MSK318/ID1642; no row exception or ID merge. Full parser/commercial replay incomplete; input classification diagnostic only.
NEXT operatorreview additional scope and failed gate / STOP.

'''
    for filename in ['CURRENT_STATE.md','PROJECT_INDEX.md']:
        path=root/filename;head,rest=path.read_text(encoding='utf8').split('\n',1);path.write_text(head+'\n'+update+rest,encoding='utf8')
    r.save(r.BASE/'CHECKPOINT.json',result)
    r.save(P/'FINAL_ARTIFACT_SEAL.json',{str(p.relative_to(P)):r.digest(p.read_bytes()) for p in P.rglob('*') if p.is_file() and p.name!='FINAL_ARTIFACT_SEAL.json'})
    print(dict(status=result['status'],DB_STARTED='NO',DB_CORRECTION_APPLIED='NO',SAFE_TO_POWER_OFF='YES',STOP=True))

if __name__=='__main__':
    import sys
    {'prepare':prepare,'close':close}[sys.argv[1]]()
