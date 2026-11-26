"""Disposition of existing F01/F03 dry-run only. No matcher, network or SQL."""
from pathlib import Path
import argparse,csv,json,hashlib
from collections import Counter

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def load_csv(p):
    with p.open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))
def reason_details(row):
    conflicts=json.loads(row['conflicts']);details=[]
    for name,value in conflicts.items():
        if isinstance(value,dict):
            source=value.get('source_values',[]);candidate=value.get('candidate_values',[])
            details.append({'field':name,'result':value.get('result','VALUE_CONTRADICTION'),'source_values':source,'candidate_values':candidate,'raw':value,
                'required_evidence':'Resolve source and candidate measurements to one sold configuration, retaining role/unit provenance. Do not choose a convenient duplicate.' if 'AMBIGUOUS' in value.get('result','') else 'Confirm brand and complete execution/model from saved product evidence; do not infer identity from title similarity.'})
        else:details.append({'field':name,'raw':value})
    return details or [{'field':row['match_method'],'required_evidence':'Exact identity or operator confirmation is required; a name candidate is insufficient.'}]

def review(folder,old_trace,current_trace,out):
    before_json=json.loads((folder/'before.json').read_text(encoding='utf-8'));after_json=json.loads((folder/'after.json').read_text(encoding='utf-8'))
    def key(r):return (r['supplier_code'],r['external_id'])
    before={key(r):r for r in before_json['rows'] if r['pass_mode']=='with_existing_links'}
    after={key(r):r for r in after_json['rows'] if r['pass_mode']=='with_existing_links'}
    assert before.keys()==after.keys() and len(before)==12120
    def trace_key(r):return (r['supplier_namespace'],r['source_external_id'])
    old={trace_key(r):r.get('persisted_sterbrust_product_id','') for r in load_csv(old_trace)}
    current={trace_key(r):r.get('persisted_sterbrust_product_id','') for r in load_csv(current_trace)}
    assert all(current[k]==v for k,v in old.items()),'Preserved ID evidence mismatch'
    risk={(r['supplier'],r['external_id']):r for r in load_csv(folder/'PERSISTED_AUTO_REVIEW_QUEUE.csv')}
    compare_fields=('status','auto_accepted','sterbrust_product_id','match_method','conflicts')
    cases=[];counts=Counter();reason_counts=Counter();source_counts=Counter()
    downgrade=promotion=0;candidate_switch=[]
    for k,left in before.items():
        right=after[k];stored=old.get(k,'')
        down=left['auto_accepted']=='TRUE' and right['auto_accepted']!='TRUE'
        up=left['auto_accepted']!='TRUE' and right['auto_accepted']=='TRUE'
        switch=bool(stored and right['auto_accepted']=='TRUE' and str(right['sterbrust_product_id'])!=stored)
        downgrade+=down;promotion+=up
        if switch:candidate_switch.append(k)
        if not (any(left[f]!=right[f] for f in compare_fields) or k in risk or switch):continue
        if switch:action='HOLD_STORED_ID_REJECT_AUTOMATIC_CANDIDATE_SWITCH'
        elif down:action='HOLD_STORED_LINK_REVIEW_ONLY' if stored else 'REVIEW_NO_AUTO_ASSIGNMENT'
        elif up:action='QUARANTINE_PROMOTION_NOT_APPLIED'
        elif k in risk:action='PREEXISTING_REVIEW_PRESERVE_STORED_LINK'
        else:action='KEEP_REVIEW_CONFLICT_NO_APPLY'
        details=reason_details(right)
        if down:
            source_counts[k[0]]+=1
            reason_counts.update(x['field'] for x in details)
        counts[action]+=1
        cases.append({'supplier_namespace':k[0],'external_id':k[1],'name':right['name'],'persisted_sterbrust_product_id':stored,'before_auto':left['auto_accepted'],'after_auto':right['auto_accepted'],'before_status':left['status'],'after_status':right['status'],'before_candidate':left['sterbrust_product_id'],'after_candidate':right['sterbrust_product_id'],'before_method':left['match_method'],'after_method':right['match_method'],'recommended_action':action,'reasons':details,'source_evidence_class':'PACKAGED-EVIDENCE existing frozen dry-run','disposition_class':'INFERENCE conservative release containment; not semantic acceptance','database_action':'NONE','automatic_promotion':'FORBIDDEN'})
    assert downgrade==210 and promotion==102 and len(candidate_switch)==9
    assert before_json['manual_decisions']==after_json['manual_decisions']==0
    out.mkdir(parents=True,exist_ok=True)
    (out/'CHURN_DISPOSITIONS.json').write_text(json.dumps(cases,ensure_ascii=False,indent=2),encoding='utf-8')
    summary={'status':'CHURN_ANALYZED_AND_CONTAINED_NOT_APPLIED','with_links_cards':len(before),'auto_to_review_conflict':downgrade,'not_auto_to_auto_quarantined':promotion,'downgrades_by_source':dict(source_counts),'downgrade_conflict_keys':dict(reason_counts),'additional_auto_candidate_switches_quarantined':len(candidate_switch),'case_count':len(cases),'dispositions':dict(counts),'persisted_risk_queue':len(risk),'new_patch_downgrades_in_persisted_queue':sum(r['before_auto']=='TRUE' for r in risk.values()),'preexisting_not_auto_in_persisted_queue':sum(r['before_auto']!='TRUE' for r in risk.values()),'preserved_old_trace_identifiers':len(old),'persisted_nonempty_ids_preserved':sum(bool(v) for v in old.values()),'real_manual_decisions_in_snapshot':0,'real_manual_replay_claim':False,'synthetic_manual_regression':'Separate tests; no fabricated real manual evidence','matching_engine_runs':0,'database_connections':0,'database_writes':0,'safe_release_policy':'No automatic reconcile or promotion in RC; preserve stored authority, retain REVIEW/CONFLICT and require evidence for each future change.','semantic_resolution':'No conflict erased or unsupported AUTO acceptance. Disputed identity requires saved technical evidence or operator decision.','inputs_sha256':{str(p):sha(p) for p in [folder/'before.json',folder/'after.json',folder/'PERSISTED_AUTO_REVIEW_QUEUE.csv',old_trace,current_trace]}}
    (out/'CHURN_REVIEW_SUMMARY.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    return summary

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    root=Path(__file__).resolve().parents[1]
    result=review(root/'reports/integration/MATCHING_DRY_RUN',root/'release/DELIVERY_2026-10-19_RC1/exports/SOURCE_TRACE.csv',root/'release/DELIVERY_2026-10-19_RC1_VI/exports/SOURCE_TRACE.csv',args.output)
    print(json.dumps(result,ensure_ascii=False,indent=2))
