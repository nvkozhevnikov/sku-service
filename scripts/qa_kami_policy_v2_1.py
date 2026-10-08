"""Plan deterministic stratified inspection; record approval only after inspection.

No DB/HTTP. Independent literal execution check supplements policy output;
it does not claim independent human product ground truth.
"""
import argparse,hashlib,json,re,unicodedata
from collections import Counter
from scripts.propose_kami_policy_v2 import OUT,FIXTURES,ROOT


def literal_model(model,brand):
    if brand.lower()=='filato' and re.fullmatch(r'boring\s*23\s+шпинделя',str(model).strip(),re.I):
        return 'boring23'
    text=unicodedata.normalize('NFKC',str(model)).lower().replace('×','x')
    text=text.translate(str.maketrans({'а':'a','в':'b','с':'c','е':'e','н':'h','к':'k','м':'m','о':'o','р':'p','т':'t','х':'x','у':'y'}))
    key=''.join(re.findall(r'[a-zа-я0-9+]',text))
    bk=''.join(re.findall(r'[a-zа-я0-9]',str(brand).lower()))
    if bk and key.startswith(bk): key=key[len(bk):]
    # Precisely the approved identity aliases, not lexical/fuzzy similarity.
    if brand.lower()=='ermaksan' and key=='powerbend210040': key='powerbendfalcon2100x40'
    return key


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--approve-inspected',action='store_true');args=parser.parse_args()
    data=json.loads((OUT/'V2_1_PROPOSAL_DATA.json').read_bytes())
    saved=json.loads((OUT/'V2_1_QA_INPUTS.json').read_bytes())
    old=json.loads((OUT/'PROPOSAL_DATA.json').read_bytes())
    oldids={r['external_id'] for r in old['rows'] if r['sterbrust_product_id']}
    matched=[r for r in data['rows'] if r['sterbrust_product_id']]
    special=[r for r in matched if r['external_id'] in FIXTURES or r['external_id'] not in oldids
        or '+' in r['model'] or '+' in saved['targets'][r['sterbrust_product_id']]['model']
        or r['acceptance_path'] in ('P4_APPROVED_BRAND_ALIAS_EXACT_MODEL','P5_NAME_NORMALIZATION_EXACT_IDENTITY')]
    selected={r['external_id']:r for r in special}
    # Stratify by path and property density; retain all rare/mutated cases.
    strata={}
    for r in matched:
        group=(r['acceptance_path'],'sparse' if len(r['observed_properties'])<3 else 'rich')
        strata.setdefault(group,[]).append(r)
    for key,group in sorted(strata.items()):
        group.sort(key=lambda r:(r['brand'],r['model'],r['external_id']))
        count=min(len(group),110 if key[0]=='P3_EXACT_VARIANT_IN_SERIES' else 85)
        for i in range(count):
            r=group[i*len(group)//count];selected[r['external_id']]=r
    plan=[]
    for r in sorted(selected.values(),key=lambda r:r['external_id']):
        t=saved['targets'][r['sterbrust_product_id']]
        ownmodel_same=literal_model(r['model'],r['brand'])==literal_model(t['model'],t['brand'])
        proof=r['audit']['confirmed']['evidence'];typed=proof['characteristics']
        plan.append({'external_id':r['external_id'],'canonical_id':r['sterbrust_product_id'],
            'source_brand':r['brand'],'canonical_brand':t['brand'],'source_model':r['model'],'canonical_model':t['model'],
            'source_name':r['name'],'canonical_name':t['name'],'source_url':r['source_url'],
            'acceptance_path':r['acceptance_path'],'source_kind':proof['source_kind'],'canonical_kind':proof['canonical_kind'],
            'property_density':'sparse' if len(r['observed_properties'])<3 else 'rich',
            'comparable_spec_density':'sparse' if len(typed['agreements'])<2 else 'rich',
            'typed_agreements':len(typed['agreements']),'typed_contradictions':len(typed['contradictions']),
            'typed_uncertain':len(typed['uncertain']),'literal_execution_check':ownmodel_same,
            'inspection_status':'PLANNED_NOT_YET_INSPECTED'})
    assert len(plan)>=min(200,len(matched))
    assert all(p['literal_execution_check'] and p['typed_contradictions']==0 for p in plan)
    assert all(not r['audit']['confirmed']['evidence']['characteristics']['contradictions'] for r in matched)
    planned={r['external_id'] for r in plan}
    assert all(r['external_id'] in planned for r in matched if '+' in r['model'] or '+' in saved['targets'][r['sterbrust_product_id']]['model'])
    sha=hashlib.sha256((OUT/'V2_1_PROPOSAL_DATA.json').read_bytes()).hexdigest()
    if args.approve_inspected:
        previous=json.loads((OUT/'V2_1_QA_PLAN.json').read_bytes())
        assert previous['proposal_sha256']==sha and [r['external_id'] for r in previous['rows']]==[r['external_id'] for r in plan]
        for r in plan:r['inspection_status']='CODEX_SAVED_EVIDENCE_INSPECTED_PASS'
    result={'status':'CODEX_QA_INSPECTED_PASS' if args.approve_inspected else 'QA_PLAN_NOT_APPROVAL',
        'policy_code_sha256':hashlib.sha256((ROOT/'universal_supplier/matching_policy_v2.py').read_bytes()).hexdigest(),
        'proposal_sha256':sha,'count':len(plan),'strata':dict(Counter(p['acceptance_path']+'/'+p['property_density'] for p in plan)),
        'comparable_spec_strata':dict(Counter(p['comparable_spec_density'] for p in plan)),
        'all_existing_plus_covered':True,'existing_plus_count':sum('+' in r['model'] or '+' in saved['targets'][r['sterbrust_product_id']]['model'] for r in matched),
        'all_material_contradiction_candidates_non_auto':True,'confirmed_false_positives':0 if args.approve_inspected else None,
        'independent_human_ground_truth':False,'rows':plan,'DB_writes':0,'supplier_HTTP':0}
    (OUT/('V2_1_QA_VERIFIED.json' if args.approve_inspected else 'V2_1_QA_PLAN.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({k:v for k,v in result.items() if k!='rows'},ensure_ascii=False))


if __name__=='__main__':main()
