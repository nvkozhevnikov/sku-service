"""Small stratified real-case regressions; full raw snapshots stay out of Git."""
import json
from pathlib import Path
from collections import defaultdict
from universal_supplier.matching_policy_v22 import kind_proof
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'reports/MATCHING_V2_2_AI_REVIEW'

def trim(row):
    return {k:row.get(k) for k in ('source','external_id','name','brand','model','execution','source_category','source_url',
        'card_kind','observed_properties','properties','evidence_ref','sterbrust_product_id','active','snapshot_hash')}

def main():
    corpus=json.loads((OUT/'V22_AFTER.json').read_bytes())['rows']
    targets=json.loads((OUT/'CANONICAL_VIEWS.json').read_bytes());groups=defaultdict(list)
    for r in corpus:
        if r['classification_before']=='EXISTING_CONFIRMED' or r['classification_after']!='EXISTING_CONFIRMED':continue
        c=r['audit_v22']['confirmed'];groups[(r['source'],c['rule_id'],kind_proof(r)['kind'])].append(r)
    cases=[]
    for group,rows in sorted(groups.items()):
        for r in rows[:2]:
            ids=r['audit_v22']['retrieval_ids'];cases.append({'source':trim(r),
                'targets':{pid:trim(targets[pid]) for pid in ids},'expected_id':r['target_after'],'expected_rule':r['audit_v22']['confirmed']['rule_id']})
    held=[]
    for r in corpus:
        blocker=next((b for b in r['audit_v22']['blocking_reasons'] if b in ('V22_EXPLICIT_CATEGORY_AUTOMATION_CONTRADICTION','V22_EXPLICIT_FRAME_CONTRADICTION')),None)
        if not blocker:continue
        held.append({'source':trim(r),'targets':{pid:trim(targets[pid]) for pid in r['audit_v22']['retrieval_ids']},
                     'expected_blocker':blocker})
    fixture={'provenance':'Saved-evidence AI-inspected stratified rule positives and actual automation-quality counterexamples; not live production/human labels',
        'registry_sha256':'40e4f2276c7d58754cfc755ca08453994193419cba6843db3170ec640007fd65',
        'cases':cases,'counterexamples':held}
    p=ROOT/'tests/fixtures/matching_v22_kind_calibrations.json'
    p.write_text(json.dumps(fixture,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print('Frozen',len(cases),'real positives /',len(held),'real holds /',p.stat().st_size,'bytes')

if __name__=='__main__':main()
