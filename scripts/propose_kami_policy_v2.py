"""Frozen, HTTP-free Policy V2 proposal. This command has no DB writer.

The operator's conditional persistence gate is NOT implied by its completion.
"""
import hashlib
import argparse
import json
import subprocess
from collections import Counter
from pathlib import Path

from universal_supplier.models import ProductCard
from universal_supplier.matching_policy_v2 import PolicyV2Index, kami_source_view, RULE_VERSION, canonical_view_v2

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'reports/KAMI_MATCHING_POLICY_V2_2026-10-08'
BASE=ROOT/'reports/KAMI_INTEGRATION_2026-10-05'
PINNED={
    'RECONCILED/CARDS.jsonl':'365fce78658e3cb692be48cda3633c4b8ec7642c200ce47e7ed4f67850345138',
    'CANONICAL_CURRENT/STERBRUST_REGISTRY.jsonl':'40e4f2276c7d58754cfc755ca08453994193419cba6843db3170ec640007fd65',
}
FIXTURES={
    'site:8924632':'145849','site:9357819':'146054','site:9796695':'146103',
    'site:9807622':'145964','site:9822221':'146139','site:9854087':'146143',
    'site:9871942':'146152','site:9874887':'146151','variant:10087984':'146115',
    'variant:9833893':'146144','variant:9833894':'146145','variant:9833895':'146146',
    'variant:9833896':'146147','variant:9871470':'146101','variant:9871477':'146102',
    'variant:9661449':'146269','variant:9660997':'146240','variant:9661510':'146306',
    'variant:9660555':'145940','variant:9661086':'146220','variant:9661451':'146271',
    'variant:9660904':'146226','variant:9661483':'146287','variant:9660558':'145942',
    'variant:9801565':'145790','variant:9833219':'145777','variant:9872122':'145675',
    'variant:9872119':'145673','variant:9659587':'22872','variant:9889118':'146889',
    'site:14893':'26087','variant:9811359':'115560','variant:10131690':'31626',
    'variant:10131693':'34791','variant:9674868':'114272',
}


def records(relative):
    digest=hashlib.sha256()
    with (BASE/relative).open('rb') as stream:
        for line in stream:
            digest.update(line)
            yield json.loads(line)
    if digest.hexdigest()!=PINNED[relative]: raise RuntimeError('Frozen input SHA mismatch: '+relative)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--second-pass',action='store_true');args=parser.parse_args()
    prefix='V2_1_SECOND_' if args.second_pass else 'V2_1_'
    if args.second_pass:
        assert json.loads((OUT/'V2_1_APPLY_RECEIPT.json').read_bytes())['status']=='KAMI_V2_1_APPLIED_INDEPENDENTLY_VERIFIED'
    # Rejected V2 evidence is immutable. No CLI option can overwrite it.
    old=json.loads((OUT/'PROPOSAL_DATA.json').read_bytes())
    assert old['summary']['not_accepted_for_persistence']
    assert json.loads((OUT/'V2_1_TARGETED_AUDIT.json').read_bytes())['status']=='TARGETED_AUDIT_BEFORE_FULL_RECOMPUTE'
    git=lambda *a:subprocess.check_output(['git',*a],cwd=ROOT,text=True).strip()
    assert git('branch','--show-current')=='feature/universal-supplier-kami-rc1'
    assert git('rev-parse','HEAD')=='c6e26ec242899535dcef06f90a789a0a5c0110d0'
    for name,sha in [('develop','deefb595868d19ba471b9b943ff779194e231c26'),('production','36924836bb02965e857173773cdb70f302914643')]:
        assert git('rev-parse',name)==sha
        assert git('rev-parse','origin/'+name)==sha
    cards=list(records('RECONCILED/CARDS.jsonl'))
    assert len(cards)==5264 and len({r['card']['external_id'] for r in cards})==5264
    views={r['card']['external_id']:kami_source_view(ProductCard.from_jsonable(r['card']),r['evidence_ref']) for r in cards}
    targets={}
    for r in records('CANONICAL_CURRENT/STERBRUST_REGISTRY.jsonl'):
        pid=str(r['sterbrust_product_id'])
        if pid in targets: raise RuntimeError('Duplicate canonical ID')
        targets[pid]={'sterbrust_product_id':pid,'name':r['name'],'model':r.get('model_raw') or '',
            'brand':r.get('brand_raw') or '', 'active':r.get('active'),
            'source_category':r.get('category_path') or '', 'observed_properties':[], 'properties':{},
            'snapshot_hash':r.get('snapshot_hash'),'captured_at':r.get('captured_at')}
    assert len(targets)==59500
    index=PolicyV2Index(targets)
    needed={pid for row in views.values() for pid in index.retrieve(row)}|set(FIXTURES.values())
    # Only retrieved own canonical cards need their saved HTML tables parsed.
    for r in records('CANONICAL_CURRENT/STERBRUST_REGISTRY.jsonl'):
        pid=str(r['sterbrust_product_id'])
        if pid in needed: targets[pid]=canonical_view_v2(r)
    index=PolicyV2Index(targets)
    rows=[]
    for frozen in cards:
        card=frozen['card'];row=views[card['external_id']]
        audit=index.evaluate(row); chosen=audit['confirmed']
        rows.append({**row,'classification':'EXISTING_CONFIRMED' if chosen else 'REVIEW',
            'sterbrust_product_id':chosen['sterbrust_product_id'] if chosen else '',
            'sterbrust_name':chosen['name'] if chosen else '',
            'acceptance_path':chosen['acceptance_path'] if chosen else '',
            'price':card['price'],'price_state':card['raw_data']['price_state'],
            'price_basis':card['raw_data']['price_basis'], 'audit':audit})
    by_id={r['external_id']:r for r in rows}
    fixtures=[]
    for external_id,expected in FIXTURES.items():
        r=by_id[external_id]
        status='PASS' if r['sterbrust_product_id']==expected else (
            'FIXTURE_RESOLUTION_REVIEW' if r['audit']['blocking_reasons']==['CANONICAL_IDENTITY_NOT_UNIQUE'] else 'FAIL')
        fixtures.append({'external_id':external_id,'source_name':r['name'],'source_model':r['model'],
            'expected_id':expected,'actual_id':r['sterbrust_product_id'],'status':status,
            'blocking_reasons':r['audit']['blocking_reasons'],'audit':r['audit']})
    old_accept={r['external_id']:r['sterbrust_product_id'] for r in old['rows'] if r['sterbrust_product_id']}
    new_accept={r['external_id']:r['sterbrust_product_id'] for r in rows if r['sterbrust_product_id']}
    summary={'rule_version':RULE_VERSION,'status':'V2_1_PROPOSAL_QA_PENDING','total':len(rows),'counts':dict(Counter(r['classification'] for r in rows)),
        'paths':dict(Counter(r['acceptance_path'] for r in rows if r['sterbrust_product_id'])),
        'unique_sterbrust_ids':len({r['sterbrust_product_id'] for r in rows if r['sterbrust_product_id']}),
        'no_candidate':sum(not r['audit']['retrieval_ids'] for r in rows),
        'matched_prices':dict(Counter(r['price_state'] for r in rows if r['sterbrust_product_id'])),
        'price_states':dict(Counter(r['price_state'] for r in rows)),
        'fixture_counts':dict(Counter(f['status'] for f in fixtures)),
        'blockers':dict(Counter(b for r in rows for b in r['audit']['blocking_reasons'])),
        'input_hashes':PINNED,'DB_writes':0,'HTTP':0,'persistence_authorized_by_this_script':False,
        'delta_vs_rejected':{'removed_accepts':len(old_accept.keys()-new_accept.keys()),'new_accepts':len(new_accept.keys()-old_accept.keys()),
            'unchanged_accepts':sum(old_accept[k]==v for k,v in new_accept.items() if k in old_accept),
            'changed_targets':sum(old_accept[k]!=v for k,v in new_accept.items() if k in old_accept)}}
    assert summary['price_states']=={'numeric_public':1333,'price_on_request':3841,'missing':90}
    OUT.mkdir(exist_ok=True)
    (OUT/(prefix+'PROPOSAL_DATA.json')).write_text(json.dumps({'summary':summary,'rows':rows,'fixtures':fixtures},ensure_ascii=False,indent=2),encoding='utf8')
    # Reproducible QA inputs retain each source/target, not just PASS labels.
    (OUT/(prefix+'QA_INPUTS.json')).write_text(json.dumps({'sources':views,'targets':{p:targets[p] for p in needed}},ensure_ascii=False),encoding='utf8')
    print(json.dumps(summary,ensure_ascii=False))


if __name__=='__main__': main()
