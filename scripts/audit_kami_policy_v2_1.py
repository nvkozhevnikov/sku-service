"""Targeted saved-evidence audit before any whole V2.1 proposal recompute."""
import json
from collections import Counter
from universal_supplier.models import ProductCard
from universal_supplier.matching_policy_v2 import kami_source_view,canonical_view_v2,assess_pair,identity_model,plus_evidence
from scripts.propose_kami_policy_v2 import ROOT,OUT,records


def main():
    old=json.loads((OUT/'PROPOSAL_DATA.json').read_bytes())
    assert old['summary']['not_accepted_for_persistence']
    views={r['card']['external_id']:kami_source_view(ProductCard.from_jsonable(r['card']),r['evidence_ref']) for r in records('RECONCILED/CARDS.jsonl')}
    accepted=[r for r in old['rows'] if r['sterbrust_product_id']]
    needed={r['sterbrust_product_id'] for r in accepted};targets={};canonical_plus=[]
    for record in records('CANONICAL_CURRENT/STERBRUST_REGISTRY.jsonl'):
        pid=str(record['sterbrust_product_id'])
        if pid in needed: targets[pid]=canonical_view_v2(record)
        if '+' in (record.get('model_raw') or ''):
            canonical_plus.append({'canonical_id':pid,'model':record.get('model_raw'),'name':record['name'],
                'plus_tokens':plus_evidence(record['model_raw']),'active':record.get('active')})
    plus=[];spec=[]
    prior={r['external_id']:r for r in accepted}
    for external_id,s in views.items():
        previous=prior.get(external_id);t=targets[previous['sterbrust_product_id']] if previous else None
        if '+' in s['model'] or (t and '+' in t.get('model','')):
            tokens=plus_evidence(s['model'])+(plus_evidence(t['model']) if t else [])
            verdict=('SAFE_SAME_EXECUTION' if t and identity_model(s)==identity_model(t) else
                     'DESCRIPTIVE_PLUS_ONLY' if tokens and all(x['kind']=='PUNCTUATION_PLUS' for x in tokens) else
                     'SIGNIFICANT_PLUS_MISMATCH' if t else 'REVIEW')
            plus.append({'external_id':external_id,'source_name':s['name'],'source_model':s['model'],
                'canonical_id':t['sterbrust_product_id'] if t else '', 'canonical_model':t['model'] if t else '',
                'previous_proposed_existing':bool(previous),'verdict':verdict,'source_url':s['source_url'],
                'plus_tokens':tokens})
    for oldrow in accepted:
        s=views[oldrow['external_id']];t=targets[oldrow['sterbrust_product_id']]
        proof=assess_pair(s,t);typed=proof['evidence']['characteristics']
        spec.append({'external_id':s['external_id'],'source_name':s['name'],'source_model':s['model'],
            'canonical_id':t['sterbrust_product_id'],'canonical_name':t['name'],'canonical_model':t['model'],
            'comparable_fields':len(typed['agreements'])+len(typed['contradictions']),
            'material_contradictions':len(typed['contradictions']),
            'verdict':'MATERIAL_CONTRADICTION_BLOCKS' if typed['contradictions'] else 'NO_PROVEN_MATERIAL_CONTRADICTION',
            'typed_evidence':typed,'blocking_reasons':proof['blocking_reasons'],'source_url':s['source_url']})
    audit={'status':'TARGETED_AUDIT_BEFORE_FULL_RECOMPUTE','source_rows':len(views),
        'old_rejected_existing_audited':len(spec),'source_plus_rows':len(plus),
        'canonical_plus_rows':len(canonical_plus),'plus_counts':dict(Counter(r['verdict'] for r in plus)),
        'old_existing_with_comparable_numeric_or_typed':sum(r['comparable_fields']>0 for r in spec),
        'old_existing_with_proven_spec_contradiction':sum(r['material_contradictions']>0 for r in spec),
        'plus':plus,'canonical_plus_inventory':canonical_plus,'spec':spec,'supplier_HTTP':0,'DB_writes':0}
    (OUT/'V2_1_TARGETED_AUDIT.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({k:v for k,v in audit.items() if k not in ('plus','canonical_plus_inventory','spec')},ensure_ascii=False))


if __name__=='__main__':main()
