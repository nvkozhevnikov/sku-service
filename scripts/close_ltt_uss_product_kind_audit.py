"""Validate and close the offline impact audit without touching source state."""
from pathlib import Path
import json
import subprocess
from collections import Counter
from scripts.audit_ltt_uss_product_kind import OUT, r, o, q, candidate, MACHINE

def main():
    q.history()
    pre=r.load(OUT/'PREFLIGHT.json');assert pre['code_hashes']==r.code_hashes()
    assert pre['baseline_sha256']==r.digest(q.BASELINE.read_bytes())
    assert pre['plan_sha256']==r.digest(q.PLAN.read_bytes())
    assert pre['qa_artifact_seal_sha256']==r.digest((q.OUT/'FINAL_ARTIFACT_SEAL.json').read_bytes())
    oldseal=r.load(q.OUT/'FINAL_ARTIFACT_SEAL.json')
    for path,sha in oldseal.items():assert r.digest((q.OUT/path).read_bytes())==sha
    architecture=r.load(OUT/'CLASSIFIER_ARCHITECTURE.json')
    for path,sha in architecture['files'].items():assert r.digest((o.ROOT/path).read_bytes())==sha
    for call in architecture['call_sites']:assert r.digest((o.ROOT/call['path']).read_bytes())==call['file_sha256']
    inv=r.load(OUT/'PRODUCT_KIND_INVENTORY.json');risks=r.load(OUT/'PRODUCT_KIND_RISK_CANDIDATES.json')
    decisions=r.load(OUT/'PRODUCT_KIND_SEMANTIC_DECISIONS.json');terms=r.load(OUT/'TERMINAL_KIND_INVENTORY.json')
    termdec=r.load(OUT/'TERMINAL_KIND_SEMANTIC_DECISIONS.json');counts=r.load(OUT/'SWEEP_COUNTS.json')
    assert inv['total']==len(inv['rows'])==2453 and inv['by_supplier']==dict(ltt_msk=369,ltt_sib=1266,ltt_yug=591,uss_stanko=227)
    assert terms['total_computed']==len(terms['rows'])==180 and terms['category_yug_not_computed']==1
    keys=lambda a:{(x['supplier'],x['index']) for x in a}
    assert len(keys(inv['rows']))==2453
    assert keys(risks['rows'])==keys(decisions['rows']) and len(decisions['rows'])==392
    assert len(termdec['rows'])==20 and keys(termdec['rows'])==keys(r.load(OUT/'TERMINAL_KIND_RISK_CANDIDATES.json')['rows'])
    regenerated=set();verified=0
    for item in inv['rows']+terms['rows']:
        facts=r.load(OUT/item['facts'])
        assert r.digest((OUT/item['facts']).read_bytes())==item['facts_sha256']
        assert r.digest((o.ROOT/facts['evidence_path']).read_bytes())==item['evidence_sha256']
        assert r.digest((o.ROOT/item['journal']).read_bytes())==item['journal_sha256']
        if item['db_evidence'].get('record'):
            assert r.digest((o.ROOT/item['db_evidence']['record']).read_bytes())==item['db_evidence']['sha256']
            if candidate(item,facts)[0]:regenerated.add((item['supplier'],item['index']))
        verified+=1
    assert regenerated==keys(risks['rows'])
    allowed={'VALID_MACHINE','CONFIRMED_FALSE_MACHINE_ACCESSORY','CONFIRMED_FALSE_MACHINE_FIXTURE',
        'CONFIRMED_FALSE_MACHINE_COMPONENT','CONFIRMED_FALSE_MACHINE_TOOLING','OTHER_KIND_DEFECT','OPERATOR_REVIEW_REQUIRED'}
    for item in decisions['rows']+termdec['rows']:
        assert item['semantic_decision'] in allowed and item['actually_sold'] and item['semantic_reason']
        assert item['evidence']['evidence_sha256']==item['evidence_sha256']
        assert r.digest((OUT/item['facts']).read_bytes())==item['evidence']['facts_sha256']
    defects=r.load(OUT/'PRODUCT_KIND_CONFIRMED_DEFECTS.json')['rows']
    clusters=r.load(OUT/'PRODUCT_KIND_DEFECT_CLUSTERS.json')
    assert len(defects)==28 and sum(a['persisted_confirmed_count'] for a in clusters['clusters'])==28
    assert sum(a['terminal_confirmed_count'] for a in clusters['clusters'])==5
    assert {(a['supplier'],a['index']) for c in clusters['clusters'] for a in c['rows'] if a['persisted']}==keys(defects)
    network=r.load(OUT/'HTTP_ZERO_PROOF.json')
    assert all(network[k]==0 for k in ['supplier_http_gets','supplier_http_heads','robots_http','prohibited_transport_attempts','network_retries','db_writes'])
    shutdown=json.loads((OUT/'SHUTDOWN.json').read_text(encoding='utf-8-sig'))
    assert shutdown['control_exit']==shutdown['workers_count']==shutdown['matching_postgres_count']==shutdown['port_55455_listeners']==0
    assert not shutdown['database_started_this_scope'] and not shutdown['postmaster_pid_exists']
    assert any('Database cluster state:' in s and 'shut down' in s for s in shutdown['control_output'])
    assert any('7696153366151515704' in s for s in shutdown['control_output'])
    gitrefs=subprocess.run(['git','rev-parse','HEAD','develop','production'],cwd=o.ROOT,text=True,capture_output=True,check=True).stdout.splitlines()
    assert gitrefs==['45052a6d21dae5e5f56e890f3a6e79466dcce733','e63eb6fe4da0a5124ae98deaa2f5f4313c4a96c3','36924836bb02965e857173773cdb70f302914643']
    validation=dict(status='PASS',checked_at=r.now(),all_persisted_inventory=2453,unique_persisted=2453,
        persisted_risk_candidates_regenerated_identically=392,semantic_decisions=392,terminal_semantic_decisions=20,
        facts_and_journals_verified=verified,prior_qa_files_unchanged=len(oldseal),
        accepted_11_code_seal_unchanged=True,architecture_and_all_32_callsites_files_unchanged=True,
        baseline_and_effective_plan_unchanged=True,protected_git_refs=gitrefs,tests_run_this_scope=False,
        live_sql_this_scope=False,sql_writes=0,supplier_http=0,
        noncandidate_machine_family= sum(x['current_product_kind'] in MACHINE for x in inv['rows'])-392,
        inventory_is_not_full_taxonomy_or_full_qa_approval=True)
    r.save(OUT/'INVENTORY_VALIDATION.json',validation)
    checkpoint=dict(status='LTT_USS_PRODUCT_KIND_IMPACT_SWEEP_VERIFIED',**counts,
        PERSISTED_ROWS_INVENTORIED='2453/2453',PRODUCT_KIND_RISK_CANDIDATES=392,
        SEMANTICALLY_REVIEWED_CANDIDATES=392,CONFIRMED_FALSE_MACHINE=23,OTHER_KIND_DEFECT=5,
        VALID_MACHINE=362,OPERATOR_REVIEW_REQUIRED=2,AFFECTED_SUPPLIERS=['ltt_msk','ltt_sib'],
        DEFECT_CLUSTERS=3,TERMINAL_ONLY_DEFECT_CLUSTERS=1,TOTAL_DEFECT_CLUSTERS=4,
        ROW129_CONFIRMED='YES',ROW129_CURRENT_KIND='machine/HIGH',ROW129_EXPECTED_KIND='accessory/fixture',
        ROW129_FIXED='NO',CLASSIFIER_SHARED_ACROSS_SOURCES='YES',DB_CORRECTION_ROWS=28,
        PARSER_CHANGES=0,CLASSIFIER_CHANGES=0,ADAPTER_CHANGES=0,DB_WRITES=0,SOURCE_WRITES=0,SUPPLIER_HTTP=0,
        FULL_QA_VERIFIED='NO',FINALIZER_STARTED='NO',RUN2='NO',BACKFILL='NO',
        matching_run=False,offer_selection_run=False,sterbrust_writes=0,esol_writes=0,
        baseline_counts=dict(source_products=2453,offers=2453,supplier_http_captures=2533,offer_commercial_observations=2533),
        baseline_counts_evidence_class='PACKAGED-EVIDENCE_LAST_ACCEPTED_SQL_NOT_QUERIED_THIS_SCOPE',
        source_evidence_class='PACKAGED-EVIDENCE',semantic_interpretation_class='INFERENCE',
        known_row129_expected_class='OPERATOR-CONFIRMED',technical_evidence_class='REPOSITORY-VERIFIED',
        DONE='All2453 inventory,392 individual persisted risk decisions,180 computed terminal inventory/20 candidate decisions, architecture and clusters',
        APPLIED='Only local audit helper, evidence, report and checkpoint files; no corrections',
        VERIFIED='Coverage/hash invariants, unchanged accepted code/baseline/prior QA evidence and stopped PG55455; no fresh supplier/SQL proof',
        full_qa_sample_unchanged=dict(reviewed=12,passed=11,failed=1,unreviewed=388),
        database_started_this_scope=False,SAFE_TO_POWER_OFF='YES',safe_to_power_off=True,
        shutdown_receipt='SHUTDOWN.json',validation='INVENTORY_VALIDATION.json',
        NEXT='OPERATOR_REVIEW_PRODUCT_KIND_INVENTORY_AND_AUTHORIZE_BATCH_FIX',STOP=True,completed_at=r.now())
    r.save(OUT/'FINAL_CHECKPOINT.json',checkpoint)
    recovery='''# Recovery / next operator gate

LTT_USS_PRODUCT_KIND_IMPACT_SWEEP_VERIFIED. STOP.
NEXT=OPERATOR_REVIEW_PRODUCT_KIND_INVENTORY_AND_AUTHORIZE_BATCH_FIX.

DONE: inventory2453/2453 across four suppliers; all392 risk candidates individually reviewed.
362 VALID_MACHINE;23 CONFIRMED_FALSE_MACHINE;5 OTHER_KIND_DEFECT;2 OPERATOR_REVIEW_REQUIRED.
Confirmed persisted28 = MSK16 + SIB12; three persisted technical clusters (8/1/19).
Separate180 computed USS terminals scanned;20 candidates reviewed =5 false lathe/tooling,3 valid machine variants,12 nonproduct-page applicability reviews. Terminal decisions remain unchanged. No DB correction for terminals.
Row129/ID1910 remains machine/HIGH; expected accessory/fixture. ID/title/description/42000RUB/OutOfStock/hash/time preserved. Row88/ID241 remains an independent source entity; no model-based or -rub URL deduplication.
Two pending persisted rows: SIB941 JB60 and SIB958 JB32S; hand-tool versus complete compact device boundary lacks explicit actuation evidence. Excluded from confirmed28. Pumps/chiller are five OTHER_KIND_DEFECT, not forced into spare/component.

APPLIED: local audit artifacts/helpers/report/checkpoint only. NO classifier/parser/adapter changes, DB writes, source repair, backfill, new baseline, RUN2, FULLQA continuation, matching, selection, Sterbrust, ESOL, finalizer, Git publication, deployment or cron.
VERIFIED: all facts/journals/QA-record hashes and candidate coverage; accepted11 code and classifier/callsite files unchanged; old5769 QA sealed files unchanged. Source material PACKAGED-EVIDENCE, sold-object interpretation INFERENCE, known row129 OPERATOR-CONFIRMED. No fresh SQL or supplier HTTP.
PG55455 never started in this scope; fresh stopped-state receipt proves17.11/system7696153366151515704/shut down,0 workers/listeners,postmaster.pid absent. SAFE_TO_POWER_OFF=YES for checked LTT/USS runtime.

Review PRODUCT_KIND_CONFIRMED_DEFECTS.json, PRODUCT_KIND_DEFECT_CLUSTERS.json, PRODUCT_KIND_OPERATOR_REVIEW.json, ROW129_EVIDENCE.json and CLASSIFIER_ARCHITECTURE.json. Any future generic fix needs a new exact operator scope and must preserve362 valid machine candidates. Twelve terminal applicability reviews are category/reference pages, not confirmed product defects.
FULLQA remains blocked:12/400 reviewed,11PASS/1FAIL,388unreviewed. This sweep does not add FULLQA approvals. Stage6 BLOCKED_IMPORT_CONTRACT_EVIDENCE. Production/55452KAMI remain outside scope.
'''
    r.atomic_bytes(OUT/'RECOVERY_NEXT.md',recovery.encode('utf8'))
    root=Path(r'D:\documents\personal\ChatGPT\Стербруст')
    report='''# LTT + USS: влияние ошибок product_kind — 2026-10-15

**LTT_USS_PRODUCT_KIND_IMPACT_SWEEP_VERIFIED / STOP**

DONE: проинвентаризированы2453/2453 сохранённых товара и рассмотрены392/392 кандидата. Отдельно проверены180 вычисленных терминальных типов USS и20 кандидатов среди них. Категория YUG не имеет вычисленного product_kind.
APPLIED: только локальные материалы аудита, вспомогательные скрипты, отчёт и checkpoint. Исправления не применялись.
VERIFIED: полнота/уникальность решений, хеши исходных материалов и записей прошлого QA, неизменность принятого кода/классификатора и5769 прежних файлов QA, остановленная PG55455. Сохранённые источники — PACKAGED-EVIDENCE; семантическая интерпретация — INFERENCE; технические причины — REPOSITORY-VERIFIED; ожидаемый тип строки129 — OPERATOR-CONFIRMED.

| Источник | Инвентарь | Кандидаты / рассмотрено | Полноценные машины | Ложный тип станка | Другой дефект типа | Оператор |
|---|---:|---:|---:|---:|---:|---:|
| MSK |369|131/131|115|13|3|0|
| SIB |1266|128/128|114|10|2|2|
| YUG |591|104/104|104|0|0|0|
| USS |227|29/29|29|0|0|0|
| Всего |2453|392/392|362|23|5|2|

Приоритет будущей коррекции БД:28 строк, MSK16/SIB12. Среди них23 оснастки/комплектующих/принадлежностей/инструмента и5 самостоятельных вспомогательных агрегатов с неверным типом: четыре вакуумных насоса и чиллер. Их нельзя автоматически объявлять запчастями. Коррекция сейчас не разрешена и не выполнена. В YUG/USS подтверждённых persisted-дефектов в сгенерированной выборке нет; это не сертификация всей таксономии.

## Архитектура и причины

Общий классификатор — sterbrust_matching/product_identity.py:170 classify_product_kind(name,category). MSK/SIB ltt.py:219, YUG ltt_yug.py:176 и USS uss_stanko.py:477 вызывают общий source_evidence в ltt_uss_html.py:74-87. Входы типа: заголовок и категория. Описание не используется. Локальные sold-title переопределения spare/tooling одинаковы для всех четырёх источников; нет отдельного MSK-классификатора.

В ingestion вывод попадает в enrichment_evidence и raw_data source_products/offers (commercial_persistence.py:239-240). Это не ключ идентичности, не числовая цена и не признак наличия. Source ID остаётся главным доказательством идентичности. Однако сам общий классификатор используется шире: matching.py проверяет согласованность типов; classify_model_role использует тип для роли модели; identity_service.py:158 и safe_new_product_decision в product_identity.py:406-416 используют его при решении о новой карточке/принадлежности; characteristic_evidence и profile_for_kind выбирают признаки. Также есть вызовы в intervesp/kami/proposals/offline review/admin. Все32 прямых вызова, их аргументы, строки и хеши сохранены в CLASSIFIER_ARCHITECTURE.json вместе с упорядоченной таксономией, алиасами и compatibility markers. Эти процессы только изучены и не запускались. Прямого использования kind в offer_selection.py и source-ID gate ltt_uss_identity.py нет; косвенное влияние на matching/identity нельзя считать отсутствующим.

Три подтверждённых persisted-механизма:

1. **COMPATIBILITY_PREPOSITION_NOT_STRIPPED —8:** MSK88,129,300,302; SIB914,915,1143,1144. «На» не отделяет станок-хозяин: machine_name/HIGH. Это балки, линза, упор и линейки.
2. **HOST_MACHINE_TOKEN_OVERRIDES_SOLD_OBJECT —1:** MSK40, пильный диск104мм/30зубьев; родительный «станка» внутри заголовка узла ошибочно даёт machine_name/HIGH.
3. **CATEGORY_FALLBACK_OVERRIDES_UNRECOGNIZED_SOLD_OBJECT —19:** MSK30,32,38,50,87,203,240,313,314,315,344; SIB10,29,938,955,960,1159,1161,1163. Нераспознанный продаваемый предмет получает machine/milling_machine/grinder из категории с MEDIUM. Сюда входят все5 вспомогательных агрегатов.

Каждая строка имеет собственные source ID/URL, сохранённый trace, коммерческие факты и ссылки/хеши собственного DOM. Модель, цена, наличие или суффикс -rub не использованы для объединения. Возможные общие исправления и риски ложного понижения записаны в кластерах как INFERENCE_NOT_IMPLEMENTED.

## Известная строка129 и неопределённость

MSK129 / ID1910: продаётся пневмобалка PB-3000/ПБ3200, устанавливаемая в Т-образный паз каретки для фиксации заготовки. CONFIRMED_FALSE_MACHINE_FIXTURE; ожидается accessory/fixture. Текущее machine/HIGH/alias станок/source machine_name сохранено. Цена42000RUB, OutOfStock, полный собственный текст, response/evidence SHA и время наблюдения2026-10-14 10:39:13.146204+00:00 сохранены в ROW129_EVIDENCE.json. Строка88/ID241 — отдельная сущность.

SIB941 JB60 и958 JB32S оставлены OPERATOR_REVIEW_REQUIRED: описание указывает на подрезатель со сменными ножами (у958 масса0.6кг), но не устанавливает однозначно ручной привод/полноценное стационарное исполнение. Цена/знакомство с моделью не заменяют доказательство. Эти две строки не включены в28 подтверждённых.

## Терминальные строки и защита полноценных станков

Отдельные20 терминальных кандидатов:5 ложных lathe/HIGH (USS191,194,195,196,197),3 корректных типа многовариантных токарных станков (204-206),12 категорий/справочных страниц с вопросом применимости товарного типа. Для последних machine может обозначать тему страницы, но не продаваемую сущность; они не названы дефектами. Сохранены OPERATOR_REVIEW_REQUIRED по применимости, поскольку перечень решений не содержит NOT_APPLICABLE. Решения AMBIGUOUS/MULTI_VARIANT не менялись, будущих DB-коррекций для терминалов сейчас0.

Четвёртый, только терминальный кластер: LATHE_ADJECTIVE_PRECEDES_CUTTING_TOOL_TAXON —5. Заголовки продают резцы/наборы/пластины; широкий алиас токарн* выбирает lathe раньше tool, а локальный резец* не покрывает резцы/резцов. Таблицы содержат самостоятельные артикулы и предложения; нельзя создавать один фиктивный станок.

Будущий фикс обязан сохранить362 подтверждённых машины в риск-выборке. Примеры: полный лазерный станок с чиллером в комплекте MSK64; заточные станки для инструмента других станков MSK358/359; самостоятельный пневматический подрезатель SIB959 с педалью/компрессором; станок снятия свесов YUG386; заточной USS350. Глобальная замена «на станок» на accessory небезопасна. YUG541 имеет машинный заголовок и несогласованное лазерное описание: VALID_MACHINE здесь подтверждает только полноценный продаваемый станок, не достоверность каждого подтипа/описания. У SIB1085 назначение пусто, но собственный H1 прямо продаёт рейсмусовый MB1010HL; пустота не превращает его в принадлежность.

## Границы и завершение

CLASSIFIER/PARSER/ADAPTER_CHANGES=0; DB/SOURCE_WRITES=0; SUPPLIER_HTTP=0. База не запускалась, SQL не выполнялся.2453/2453/2533/2533 — PACKAGED-EVIDENCE последнего принятого снимка, не новая SQL-проверка. Нового RUN2, backfill, matching, selection, Sterbrust/ESOL, finalizer, публикации Git, production/deploy/cron нет. Тесты не перезапускались: поведение кода не менялось, проверены целостность материалов и полнота аудита.

Свежая проверка runtime: PostgreSQL17.11, system7696153366151515704, shut down; рабочих процессов0, порт55455 закрыт, postmaster.pid отсутствует. SAFE_TO_POWER_OFF=YES для проверенного изолированного LTT/USS runtime.
FULL_QA_VERIFIED=NO: прежний результат12/400=11PASS/1FAIL,388 не рассмотрено, остаётся неизменным. Stage6 BLOCKED_IMPORT_CONTRACT_EVIDENCE.

Артефакты: [FINAL_CHECKPOINT.json](../work/sku-service-ltt-uss/reports/LTT_USS_INTEGRATION_2026-10-13/PRODUCT_KIND_IMPACT_SWEEP_2026-10-15/FINAL_CHECKPOINT.json), [решения](../work/sku-service-ltt-uss/reports/LTT_USS_INTEGRATION_2026-10-13/PRODUCT_KIND_IMPACT_SWEEP_2026-10-15/PRODUCT_KIND_SEMANTIC_DECISIONS.json), [дефекты](../work/sku-service-ltt-uss/reports/LTT_USS_INTEGRATION_2026-10-13/PRODUCT_KIND_IMPACT_SWEEP_2026-10-15/PRODUCT_KIND_CONFIRMED_DEFECTS.json), [кластеры](../work/sku-service-ltt-uss/reports/LTT_USS_INTEGRATION_2026-10-13/PRODUCT_KIND_IMPACT_SWEEP_2026-10-15/PRODUCT_KIND_DEFECT_CLUSTERS.json).

NEXT=OPERATOR_REVIEW_PRODUCT_KIND_INVENTORY_AND_AUTHORIZE_BATCH_FIX / STOP.
'''
    r.atomic_bytes(root/'reports/LTT_USS_PRODUCT_KIND_IMPACT_SWEEP_2026-10-15.md',report.encode('utf8'))
    task=root/'tasks/active/2026-10-15_LTT_USS_PRODUCT_KIND_IMPACT_SWEEP.md'
    body=task.read_text(encoding='utf8').replace('Status ACTIVE.','Status DONE / VERIFIED.').replace('DONE/APPLIED/VERIFIED pending.','DONE/APPLIED/VERIFIED recorded below.')
    body+='\n## Completion\n\n'+recovery
    task.write_text(body,encoding='utf8');dest=root/'tasks/completed'/task.name
    assert not dest.exists();task.rename(dest)
    update='''
## Product-kind impact sweep VERIFIED — 2026-10-15

DONE:2453/2453 inventory;392/392 risk decisions:362valid,23false-machine,5other-kind-defects,2operator.
Confirmed persisted28(MSK16/SIB12),three clusters; terminal20reviewed=5false/3valid/12applicability-review, fourth terminal-only cluster.
APPLIED:audit evidence/helpers/report/checkpoint only; no classifier/parser/DB/source correction. Row129 still machine/HIGH.
REPOSITORY-VERIFIED:accepted code and5769 priorQAfiles unchanged; source PACKAGED-EVIDENCE, semantics INFERENCE.
PG55455neverstarted/remainsstopped,workers0/portclosed/pidabsent;SAFE_TO_POWER_OFF=YES.
HTTP0/DBwrites0;FULLQANO/finalizerNO. OldFULLQA12/400(11PASS/1FAIL),388unreviewed unchanged.
[Report](reports/LTT_USS_PRODUCT_KIND_IMPACT_SWEEP_2026-10-15.md) · [Task](tasks/completed/2026-10-15_LTT_USS_PRODUCT_KIND_IMPACT_SWEEP.md).
NEXT OPERATOR_REVIEW_PRODUCT_KIND_INVENTORY_AND_AUTHORIZE_BATCH_FIX / STOP.

'''
    for filename in ['CURRENT_STATE.md','PROJECT_INDEX.md']:
        path=root/filename;head,rest=path.read_text(encoding='utf8').split('\n',1)
        path.write_text(head+'\n'+update+rest,encoding='utf8')
    r.save(r.BASE/'CHECKPOINT.json',checkpoint)
    seal={str(p.relative_to(OUT)):r.digest(p.read_bytes()) for p in OUT.rglob('*') if p.is_file() and p.name!='FINAL_ARTIFACT_SEAL.json'}
    r.save(OUT/'FINAL_ARTIFACT_SEAL.json',seal)
    print(dict(status=checkpoint['status'],artifacts_sealed=len(seal),old_qa_files_unchanged=len(oldseal),SAFE_TO_POWER_OFF='YES',STOP=True))

if __name__=='__main__':main()
