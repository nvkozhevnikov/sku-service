"""Verify ONLY the 67 supplied priority cases; append-only proposal outputs."""
from pathlib import Path
from collections import Counter
import sys,json,hashlib,subprocess
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from universal_supplier.beka_canonical_offline import canonical_view,SavedCandidateIndex
from universal_supplier.offline_review_resolution import learn_cnc_mappings
from universal_supplier.characteristic_evidence import equipment_scope
from universal_supplier.priority_evidence_verification import visible_page,verify_candidate,apply_verified
PRE=ROOT/'reports/RC_LOCAL/OFFLINE_CANONICAL_EXPANSION_2026-10-02'
OUT=ROOT/'reports/RC_LOCAL/PRIORITY_NEAR_COMPLETE_INTEGRATION_2026-10-02'
GROK=ROOT.parent/'sku-service-priority-near-complete-rc1'
COMMIT='ef31a5bff06761997b96267cc000f16497b8fc7e'
def load(p):return json.loads(p.read_text(encoding='utf-8'))
def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()
def save(name,obj):
    OUT.mkdir(exist_ok=True);p=OUT/name;body=json.dumps(obj,ensure_ascii=False,indent=2).encode()
    if p.exists():assert p.read_bytes()==body, 'Existing evidence differs: '+name
    else:p.write_bytes(body)
def main():
    assert not (OUT/'CHECKPOINT.json').exists(),'Already sealed; resume checkpoint'
    hashes=load(PRE/'ARTIFACT_SHA256.json');inputs={}
    for name in ('CHECKPOINT.json','MATCHING_AFTER.json','GROK_PRIORITY_NEAR_COMPLETE.json','STRICT_EVALUATIONS.json'):
        inputs[str(PRE/name)]=sha(PRE/name);assert inputs[str(PRE/name)]==hashes[name]
    assert subprocess.check_output(['git','rev-parse','HEAD'],cwd=GROK,text=True).strip()==COMMIT
    for name in ('priority_near_complete/evidence/PRIORITY_NEAR_COMPLETE_EVIDENCE.json',
                 'priority_near_complete/evidence/PRIORITY_NEAR_COMPLETE_EVIDENCE.csv',
                 'priority_near_complete/REPORT.md','priority_near_complete/evidence/FETCH_LOG.jsonl'):
        blob=subprocess.check_output(['git','show',COMMIT+':'+name],cwd=GROK)
        local=(GROK/name).read_bytes()
        assert blob.replace(b'\r\n',b'\n')==local.replace(b'\r\n',b'\n'),name
        inputs[str(GROK/name)]=hashlib.sha256(local).hexdigest()
    cases=load(GROK/'priority_near_complete/evidence/PRIORITY_NEAR_COMPLETE_EVIDENCE.json')['rows']
    priorities=load(PRE/'GROK_PRIORITY_NEAR_COMPLETE.json')
    assert len(cases)==len(priorities)==67
    assert {c['priority_url'] for c in cases}=={p['url'] for p in priorities}
    fetched=[json.loads(line) for line in (GROK/'priority_near_complete/evidence/FETCH_LOG.jsonl').read_text(encoding='utf-8').splitlines()]
    assert len(fetched)==57 and all(r['http_status']==200 for r in fetched)
    captures={}
    for r in fetched:
        p=Path(r['saved_path']);rel=p.relative_to(GROK).as_posix()
        assert rel.startswith('priority_near_complete/evidence/captures/')
        assert sha(p)==r['sha256'] and p.stat().st_size==r['bytes']
        committed=subprocess.check_output(['git','show',COMMIT+':'+rel],cwd=GROK)
        assert p.read_bytes().replace(b'\r\n',b'\n')==committed.replace(b'\r\n',b'\n')
        captures[r['url']]=p;inputs[str(p)]=r['sha256']
    prior=load(PRE/'MATCHING_AFTER.json');rows=prior['rows'];bykey={(r['source'],str(r['external_id'])):r for r in rows}
    assert len(rows)==4407 and Counter(r['classification'] for r in rows)=={'EXISTING_CONFIRMED':454,'REVIEW':3694,'CONFLICT':259}
    needed={str(c['canonical_id']) for case in cases for a in case['source_assessments'] for c in a['candidates']}
    needed|={str(r['sterbrust_product_id']) for r in rows if r['classification']=='EXISTING_CONFIRMED'}
    meta=load(ROOT/'reports/RC_LOCAL/FRESH_REFRESH_INTEGRATION_2026-10-02/EVIDENCE_VERIFIED.json')
    targets={};digest=hashlib.sha256();ids=set()
    print('Verifying saved registry and selected canonical records; no HTTP/SQL',flush=True)
    for line in Path(meta['fresh_raw_external_path']).open('rb'):
        digest.update(line);r=json.loads(line);pid=str(r['sterbrust_product_id']);assert pid not in ids;ids.add(pid)
        # Global exact identity index, not global matching. Every active ID remains
        # in uniqueness checks, even when it was omitted from Grok proposals.
        v={'sterbrust_product_id':pid,'name':r['name'],'brand':r.get('brand_raw'),
           'model':r.get('model_raw'),'active':r.get('active')}
        if pid in needed:v=canonical_view(r);v['product_url']=r.get('product_url')
        targets[pid]=v
    assert len(ids)==59500 and digest.hexdigest()==meta['fresh_raw_sha256']
    inputs[meta['fresh_raw_external_path']]=digest.hexdigest();index=SavedCandidateIndex(targets)
    pairs=[]
    for r in rows:
        if r['classification']=='EXISTING_CONFIRMED':
            pairs.append({**r,'equipment_type':equipment_scope(r['name'],r.get('source_category','')),
                'source_properties':r.get('observed_properties') or [{'name':k,'value':v} for k,v in r.get('properties',{}).items()],
                'sterbrust_properties':targets[str(r['sterbrust_product_id'])]['observed_properties']})
    mappings=learn_cnc_mappings(pairs);save('CONFIRMED_ONLY_CNC_MAPPINGS.json',mappings)
    previous={(r['source'],r['external_id']):r for r in load(PRE/'STRICT_EVALUATIONS.json')}
    audits=[];promotions=[]
    for case in cases:
        if case['proposed_result']!='EXISTING_EVIDENCE_COMPLETE':continue
        assert len(case['source_assessments'])==1
        a=case['source_assessments'][0];key=a['source'],a['external_id'];row=bykey[key]
        cap=captures[case['priority_url']];page=visible_page(cap.read_bytes())
        assert page['h1']==case['page']['h1']
        assert [(p['name'],p['value']) for p in page['properties']]==[(p['name'],p['value']) for p in case['page']['characteristics']]
        results=[]
        for c in a['candidates']:
            pid=c['canonical_id'];target=targets[pid]
            old=next((x for x in previous[key]['evaluations'] if x['canonical_id']==pid),{})
            contradictions=old.get('evidence',{}).get('typed_characteristics',{}).get('contradictions',[])
            proof=verify_candidate(row,target,page,mappings,str(cap),contradictions)
            results.append({'canonical_id':pid,'canonical_name':target['name'],'proof':proof})
        passing=[x for x in results if x['proof']['confirmed']]
        accepted=len(passing)==1
        audits.append({'priority_index':case['priority_index'],'source':key[0],'external_id':key[1],
            'source_model':row['model'],'url':case['priority_url'],'accepted':accepted,'candidates':results})
        if accepted:promotions.append({'source':key[0],'external_id':key[1],**passing[0],'evidence_ref':str(cap)})
    save('VERIFY_45.json',audits);save('PROMOTIONS.json',promotions)
    save('INPUT_SHA256.json',inputs)
    after=apply_verified(rows,promotions);assert apply_verified(after,promotions)==after
    changed={(p['source'],p['external_id']) for p in promotions}
    for r,old in zip(after,rows):
        if (r['source'],str(r['external_id'])) not in changed:assert r==old
        for k in ('name','model','execution','external_id','source_url','properties','observed_properties'):assert r.get(k)==old.get(k)
    save('MATCHING_AFTER.json',{**prior,'rows':after,'predecessor':str(PRE/'CHECKPOINT.json')})
    counts=Counter(r['classification'] for r in after)
    save('INTERIM_VERIFIED.json',{'accepted_45':len(promotions),'remaining_45':45-len(promotions),
        'overall':dict(counts),'ready':0,'replay_exact_noop':True,'http':0,'sql':0,'grok_captures_verified':57,
        'registry_unique_ids':59500,'unchanged_source_identities':4407,'previous_existing_preserved':454,
        'previous_conflicts_preserved':259})
    print(json.dumps(load(OUT/'INTERIM_VERIFIED.json'),indent=2),flush=True)
    for a in audits:
        if not a['accepted']:print(a['source_model'],[(c['canonical_id'],c['proof']['blocking_reasons']) for c in a['candidates']],flush=True)
if __name__=='__main__':main()
