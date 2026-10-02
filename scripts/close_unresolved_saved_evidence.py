"""Cluster first, expand saved canonical proof once, never global match/crawl."""
from pathlib import Path
from collections import Counter,defaultdict
from copy import deepcopy
import sys,json,hashlib,re
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from universal_supplier.beka_canonical_offline import canonical_view,model_key,family_key,SavedCandidateIndex
from universal_supplier.offline_canonical_closure import own_model_columns,strict_saved_resolution,evidence_route
from universal_supplier.characteristic_evidence import build_evidence,equipment_scope
from universal_supplier.offline_review_resolution import learn_cnc_mappings
from universal_supplier.intervesp_final_proposals import readiness_proposals
from scripts.build_characteristic_evidence import section_evidence
from scripts.match_fresh_refresh_consolidated import summary,NAMESPACES
from sterbrust_matching.normalization import normalize_brand,normalize_model,model_tokens
PRE=ROOT/'reports/RC_LOCAL/OPTIMUM_COMPLETE_CLOSURE_2026-10-02'
OUT=ROOT/'reports/RC_LOCAL/OFFLINE_CANONICAL_EXPANSION_2026-10-02'
def load(p):return json.loads(p.read_text(encoding='utf-8'))
def save(name,v):
    body=json.dumps(v,ensure_ascii=False,indent=2,default=str).encode();p=OUT/name
    if p.exists():assert p.read_bytes()==body
    else:p.write_bytes(body)
def main():
    assert not (OUT/'MATCHING_AFTER.json').exists(),'Completed; use checkpoint, never repeat'
    checks=load(PRE/'ARTIFACT_SHA256.json')
    assert all(hashlib.sha256((PRE/p).read_bytes()).hexdigest()==sha for p,sha in checks.items())
    accepted=load(PRE/'MATCHING_ACCEPTED.json');rows=deepcopy(accepted['rows']);closure=load(PRE/'MATCHING_CLOSURE.json')
    oldroute={(r['source'],r['external_id']):r.get('next_evidence_route') for r in closure['rows']}
    original={(r['source'],str(r['external_id'])):r for r in accepted['rows']}
    unresolved=[r for r in rows if r['classification']=='REVIEW'];assert len(unresolved)==3694
    OUT.mkdir(exist_ok=True)
    clusters=Counter((r['source'],oldroute[(r['source'],str(r['external_id']))],
        equipment_scope(r['name'],r.get('source_category','')),family_key(r.get('model',''))) for r in unresolved)
    save('CLUSTERS_BEFORE.json',[{'source':s,'route':route,'equipment_scope':scope,'model_family':family,'rows':n}
        for (s,route,scope,family),n in sorted(clusters.items(),key=lambda p:(-p[1],str(p[0])))])
    save('RESUME_STATE.json',{'stage':'CLUSTERS_SAVED_BEFORE_CANONICAL_EXPANSION','http':0,'sql':0,'matcher':False})
    proof=load(ROOT/'reports/RC_LOCAL/FRESH_REFRESH_INTEGRATION_2026-10-02/EVIDENCE_VERIFIED.json')
    needed={(normalize_brand(r.get('brand')),model_key(r)) for r in rows if r.get('brand') and r.get('model')}
    pids={str(r['sterbrust_product_id']) for r in rows if r['classification']=='EXISTING_CONFIRMED'}
    pids|={str(r.get('proposed_sterbrust_id')) for r in unresolved if r.get('proposed_sterbrust_id')}
    targets={};ids=set();digest=hashlib.sha256();absence={'complete':True,'sha256':proof['fresh_raw_sha256'],'models':defaultdict(list),'families':defaultdict(list)}
    print('Reading saved canonical registry once; no HTTP',flush=True)
    for line in Path(proof['fresh_raw_external_path']).open('rb'):
        digest.update(line);r=json.loads(line);pid=str(r['sterbrust_product_id']);assert pid not in ids;ids.add(pid)
        brand=normalize_brand(r.get('brand_raw'));keys=({normalize_model(r.get('model_raw'),brand)}|set(model_tokens(r['name'])))-{''}
        viewkeys={(brand,model_key({'model':m,'brand':r.get('brand_raw'),'name':r['name']})) for m in [r.get('model_raw')]+list(model_tokens(r['name']))}
        for key in keys:
            absence['models'][key].append(pid);family=re.match(r'[a-zа-я]+\d+',key)
            absence['families'][family.group(0) if family else key].append(pid)
        if pid in pids or needed & viewkeys:
            view=canonical_view(r);extra=own_model_columns(r);view['observed_properties']+=extra
            view.update(product_url=r.get('product_url'),new_exact_column_properties=extra,
                category_ids=r.get('category_ids') or [r.get('category_id')])
            targets[pid]=view
    assert len(ids)==59500 and digest.hexdigest()==proof['fresh_raw_sha256']
    save('CANONICAL_EXPANSION_PROOF.json',{'snapshot_sha256':digest.hexdigest(),'unique_ids':len(ids),
        'target_records':len(targets),'multi_column_properties':sum(len(t['new_exact_column_properties']) for t in targets.values()),
        'own_description_properties':sum(sum(p.get('provenance')=='saved_canonical_detail_table' for p in t['observed_properties']) for t in targets.values())})
    index=SavedCandidateIndex(targets)
    sections={str(r['id']):r for r in load(ROOT/'reports/rest/STERBRUST_SECTIONS_RAW.json')}
    pairs=[]
    for row in rows:
        if row['classification']!='EXISTING_CONFIRMED':continue
        t=targets[row['sterbrust_product_id']]
        pairs.append({**row,'equipment_type':equipment_scope(row['name'],row.get('source_category','')),
            'source_properties':row.get('observed_properties') or [{'name':k,'value':v} for k,v in row.get('properties',{}).items()],
            'sterbrust_properties':t['observed_properties'],'sections':section_evidence(t['category_ids'],sections)})
    assert len(pairs)==454
    learned=build_evidence(pairs);cnc=learn_cnc_mappings(pairs)
    save('CONFIRMED_ONLY_EVIDENCE.json',learned)
    audits=[];promotions=[]
    for row in unresolved:
        identity=row['source'],str(row['external_id']);retrieval=index.retrieve(row)
        exact=[targets[p] for p in retrieval['exact_full_model_ids']]
        # Beka audits are not repeated. Only new multi-model column proof can
        # justify a fresh strict evaluation; all other previous results reused.
        beka=row['source'] in ('beka_mak','beka_mak_tr')
        if beka and not any(t['new_exact_column_properties'] for t in exact):
            previous=row.get('historical_hold_resolution') or {}
            audit={'confirmed':None,'evaluations':[], 'blocking_reasons':previous.get('blocking_reasons') or ['BEKA_PREVIOUS_AUDIT_REUSED_NO_NEW_EVIDENCE']}
        else:audit=strict_saved_resolution(row,exact,cnc)
        audit.update(source=identity[0],external_id=identity[1],previous_route=oldroute[identity],retrieval=retrieval)
        audits.append(audit)
        if audit['confirmed']:
            pid=audit['confirmed']['canonical_id'];t=targets[pid]
            row.update(classification='EXISTING_CONFIRMED',sterbrust_product_id=pid,sterbrust_name=t['name'],
                proposed_sterbrust_id='',full_model_confirmed=True,offline_expanded_identity_proof=audit)
            promotions.append({'source':identity[0],'external_id':identity[1],'canonical_id':pid,'previous_route':oldroute[identity]})
    enriched,groups,absenceaudit=readiness_proposals(rows,learned,absence,
        absence_review_ref='saved://fresh59500/'+proof['fresh_raw_sha256'],allowed_sources=NAMESPACES)
    byid={(r['source'],str(r['external_id'])):r for r in enriched}
    plans=[];human=[];routes=Counter();closed=Counter();decisions=[]
    for audit in audits:
        identity=audit['source'],audit['external_id'];row=byid[identity]
        verdict=evidence_route(row,audit,row.get('readiness') or {});routes[verdict]+=1
        if verdict in ('AUTO_RESOLVED_EXISTING','NEW_READINESS_CANDIDATE'):closed[audit['previous_route']]+=1
        decision={**audit,'verdict':verdict,'source_url':row['source_url'],'readiness':row.get('readiness')}
        decisions.append(decision)
        if verdict.startswith('TARGETED_'):
            target_ids=audit['retrieval']['exact_full_model_ids'] or audit['retrieval']['family_retrieval_only_ids']
            if not target_ids and row.get('proposed_sterbrust_id') in targets:target_ids=[row['proposed_sterbrust_id']]
            urls=[targets[p].get('product_url') for p in target_ids if targets[p].get('product_url')]
            if verdict=='TARGETED_SUPPLIER_DETAIL_REQUIRED':urls=[row['source_url']]
            # Canonical exact targets absent: never manufacture a URL or treat
            # generic catalogue search as proof that a page exists.
            plans.append({'source':identity[0],'external_id':identity[1],'verdict':verdict,
                'exact_urls':sorted(set(urls)),'source_url':row['source_url'],'blocking_reasons':audit['blocking_reasons'],
                'required_evidence':['visible full execution','own product description/typed property table','accessory/condition/axis context'],
                'canonical_ids_retrieval_only':target_ids,'known_exact_page':bool(urls),
                'request_kind':'canonical_visible_page' if 'STERBRUST' in verdict else 'supplier_visible_detail',
                'execute_http':False,'absence_not_inferred':True})
    for identity,prev in original.items():
        now=byid[identity]
        if prev['classification'] in ('EXISTING_CONFIRMED','CONFLICT'):assert now==prev
        for field in ('source','external_id','model','execution','name','source_url'):assert now.get(field)==prev.get(field)
    result={'source_rows':len(enriched),'overall':summary(enriched),'by_source':{s:summary([r for r in enriched if r['source']==s]) for s in NAMESPACES},
        'new_existing':len(promotions),'closed_by_previous_route':dict(closed),'outcome_routes':dict(routes),
        'confirmed_only_training_pairs':454,'safe_synonyms':sum(m['verdict']=='SAFE' for m in learned['synonym_mappings']),
        'safe_sections':sum(m['verdict']=='SAFE' for m in learned['section_mappings']),
        'new_groups':len(groups['groups']),'human_required_proven':len(human),'http':0,'sql':0,'global_matcher':False,
        'beka_previous_audits_reused':True,'xml_xlsx_published':False}
    save('STRICT_EVALUATIONS.json',decisions);save('PROMOTIONS.json',promotions);save('GROK_TARGETED_EVIDENCE_PLAN.json',plans)
    save('NEW_GROUPS.json',groups);save('ABSENCE_AUDIT.json',absenceaudit)
    save('MATCHING_AFTER.json',{'rows':enriched,'summary':result,'predecessor':str(PRE/'CHECKPOINT.json')})
    save('SUMMARY.json',result);print(json.dumps(result),flush=True)
if __name__=='__main__':main()
