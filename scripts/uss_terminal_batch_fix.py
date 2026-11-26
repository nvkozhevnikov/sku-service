"""Authorized exact eight saved-source repairs; gates precede all database writes."""
from collections import Counter
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
import copy
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts import audit_uss_terminal_sweep as sweep
from scripts import resume_uss_h2_url_handoff as accepted
r,o,q=sweep.r,sweep.o,sweep.q
PHASE=r.BASE/'USS_TERMINAL_BATCH_FIX_2026-10-15'
EXPECTED={20,53,61,64,65,297,305,405}
OPERATOR={1,12,17,306,307,318}
ADAPTER='universal_supplier/adapters/uss_stanko.py'

def rows():
    for e in r.load(q.PLAN)['rows']:
        p=o.ROOT/e['journal'];assert r.digest(p.read_bytes())==e['journal_sha256'];yield p,r.load(p)

def saved(row):
    m=row['result'];response=r.load(r.OUT/m['response_record']);raw=(r.OUT/response['raw_path']).read_bytes()
    body=(r.OUT/'EVIDENCE/uss_stanko'/(m['evidence_sha256']+'.html')).read_bytes()
    assert r.digest(raw)==m['response_sha256']==response['response_sha256']
    assert r.digest(body)==m['evidence_sha256'] and o.sanitise_for_charset(raw,m['charset'])==body
    return raw,body

def evaluate(row):
    raw,body=saved(row);m=row['result'];p=r.parse('uss_stanko',body,m['final_url'])
    status,diag=q.accepted.f._card_gate('uss_stanko',raw.decode(m['charset'] or 'utf8','replace'),m['final_url'])
    outcome='PERSISTED' if status.value=='SUCCESS' else ('MULTI_VARIANT' if 'MULTI_VARIANT' in diag else status.value)
    return p,status,diag,outcome

def before():
    assert not PHASE.exists();assert r.code_hashes()==r.load(accepted.PHASE/'NEW_CODE_SEAL.json')
    PHASE.mkdir();r.save(PHASE/'OLD_CODE_SEAL.json',r.code_hashes())
    r.atomic_bytes(PHASE/'OLD_USS_ADAPTER.py',Path(ADAPTER).read_bytes())
    r.atomic_bytes(PHASE/'OLD_OWN_HEADING_TEST.py',Path('tests/test_uss_own_heading.py').read_bytes())
    seal={}
    for base in [sweep.OUT,q.OUT,accepted.PHASE]:
        for p in base.rglob('*'):
            if p.is_file():seal[str(p.relative_to(o.ROOT))]=r.digest(p.read_bytes())
    r.save(PHASE/'HISTORICAL_EVIDENCE_SEAL.json',seal)
    facts=[];fixtures=[]
    with o.offline_guard() as network:
      for path,row in rows():
        if row['supplier']!='uss_stanko':continue
        p,status,diag,outcome=evaluate(row);assert outcome==row['outcome']
        payload=asdict(p);facts.append(dict(index=row['index'],outcome=outcome,payload=payload,payload_sha256=r.digest(r.encoded(payload)),journal=str(path.relative_to(o.ROOT)),journal_sha256=r.digest(path.read_bytes())))
        if row['index'] in EXPECTED|OPERATOR:
            raw,body=saved(row);dest=Path('tests/fixtures/uss_terminal_batch')/f"row{row['index']}.html"
            r.atomic_bytes(dest,body);fixtures.append(dict(index=row['index'],url=row['url'],fixture=str(dest),sha256=r.digest(body),raw_sha256=r.digest(raw),observed_at=row['result']['observed_at']))
    assert len(facts)==407
    r.save(PHASE/'BEFORE_407.json',facts);r.save(Path('tests/fixtures/uss_terminal_batch/PROVENANCE.json'),fixtures)
    r.save(PHASE/'BEFORE_PREPARATION.json',dict(status='PASS',network=network,baseline=r.load(q.BASELINE)['totals'],checked_at=r.now()))
    print('Before-state and14exact saved fixtures sealed; PG not started')

def history():
    for k,v in r.load(PHASE/'HISTORICAL_EVIDENCE_SEAL.json').items():assert r.digest((o.ROOT/k).read_bytes())==v,k

def blocked_diagnostics():
    """Read-only impact evidence after failed tests; no repairs/retest/DB start."""
    import xml.etree.ElementTree as ET
    suite=ET.parse(PHASE/'TESTS.xml').getroot()
    cases=suite.findall('.//testcase')
    failures=[dict(name=c.get('classname')+'::'+c.get('name'),traceback='\n'.join(n.text or '' for n in c.findall('failure'))) for c in cases if c.find('failure') is not None]
    receipt=dict(total=len(cases),passed=sum(not any(c.find(tag) is not None for tag in ['failure','error','skipped']) for c in cases),fail=len(failures),error=len(suite.findall('.//error')),skip=len(suite.findall('.//skipped')),failures=failures,short_temp_root=r'D:\documents\personal\ChatGPT\Стербруст\tmp\lttqa',no_assertions_weakened=True,retest_run=False)
    assert receipt['fail']>0
    r.save(PHASE/'TEST_RECEIPT.json',receipt)
    old={x['index']:x for x in r.load(PHASE/'BEFORE_407.json')};comparison=[];affected=[];counts=Counter()
    with o.offline_guard() as network:
      for path,row in rows():
        if row['supplier']!='uss_stanko':continue
        i=row['index'];p,status,diag,outcome=evaluate(row);payload=asdict(p);sha=r.digest(r.encoded(payload))
        previous=old[i];changed=sha!=previous['payload_sha256'];counts[outcome]+=1
        comparison.append(dict(index=i,url=row['url'],old_outcome=previous['outcome'],new_outcome=outcome,old_payload_sha256=previous['payload_sha256'],new_payload_sha256=sha,payload_changed=changed,outcome_changed=outcome!=previous['outcome']))
        if changed or outcome!=previous['outcome']:
            affected.append(dict(index=i,url=row['url'],source_id=p.site_internal_id,old_outcome=previous['outcome'],new_outcome=outcome,old_payload=previous['payload'],new_payload=payload,old_journal=str(path.relative_to(o.ROOT)),old_journal_sha256=r.digest(path.read_bytes()),source_metadata=row['result'],db_applied=False,original_observation_time=row['result']['observed_at']))
        if len(comparison)%50==0:print(f'BLOCKED READONLY IMPACT {len(comparison)}/407',flush=True)
    history();seal=r.code_hashes();oldseal=r.load(PHASE/'OLD_CODE_SEAL.json')
    changedcode=[k for k in seal if seal[k]!=oldseal[k]];assert changedcode==[ADAPTER]
    r.save(PHASE/'NEW_CODE_SEAL_NOT_ACCEPTED.json',seal)
    changedindices=sorted(x['index'] for x in affected);extra=sorted(set(changedindices)-EXPECTED)
    impact=dict(status='READONLY_DIAGNOSTIC_AFTER_FAILED_TEST_GATE_NOT_DB_AUTHORITY',total=407,changed_indices=changedindices,unexpected_indices=extra,
        existing_persisted_payload_changes=sum(x['payload_changed'] for x in comparison if x['old_outcome']=='PERSISTED'),
        valid_ambiguous_payload_changes=sum(x['payload_changed'] for x in comparison if x['old_outcome']=='AMBIGUOUS' and x['index'] not in EXPECTED),
        multi_variant_payload_changes=sum(x['payload_changed'] for x in comparison if x['old_outcome']=='MULTI_VARIANT'),
        operator_accepted_ambiguous_payload_changes=sum(x['payload_changed'] or x['outcome_changed'] for x in comparison if x['index'] in OPERATOR),
        new_outcomes=counts,expected_only_eight_changed=set(changedindices)==EXPECTED,network=network,rows=comparison)
    r.save(PHASE/'IMPACT_COMPARISON_407.json',impact);r.save(PHASE/'AFFECTED_ROWS.json',affected)
    transitions=[]
    for cluster,indices,oldname,newname in [
        ('CLUSTER_A',[53,61,64,65,297,305],'VERTICAL_KEY_VALUE_COMMERCIAL_ONLY','SAFE_SINGLE_PRODUCT_COLUMN_HEADER_COMMERCIAL_SUPPORT'),
        ('CLUSTER_B',[20],'NO_MAIN_ROW_OWNERSHIP_IN_SHARED_COMMERCIAL_TABLE','EVIDENCE_BACKED_MAIN_PRODUCT_ROW_OWNERSHIP'),
        ('CLUSTER_C',[405],'GLOBAL_H2_GUARDS_IGNORE_LOCAL_MAIN_PRODUCT_SECTION','LOCAL_MAIN_PRODUCT_SECTION_OWNERSHIP_WITH_ACCESSORY_ISOLATION')]:
        transitions.append(dict(cluster=cluster,indices=indices,OLD=oldname,NEW=newname,status='LOCAL_IMPLEMENTATION_TEST_GATE_FAILED_NOT_ACCEPTED',old_code_hashes=oldseal,new_code_hashes=seal,evidence_class='REPOSITORY-VERIFIED',tests='TEST_RECEIPT.json',impact='IMPACT_COMPARISON_407.json',db_applied=False))
    r.save(PHASE/'CLUSTER_TRANSITIONS.json',transitions)
    decisions=copy.deepcopy(r.load(sweep.OUT/'USS_AMBIGUOUS_122_DECISIONS.json'))
    for d in decisions['rows']:
        if d['index'] in OPERATOR:
            d.update(decision='VALID_AMBIGUOUS_FAMILY_URL_CHILD_IDENTITY_NOT_PROVEN' if d['index'] in {1,12,17} else 'VALID_AMBIGUOUS_DATASHEET_NO_COMMERCIAL_OFFER',operator_question=None,interpretation_class='OPERATOR-CONFIRMED',resolution_spec='specs/incoming/2026-10-15_LTT_USS_TERMINAL_BATCH_FIX.txt')
    decisions.update(status='CURRENT_ACCEPTED_POPULATION_NO_BACKFILL',counts=dict(Counter(d['decision'] for d in decisions['rows'])),operator_review_remaining=0,revised_population_created=False)
    r.save(PHASE/'TERMINAL_INVENTORY_OPERATOR_RESOLVED.json',decisions)
    r.save(PHASE/'BACKFILL_RECEIPT.json',dict(status='NOT_STARTED_TEST_GATE_FAILED',applied_rows=[],db_started=False,db_writes=0,source_products_created=0,offers_created=0,captures_created=0,observations_created=0))
    r.save(PHASE/'HTTP_ZERO_PROOF.json',dict(**network,supplier_http=0,db_started=False,db_writes=0))
    blocker=dict(status='LTT_USS_TERMINAL_BATCH_FIX_BLOCKED',stage='REGRESSION_TEST_GATE',blocker='9 tests failed in historical frozen_run() code-seal assertion before exercising target behavior',root_cause='HISTORICAL_RUN1_CODE_SEAL_REQUIRED_BY_LEGACY_TEST_HARNESS_WITH_CURRENT_REVISED_CODE',confirmed_scope=sorted(EXPECTED),new_scope=extra,tests=receipt,impact_summary={k:v for k,v in impact.items() if k!='rows'},db_started=False,db_correction_applied=False,backfill_run=False,revised_baseline_created=False,new_run2_run=False,supplier_http=0,full_qa_resumed=False,finalizer_started=False,matching_run=False,offer_selection_run=False,sterbrust_writes=0,esol_writes=0,accepted_baseline=r.load(q.BASELINE)['totals'],baseline_claim='PACKAGED-EVIDENCE_NO_DATABASE_START',safe_to_power_off=False,next='OPERATOR_REVIEW_FAILED_TEST_GATE_AND_AUTHORIZE_CHECKPOINT_AWARE_HARNESS_RESUME',checked_at=r.now())
    r.save(PHASE/'BLOCKER.json',blocker)
    print(dict(tests={k:receipt[k] for k in ['total','passed','fail','error','skip']},changed_indices=changedindices,unexpected_indices=extra,db_started=False),flush=True)

if __name__=='__main__':
    (blocked_diagnostics if len(sys.argv)>1 and sys.argv[1]=='blocked-diagnostics' else before)()
