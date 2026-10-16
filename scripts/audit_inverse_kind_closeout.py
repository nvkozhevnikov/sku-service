"""Seal completed impact analysis, never a product finalizer or mutation runner."""
from scripts.audit_inverse_kind import *
from scripts.audit_inverse_kind_decisions import FALSE,VALID,REVIEW,NA
import json

def main():
    unchanged()
    decisions=r.load(OUT/'INVERSE_KIND_SEMANTIC_DECISIONS.json')['rows'];terminal=r.load(OUT/'TERMINAL_INVERSE_KIND_SEMANTIC_DECISIONS.json')['rows']
    assert Counter(a['semantic_decision'] for a in decisions)=={FALSE:97,VALID:597,REVIEW:106}
    assert Counter(a['semantic_decision'] for a in terminal)=={FALSE:4,REVIEW:13,NA:44}
    assert len(decisions)==len({(a['supplier'],a['index']) for a in decisions})==800
    assert {(a['supplier'],a['index']) for a in decisions}=={(a['supplier'],a['index']) for a in r.load(OUT/'INVERSE_KIND_RISK_CANDIDATES.json')['rows']}
    assert len(r.load(OUT/'ALL_PERSISTED_KIND_INVENTORY.json')['rows'])==2453
    assert len(r.load(OUT/'INVERSE_KIND_NON_MACHINE_INVENTORY.json')['rows'])==1344
    for filename in ['INVERSE_KIND_NON_MACHINE_INVENTORY.json','TERMINAL_INVERSE_KIND_INVENTORY.json']:
        for a in r.load(OUT/filename)['rows']:assert r.digest((OUT/a['facts']).read_bytes())==a['facts_sha256']
    assert r.load(OUT/'PREVIOUS_29_REGRESSION_PROTECTION.json')['count']==29
    assert r.load(QA/'FRESH_400_SEMANTIC_RESULTS.json')['reviewed']==8
    runtime=json.loads((OUT/'SAFE_RUNTIME_VERIFICATION.json').read_text('utf-8-sig'))
    assert runtime['SAFE_TO_POWER_OFF']=='YES' and not runtime['database_started_in_this_task']
    before=r.load(OUT/'PREFLIGHT.json')['git'];after=q.git()
    for k in ['HEAD','branch','develop','production','diff_stat']:assert before[k]==after[k],('TRACKED_GIT_DRIFT',k)
    r.save(OUT/'GIT_FINAL.json',after)
    cp=dict(status='LTT_USS_INVERSE_PRODUCT_KIND_IMPACT_SWEEP_VERIFIED',PERSISTED_ROWS_INVENTORIED='2453/2453',NON_MACHINE_ROWS_INVENTORIED=1344,RISK_CANDIDATES=800,SEMANTICALLY_REVIEWED=800,CONFIRMED_FALSE_NON_MACHINE=97,TYPED_FALSE_NON_MACHINE=31,UNKNOWN_MACHINE_COVERAGE_GAPS=66,VALID_NON_MACHINE=597,OPERATOR_REVIEW_REQUIRED=106,TERMINAL_INPUTS_COMPUTED=180,TERMINAL_NON_MACHINE_KINDS=118,TERMINAL_CANDIDATES=61,TERMINAL_SEMANTICALLY_REVIEWED=61,TERMINAL_CONFIRMED_DEFECTS=4,TERMINAL_OPERATOR_REVIEW_REQUIRED=13,TERMINAL_NOT_APPLICABLE=44,ROW82_CONFIRMED='YES',ROW82_CURRENT='spare/HIGH',ROW82_EXPECTED='machine',AFFECTED_SUPPLIERS=['ltt_msk','ltt_sib','ltt_yug','uss_stanko'],AFFECTED_BY_SUPPLIER=dict(ltt_msk=20,ltt_sib=11,ltt_yug=47,uss_stanko=19),DEFECT_CLUSTERS=3,PREVIOUS_29_REGRESSION_RISK='ASSESSED',PREVIOUS_29_FRESH_PROTECTED=29,DB_WRITES=0,SQL_CONNECTIONS=0,SOURCE_WRITES=0,SUPPLIER_HTTP=0,FULL_QA_VERIFIED='NO',FULL_QA_RESUMED='NO',FINALIZER_STARTED='NO',MATCHING_RUN='NO',SELECTION_RUN='NO',PARSER_CHANGES=0,CLASSIFIER_CHANGES=0,ADAPTER_CHANGES=0,SAFE_TO_POWER_OFF='YES',POSTGRES_STARTED='NO',NEXT='OPERATOR_REVIEW_AND_SCOPED_INVERSE_KIND_BATCH_FIX',STOP=True,DONE='Complete offline inventory/rule audit/candidate semantic decisions and29 protection',APPLIED='New audit helpers/evidence/docs only; no data/business-code changes',VERIFIED='Source/SQL snapshots PACKAGED-EVIDENCE; technical checks REPOSITORY-VERIFIED; semantic decisions INFERENCE; isolated runtime stop LIVE-VERIFIED, no fresh live SQL',accepted_business_baseline=str(P.relative_to(o.ROOT)),report='reports/LTT_USS_INVERSE_KIND_IMPACT_SWEEP_2026-10-16.md',checked_at=r.now())
    for path in [OUT/'FINAL_CHECKPOINT.json',OUT/'CHECKPOINT.json',r.BASE/'CHECKPOINT.json']:r.save(path,cp)
    r.atomic_bytes(OUT/'RECOVERY_NEXT.md',('LTT_USS_INVERSE_PRODUCT_KIND_IMPACT_SWEEP_VERIFIED / STOP.\n\nRead root report,97-row confirmed list,106operator cases,61terminal decisions,63rule marker audit and fresh29 protection. Accepted business/DB baseline remains PRODUCT_KIND_29_RESUME_2026-10-16. This is offline analysis, not repair authorization.\n97persisted=31wrong typed labels+66unknown machine gaps;4terminal computed kind defects do not create products. Do not merge same-model source IDs or URLs based on -rub.\nFuture code/metadata batch requires operator-reviewed scope and new full replay/regression gates. No400QA continuation/finalizer/matching/selection/publication/production. FULL_QA_VERIFIED=NO; Stage6 BLOCKED_IMPORT_CONTRACT_EVIDENCE.\nPG55455 never started in this task, freshly verified shut down/portclosed/pidabsent/workers0,SAFE_TO_POWER_OFF=YES. HTTP0/SQLconnections0/DBwrites0.\nNEXT OPERATOR_REVIEW_AND_SCOPED_INVERSE_KIND_BATCH_FIX / STOP.\n').encode())
    root=o.ROOT.parent.parent
    docs=['reports/LTT_USS_INVERSE_KIND_IMPACT_SWEEP_2026-10-16.md','tasks/completed/2026-10-16_LTT_USS_INVERSE_KIND_SWEEP.md','specs/incoming/2026-10-16_LTT_USS_INVERSE_KIND_SWEEP.txt','CURRENT_STATE.md','PROJECT_INDEX.md','ANTIPATTERNS.md']
    r.save(OUT/'DOCUMENTATION_SEAL.json',{p:r.digest((root/p).read_bytes()) for p in docs})
    paths=['scripts/audit_inverse_kind.py','scripts/audit_inverse_kind_markers.py','scripts/audit_inverse_kind_decisions.py','scripts/audit_inverse_kind_regression.py','scripts/audit_inverse_kind_closeout.py']
    r.save(OUT/'AUDIT_CODE_SEAL.json',{p:r.digest((o.ROOT/p).read_bytes()) for p in paths})
    seal={str(p.relative_to(OUT)):r.digest(p.read_bytes()) for p in OUT.rglob('*') if p.is_file() and p.name!='FINAL_ARTIFACT_SEAL.json'}
    r.save(OUT/'FINAL_ARTIFACT_SEAL.json',seal)
    for n,sha in seal.items():assert r.digest((OUT/n).read_bytes())==sha
    unchanged()
    print(dict(status=cp['status'],persisted=2453,non_machine=1344,reviewed=800,confirmed=97,terminal_reviewed=61,terminal_confirmed=4,protected29=29,sealed_files=len(seal),db_writes=0,http=0,safe='YES',HEAD=after['HEAD'].strip()),flush=True)

if __name__=='__main__':main()
