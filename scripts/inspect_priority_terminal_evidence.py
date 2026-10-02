"""Reconstruct unresolved priority proof from saved raw records, not Grok verdicts."""
from pathlib import Path
from collections import Counter
import sys,json,hashlib
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.verify_priority_near_complete import PRE,OUT,GROK,load,save
from universal_supplier.beka_canonical_offline import canonical_view,SavedCandidateIndex
from universal_supplier.priority_evidence_verification import visible_page,verify_candidate
from lxml import html

def own_table_rows(record):
    text=(record.get('raw_data') or {}).get('detailText') or ''
    if not text:return []
    root=html.fromstring(text);out=[]
    for tr in root.xpath('//tr'):
        cells=tr.xpath('./td|./th')
        if len(cells)!=2 or tr.xpath('.//table|.//a|.//script'):continue
        name,value=[' '.join(' '.join(n.itertext()).split()) for n in cells]
        if name and value:out.append({'name':name,'value':value,'unit':'','provenance':'verified_own_canonical_table'})
    return out

def main():
    cases=load(GROK/'priority_near_complete/evidence/PRIORITY_NEAR_COMPLETE_EVIDENCE.json')['rows']
    cases=[c for c in cases if c['proposed_result']!='EXISTING_EVIDENCE_COMPLETE'];assert len(cases)==22
    wanted={c['canonical_id'] for case in cases for a in case['source_assessments'] for c in a['candidates']}
    raw={};meta=load(ROOT/'reports/RC_LOCAL/FRESH_REFRESH_INTEGRATION_2026-10-02/EVIDENCE_VERIFIED.json')
    digest=hashlib.sha256()
    for line in Path(meta['fresh_raw_external_path']).open('rb'):
        digest.update(line);r=json.loads(line)
        if str(r['sterbrust_product_id']) in wanted:raw[str(r['sterbrust_product_id'])]=r
    assert digest.hexdigest()==meta['fresh_raw_sha256'] and set(raw)==wanted
    save('RAW_PRIORITY_CANONICAL.json',raw)
    identity=ROOT.parent/'sku-service-fresh-catalog-refresh-rc1/catalog_refresh/evidence/sterbrust/STERBRUST_REGISTRY_IDENTITY.jsonl'
    alltargets={}
    for line in identity.open(encoding='utf-8'):
        r=json.loads(line);pid=str(r['sterbrust_product_id']);alltargets[pid]={
            'sterbrust_product_id':pid,'brand':r.get('brand_raw'),'model':r.get('model_raw'),
            'name':r['name'],'active':r.get('active')}
    for pid,r in raw.items():alltargets[pid]=canonical_view(r);alltargets[pid]['product_url']=r.get('product_url')
    SavedCandidateIndex(alltargets)
    bykey={(r['source'],str(r['external_id'])):r for r in load(PRE/'MATCHING_AFTER.json')['rows']}
    mapping=load(OUT/'CONFIRMED_ONLY_CNC_MAPPINGS.json')
    fetched={r['url']:Path(r['saved_path']) for r in (json.loads(line) for line in (GROK/'priority_near_complete/evidence/FETCH_LOG.jsonl').read_text(encoding='utf-8').splitlines())}
    results=[]
    for case in cases:
        page=None;cap=fetched.get(case['priority_url'])
        if cap:
            page=visible_page(cap.read_bytes())
            assert page['h1']==case['page']['h1']
        # Saved source330S/BODOR captures are predecessor records, not fresh GET.
        if case['priority_index'] in (3,4):
            oldsha={3:'28c58f50ce7f3448fc235e1ac958bd6bde82c74f970e28be9db6e04e9c92c2b2',4:'cf0e0bd8018c991920fedf64ae005355e324767754c5816e3b41271a7f5b2167'}[case['priority_index']]
            cap=ROOT/'reports/RC_LOCAL/INTERVESP_FULL_2026-10-01/DETAIL_EVIDENCE/intervesp.ru'/f'{oldsha}.html'
            assert hashlib.sha256(cap.read_bytes()).hexdigest()==oldsha
            page=visible_page(cap.read_bytes());assert page['h1']==case['page']['h1']
        assessments=[]
        for a in case['source_assessments']:
            row=bykey[a['source'],a['external_id']];candidates=[]
            for c in a['candidates']:
                pid=c['canonical_id'];target=alltargets[pid]
                if case['supplier']=='sterbrust' and page:
                    assert page['h1']==target['name'] and page['canonical_url'].rstrip('/')==target['product_url'].rstrip('/')
                sourcepage=page if case['supplier']!='sterbrust' and page else {
                    'h1':row['name'],'canonical_url':row['source_url'],'properties':[]}
                proof=verify_candidate(row,target,sourcepage,mapping,str(cap or row['evidence_ref']))
                candidates.append({'canonical_id':pid,'canonical_name':target['name'],
                    'canonical_model':target['model'],'canonical_url':target['product_url'],'proof':proof,
                    'raw_properties':[{'name':p['property_name'],'value':p['value_flat']} for p in raw[pid].get('properties',[]) if p.get('value_flat') not in (None,'','N')],
                    'raw_table_properties':own_table_rows(raw[pid]),'previous_contradictions':c['previous_contradictions']})
            assessments.append({'source':a['source'],'external_id':a['external_id'],'source_name':row['name'],
                'source_model':row['model'],'source_properties':sourcepage['properties'] or row.get('observed_properties',[]),
                'source_quarantine':row.get('detail_review_reason') or row.get('listing_identity_reason'),
                'match_conflicts':row.get('match_conflicts'),'candidates':candidates})
        results.append({'priority_index':case['priority_index'],'priority_url':case['priority_url'],
            'supplier':case['supplier'],'capture':str(cap) if cap else None,'assessments':assessments})
    save('INDEPENDENT_22_PROOF.json',results)
    for r in results:
        print(r['priority_index'],[(a['source_model'],[(c['canonical_id'],c['proof']['confirmed'],c['proof']['blocking_reasons']) for c in a['candidates']]) for a in r['assessments']],flush=True)
    for r in results:
        if r['priority_index'] in (47,59,60):
            print('RAW',r['priority_index'],[(c['canonical_id'],[(p['name'],p['value']) for p in c['raw_properties']+c['raw_table_properties'] if any(k in p['name'].lower() for k in ('отверст','конус','мощност','стран','вес','масс'))]) for a in r['assessments'] for c in a['candidates']],flush=True)
if __name__=='__main__':main()
