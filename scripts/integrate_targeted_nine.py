"""Exactly nine saved evidence cases, fail-closed proposals; no live operations."""
from pathlib import Path
from collections import Counter
from copy import deepcopy
import sys,json,hashlib,subprocess
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from universal_supplier.beka_canonical_offline import canonical_view,SavedCandidateIndex
from universal_supplier.offline_review_resolution import learn_cnc_mappings
from universal_supplier.characteristic_evidence import equipment_scope
from universal_supplier.priority_evidence_verification import verify_candidate,visible_page,apply_verified,merge_observations
from universal_supplier.targeted_evidence_verification import smec_table,labelled_power_pair,canonical_visible_table
PRE=ROOT/'reports/RC_LOCAL/PRIORITY_NEAR_COMPLETE_INTEGRATION_2026-10-02'
OUT=ROOT/'reports/RC_LOCAL/TARGETED_NINE_INTEGRATION_2026-10-02/ACCEPTED'
GROK=ROOT.parent/'sku-service-priority-near-complete-rc1'
REL='priority_near_complete/targeted_evidence_2026-10-02'
COMMIT='0547617dfcbd206281e12af92d59ba89a469284c'
def load(p):return json.loads(p.read_text(encoding='utf-8'))
def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()
def save(n,v):
    OUT.mkdir(parents=True,exist_ok=True);p=OUT/n;b=json.dumps(v,ensure_ascii=False,indent=2).encode()
    if p.exists():assert p.read_bytes()==b,n
    else:p.write_bytes(b)
def main():
    assert not (OUT/'CHECKPOINT.json').exists(),'Already complete'
    inputs={}
    for name in ('CHECKPOINT.json','MATCHING_ACCEPTED.json','RAW_PRIORITY_CANONICAL.json','INDEPENDENT_22_PROOF.json'):
        inputs[str(PRE/name)]=sha(PRE/name);assert inputs[str(PRE/name)]==load(PRE/'ARTIFACT_SHA256.json')[name]
    assert subprocess.check_output(['git','rev-parse','HEAD'],cwd=GROK,text=True).strip()==COMMIT
    for name in ('TARGETED_EVIDENCE.json','TARGETED_EVIDENCE.csv','FETCH_LOG.jsonl','REPORT.md'):
        local=(GROK/REL/name).read_bytes();committed=subprocess.check_output(['git','show',COMMIT+':'+REL+'/'+name],cwd=GROK)
        assert local.replace(b'\r\n',b'\n')==committed.replace(b'\r\n',b'\n')
        inputs[str(GROK/REL/name)]=hashlib.sha256(local).hexdigest()
    data=load(GROK/REL/'TARGETED_EVIDENCE.json');cases=data['cases']
    assert len(cases)==9 and {c['priority_index'] for c in cases}=={0,1,2,3,4,60,56,57,58}
    fetch=[json.loads(l) for l in (GROK/REL/'FETCH_LOG.jsonl').read_text(encoding='utf-8').splitlines()]
    caps={}
    for f in fetch:
        if not f.get('path'):continue
        p=Path(f['path']);rel=p.relative_to(GROK).as_posix()
        assert rel.startswith(REL+'/captures/') and sha(p)==f['sha256'] and p.stat().st_size==f['bytes']
        assert p.read_bytes()==subprocess.check_output(['git','show',COMMIT+':'+rel],cwd=GROK)
        inputs[str(p)]=f['sha256'];caps[f['key']]=p
    prior=load(PRE/'MATCHING_ACCEPTED.json');rows=prior['rows'];bykey={(r['source'],str(r['external_id'])):r for r in rows}
    assert len(rows)==4407 and Counter(r['classification'] for r in rows)=={'EXISTING_CONFIRMED':505,'REVIEW':3643,'CONFLICT':259}
    wanted={str(r['sterbrust_product_id']) for r in rows if r['classification']=='EXISTING_CONFIRMED'}|{p for c in cases for p in c['evidence_target_ids']}
    targets={};raw={};digest=hashlib.sha256();ids=set()
    meta=load(ROOT/'reports/RC_LOCAL/FRESH_REFRESH_INTEGRATION_2026-10-02/EVIDENCE_VERIFIED.json')
    print('Reading pinned canonical identity and confirmed training fields; exactly nine evaluations',flush=True)
    for line in Path(meta['fresh_raw_external_path']).open('rb'):
        digest.update(line);r=json.loads(line);pid=str(r['sterbrust_product_id']);assert pid not in ids;ids.add(pid)
        v={'sterbrust_product_id':pid,'name':r['name'],'brand':r.get('brand_raw'),'model':r.get('model_raw'),'active':r.get('active')}
        if pid in wanted:v=canonical_view(r);v['product_url']=r.get('product_url');raw[pid]=r
        targets[pid]=v
    assert len(ids)==59500 and digest.hexdigest()==meta['fresh_raw_sha256'];inputs[meta['fresh_raw_external_path']]=digest.hexdigest()
    SavedCandidateIndex(targets)
    pairs=[{**r,'equipment_type':equipment_scope(r['name'],r.get('source_category','')),
            'source_properties':r.get('observed_properties') or [{'name':k,'value':v} for k,v in r.get('properties',{}).items()],
            'sterbrust_properties':targets[str(r['sterbrust_product_id'])]['observed_properties']}
           for r in rows if r['classification']=='EXISTING_CONFIRMED']
    # Read new observation labels only from previously VERIFIED captures attached
    # to accepted Existing. No source edits, no old proof replay, no REVIEW training.
    known_caps=load(PRE/'INPUT_SHA256.json')
    for p in pairs:
        cap=p.get('priority_evidence_ref')
        if not cap:continue
        assert cap in known_caps and sha(Path(cap))==known_caps[cap]
        observed=visible_page(Path(cap).read_bytes())
        assert observed['h1']==p['name']
        p['source_properties']=merge_observations(observed['properties'],p['source_properties'])
    mappings=learn_cnc_mappings(pairs);save('CONFIRMED_ONLY_TYPED_MAPPINGS.json',mappings)
    smec={}
    for model,heading,key in [('SL2000B','SL 2000 B (8″)','smec-sl2000'),('SL2000BM','SL 2000M B (8″)','smec-sl2000'),('SL3000BL','SL 3000L B (12″)','smec-sl3000')]:
        smec[model]=smec_table(caps[key].read_bytes(),heading)
    save('MANUFACTURER_EXACT_TABLES.json',smec)
    previous={c['priority_index']:c for c in load(PRE/'INDEPENDENT_22_PROOF.json')}
    oldfetch=[json.loads(l) for l in (GROK/'priority_near_complete/evidence/FETCH_LOG.jsonl').read_text(encoding='utf-8').splitlines()]
    sources={f['url']:Path(f['saved_path']) for f in oldfetch}
    promotions=[];audit=[];dup=[];errors=[]
    for case in cases:
        i=case['priority_index'];old=previous[i];assert case['source_records']==[{'source':a['source'],'external_id':a['external_id']} for a in old['assessments']]
        row=bykey[case['source_records'][0]['source'],case['source_records'][0]['external_id']]
        cap=sources.get(row['source_url'])
        if i in (3,4):cap=Path(old['capture'])
        page=visible_page(cap.read_bytes()) if cap else {'h1':row['name'],'canonical_url':row['source_url'],'properties':[]}
        if cap:inputs[str(cap)]=sha(cap)
        proofs=[];live={}
        for pid in case['evidence_target_ids']:
            target=deepcopy(targets[pid])
            if pid in caps:
                live[pid]=canonical_visible_table(caps[pid].read_bytes());assert live[pid]['h1']==target['name']
                target['observed_properties']=merge_observations(live[pid]['properties'],target['observed_properties'])
            proof=verify_candidate(row,target,page,mappings,str(cap or row['evidence_ref']))
            proofs.append({'canonical_id':pid,'canonical_name':target['name'],'canonical_model':target['model'],
                'canonical_url':target['product_url'],'proof':proof,'live_own_table':live.get(pid)})
        if i==58:
            assert len(proofs)==1;proof=proofs[0]['proof']
            table={r['label']:r['values'][0] for r in smec['SL3000BL']['rows']}
            assert table['Spindle bore diameter mm']=='105' and table['Draw tube ID mm']=='91'
            assert labelled_power_pair(table['Spindle motor (Cont./Max) kW'],'Cont./Max')['continuous_kw']=='18.5'
            if proof['confirmed']:
                promotions.append({'source':row['source'],'external_id':str(row['external_id']),
                    'canonical_id':'147497','canonical_name':targets['147497']['name'],'proof':proof,'evidence_ref':str(cap)})
                status='RESOLVED_EXISTING';route=status
                reason='Strict unique full model; independent processing length1028mm + X-axis travel245mm agree. Official MAIN bore105mm is separate manufacturer evidence, not copied into both sides; Cont./Max18.5/26 and S6 remain distinct, not identity anchors.'
            else:status='STILL_UNRESOLVED';route='CANONICAL_SIDE_EVIDENCE_REQUIRED';reason='Strict proof blocked: '+str(proof['blocking_reasons'])
        elif i in (1,3):
            status='CANONICAL_DUPLICATE_CONFIRMED';route='TRUE_HUMAN_REQUIRED'
            reason='146998/147023 same full330C and observed cut/blade/speed/motor/envelope/weight; no surviving ID chosen. Canonical owner must determine survivor and references under separate correction authority.'
            dup.append({'priority_index':i,'ids':['146998','147023'],'source_records':case['source_records'],
                'proof':live,'source_visible_model':'330C','url_alias':'330s' if i==3 else None,
                'canonical_choice':None,'required_action':'Canonical owner chooses survivor, audits references and redirects/aliases, and separately approves correction. No automatic delete/merge/update.'})
        elif i in (0,2,56,57):
            status='FACTUAL_CONTRADICTION';route='FACTUAL_CONFLICT'
            reason={0:'Both targets retained: blade8400 vs8200, cut800 vs official820 and weight3250 vs3424;97218 speed120 vs own20–100.',
                    2:'Both targets retained. PDF / visible manufacturer block / JSON-LD disagree on blade, motor, speed, weight/envelope. No convenient-layer selection.',
                    56:'Official bore76, speed4500/noseA2-6/weight3900 vs saved speed4000/noseA2-5/weight3800. Cont./Max and S6 not conflated.',
                    57:'Official mill B bore76, speed4500/noseA2-6/weight4000/X215 vs saved4000/A2-5/3800/X210. Execution B≠BM.'}[i]
            errors.append({'priority_index':i,'model':case['model'],'canonical_ids':case['evidence_target_ids'],
                'reason':reason,'canonical_proofs':proofs,'authority_layers':case['fields'],
                'source_classification_change':False,'new_absence_proof':False,'required_action':'Resolve exact execution/version and inconsistent labelled values with canonical/manufacturer evidence; no ID assignment or correction applied.'})
        elif i==4:
            status='STILL_UNRESOLVED';route='CANONICAL_SIDE_EVIDENCE_REQUIRED';reason='Source visibly used; canonical114334 condition unspecified. Price/stock/warranty are not condition proof.'
        elif i==60:
            status='STILL_UNRESOLVED';route='CANONICAL_SIDE_EVIDENCE_REQUIRED';reason='Net180kg differs legitimately from gross210kg; Morse4 notations equivalent. Country property roles remain undefined; no invented semantics or identity promotion.'
        else:raise AssertionError(i)
        audit.append({'priority_index':i,'model':case['model'],'source_records':case['source_records'],'status':status,'route':route,'reason':reason,
            'canonical_targets':proofs,'evidence_target_ids':case['evidence_target_ids'],'all_targets_retained':True})
        print(i,status,[(p['canonical_id'],p['proof']['blocking_reasons']) for p in proofs],flush=True)
    save('NINE_VERIFIED_PROOFS.json',audit);save('PROMOTIONS.json',promotions)
    save('CANONICAL_DUPLICATE_CORRECTION_PACK.json',{'unique_duplicate_groups':1,'affected_source_cases':2,'items':dup,'sql':0,'production':0})
    save('CANONICAL_DATA_ERROR_PACK.json',{'items':errors,'correction_applied':False,'source_conflicts_not_assigned':True})
    save('INPUT_SHA256.json',inputs)
    after=apply_verified(rows,promotions);assert apply_verified(after,promotions)==after
    for old,new in zip(rows,after):
        if old['classification'] in ('EXISTING_CONFIRMED','CONFLICT'):assert old==new
        for k in ('source','external_id','model','execution','name','properties','observed_properties','source_url'):assert old.get(k)==new.get(k)
    count=Counter(r['classification'] for r in after)
    summary={'new_existing':len(promotions),'overall':{'Existing':count['EXISTING_CONFIRMED'],'READY_TO_CREATE_IDENTITY':0,'Review':count['REVIEW'],'Conflict':count['CONFLICT']},
        'cases':9,'routes':dict(Counter(a['route'] for a in audit)),'true_human_required_cases':sum(a['route']=='TRUE_HUMAN_REQUIRED' for a in audit),
        'true_human_required_unique_canonical_decisions':1,'sql':0,'http':0,'global_matcher':False,'source_ingestion':False,'replay_exact_noop':True,
        'grok_saved_http_statuses':dict(Counter(str(f['status']) for f in fetch)),'canonical_registry_unique_ids':59500,
        'final_xml_xlsx_published':False}
    save('MATCHING_ACCEPTED.json',{**prior,'rows':after,'summary':summary,'predecessor':str(PRE/'CHECKPOINT.json')})
    save('VERIFIED_SUMMARY.json',summary)
    print(json.dumps(summary,indent=2),flush=True)
if __name__=='__main__':main()
