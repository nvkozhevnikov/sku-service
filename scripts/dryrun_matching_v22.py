"""Offline full six-namespace BEFORE/AFTER; no DB or HTTP imports."""
import hashlib,json
from pathlib import Path
from collections import Counter,defaultdict
from universal_supplier.matching_policy_v22 import PolicyV22Index,kind_proof
from universal_supplier.matching_policy_v2 import identity_model,NON_MACHINE

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'reports/MATCHING_V2_2_AI_REVIEW'
def save(n,v):(OUT/n).write_text(json.dumps(v,ensure_ascii=False,indent=2,default=str)+'\n',encoding='utf-8')

def route(r,audit):
    reasons=set(audit['blocking_reasons'])
    if 'CANONICAL_IDENTITY_NOT_UNIQUE' in reasons:return 'MULTIPLE_STERBRUST_CANDIDATES'
    if kind_proof(r)['kind'] in NON_MACHINE or kind_proof(r)['kind']=='accessory':return 'NEEDS_SOURCE_EVIDENCE'
    if any('CONTRADICTION' in x or 'GUARD' in x or 'MISMATCH' in x for x in reasons):return 'TRUE_AMBIGUOUS'
    if not audit['retrieval_ids']:
        # No exact match is never positive canonical-absence proof.
        return 'NEEDS_SOURCE_EVIDENCE' if not r.get('brand') or not r.get('model') else 'POSSIBLE_FUTURE_PATTERN'
    return 'MANUAL_HIGH_CONFIDENCE' if reasons<= {'SOURCE_VISIBLE_EXECUTION_NOT_PROVEN','CANONICAL_VISIBLE_EXECUTION_NOT_PROVEN'} else 'TRUE_AMBIGUOUS'

def main():
    before=json.loads((OUT/'V21_BEFORE.json').read_bytes());targets=json.loads((OUT/'CANONICAL_VIEWS.json').read_bytes())
    index=PolicyV22Index(targets);rows=[];new=[];removed=[];changed=[];summary=defaultdict(Counter)
    rule_counts=Counter();routes=Counter();raw_removed=[]
    for r in before['rows']:
        old=r['audit_v21'];newaudit=index.evaluate(r)
        old_choice=old['confirmed'];choice=newaudit['confirmed']
        # Current accepted physical links have independent persisted authority.
        # An offline alternative is never allowed to silently rewrite them.
        preserved=r['persisted_auto_accepted'] and r['persisted_classification'] in ('EXACT_MATCH','HIGH_CONFIDENCE_MATCH') and bool(r.get('linked_sterbrust_product_id'))
        prior='EXISTING_CONFIRMED' if preserved else r['classification_v21']
        after='EXISTING_CONFIRMED' if preserved or choice else 'REVIEW'
        if prior=='CONFLICT':after='CONFLICT'
        target=str(r['linked_sterbrust_product_id']) if preserved else choice['sterbrust_product_id'] if choice else ''
        old_target=str(r['linked_sterbrust_product_id']) if preserved else old_choice['sterbrust_product_id'] if old_choice else ''
        current={**r,'classification_before':prior,'classification_after':after,'target_after':target,
                 'audit_v22':newaudit,'persisted_existing_preserved':preserved,
                 'terminal_route':route(r,newaudit) if after!='EXISTING_CONFIRMED' else 'EXISTING_CONFIRMED'}
        rows.append(current);s=summary[r['source']];s['Total']+=1;s['Existing BEFORE']+=prior=='EXISTING_CONFIRMED'
        s['Existing AFTER']+=after=='EXISTING_CONFIRMED';s['Review AFTER']+=after=='REVIEW';s['Conflict AFTER']+=after=='CONFLICT'
        s['New_candidate BEFORE']+=0;s['New_candidate AFTER']+=0
        if old_choice and not choice:raw_removed.append((r['source'],r['external_id'],newaudit['blocking_reasons']))
        if prior!='EXISTING_CONFIRMED' and after=='EXISTING_CONFIRMED':
            assert choice and targets[target]['active']=='Y'
            assert len([x for x in newaudit['candidates'] if x['confirmed']])==1
            assert choice.get('rule_id') and choice['new_rule_applied']
            new.append(current);rule_counts[choice['rule_id']]+=1;s['+Matches']+=1
        if prior=='EXISTING_CONFIRMED' and after!='EXISTING_CONFIRMED':removed.append(current)
        if old_target and target and old_target!=target:changed.append(current)
        if after!='EXISTING_CONFIRMED':routes[current['terminal_route']]+=1
    assert len(rows)==9671 and len({(r['source'],r['external_id']) for r in rows})==9671
    assert not changed,'Changed accepted canonical IDs require explicit investigation'
    save('V22_AFTER.json',{'rows':rows,'summary':{s:dict(v) for s,v in summary.items()},'new_auto_accepts':len(new),
        'removed_auto_accepts':len(removed),'changed_candidate_count':len(changed),'raw_policy_removed':raw_removed,
        'rules':dict(rule_counts),'terminal_routes':dict(routes),'DB_writes':0,'HTTP':0,
        'before_definition':'Current approved persisted links preserved + common V2.1 dry-run; not current operational adoption',
        'persisted_operational_existing':sum(r['persisted_existing_preserved'] for r in rows),
        'canonical_absence_proven_new':0})
    # Bounded candidates for genuine AI evidence inspection, not an AI writer.
    audit_pairs=[]
    for r in new:
        chosen=r['audit_v22']['confirmed'];t=targets[chosen['sterbrust_product_id']]
        audit_pairs.append({'supplier':r['source'],'external_id':r['external_id'],'source_product_id':r['source_product_id'],
            'source_url':r['source_url'],'source_name':r['name'],'source_brand':r['brand'],'source_model':r['model'],
            'source_category':r['source_category'],'canonical_id':t['sterbrust_product_id'],'canonical_name':t['name'],
            'canonical_brand':t['brand'],'canonical_model':t['model'],'canonical_category':t['source_category'],
            'rule_id':chosen['rule_id'],'kind_evidence':chosen['evidence']['kind_evidence'],
            'agreements':chosen['evidence']['characteristics']['agreements'],
            'uncertain':chosen['evidence']['characteristics']['uncertain'],
            'negative_guard_result':chosen['evidence']['negative_guard_result'],
            'raw_candidate_score':r['current_candidate_score'],'evidence_ref':r['evidence_ref']})
    save('NEW_AUTO_ACCEPT_QA_INPUTS.json',audit_pairs)
    save('CHECKPOINT.json',{'status':'V22_DRY_RUN_REQUIRES_AI_STRATIFIED_QA','new_auto_accepts':len(new),
        'removed':len(removed),'changed':len(changed),'resume_from':'NEW_AUTO_ACCEPT_QA_INPUTS.json; audit pairs and counterexamples then final CSV/tests',
        'DB_writes':0,'HTTP':0,'active_55452_access':0})
    print(json.dumps({'summary':{s:dict(v) for s,v in summary.items()},'new':len(new),'removed':len(removed),'changed':len(changed),
        'raw_removed':raw_removed,'rules':dict(rule_counts),'routes':dict(routes)},ensure_ascii=False))

if __name__=='__main__':main()
