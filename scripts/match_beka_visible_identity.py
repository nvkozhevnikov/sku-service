"""Scoped new24 matching proposals, immutable prior1802; no SQL mutation."""
from pathlib import Path
import sys,os,json,hashlib
from copy import deepcopy
from dataclasses import replace
from collections import defaultdict,Counter
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
from scripts.audit_beka_visible_identity import OUT,PRIOR,load,save,sha
from scripts.resolve_reviews_offline import EVIDENCE,BASE
from scripts.intervesp_full_preflight import read_source_state
from scripts.generate_proposed_matching import read_qa_rows
from scripts.publish_full_intervesp import attach_source_evidence
from universal_supplier.postgres import PostgresConfig
from universal_supplier.matching_service import load_registry_from_csv,_source,_candidates,_candidate
from sterbrust_matching.matching import match_product
from universal_supplier.proposed_matching import classify_persisted_sources,_card
from universal_supplier.beka_visible_identity import model
from universal_supplier.offline_review_resolution import resolve_existing,clean_observed_model
from universal_supplier.intervesp_final_proposals import final_counts,readiness_proposals,terminal_bucket
from sterbrust_matching.normalization import normalize_brand,normalize_model,extract_model

def visible_context(record):
    """Own visible specifications -> bilingual diagnostic view; raw SQL unchanged."""
    r=deepcopy(record)
    if r['supplier_code']!='beka_mak_tr': return r
    content=r['raw_data']['source_content']; props=content['technical_properties']
    names={k for k,v in props}
    translations={'Main Drive Motor':'Мощность двигателя','Band Dimensions':'Размеры ленточного полотна',
        'Blade Dimensions':'Размеры ленточного полотна','Blade Speed':'Скорость резки','Cutting Speeds':'Скорость резки'}
    if names & {'Band Dimensions','Blade Dimensions'}:
        content['source_category']+=' | ленточнопильный станок'
    elif 'Blade OD x T x t' in names:
        content['source_category']+=' | дисковый отрезной станок'
    else: return r
    content['technical_properties']=[(translations.get(k,k),v) for k,v in props]
    return r

def main(*,out_dir=OUT,previous_dir=PRIOR,expected_count=24):
    OUT=out_dir; PRIOR=previous_dir
    result_dir=OUT/'CANONICAL_VISIBLE_CONTEXT'
    if result_dir.exists(): raise ValueError('Do not replace scoped final checkpoint')
    applied=load(OUT/'APPLIED.json')
    if applied['stage']!='INGEST_AND_REPLAY_VERIFIED': raise ValueError('Applied/replay stage not verified')
    state=read_source_state(); os.environ['PGPASSFILE']=str(Path(os.environ['LOCALAPPDATA'])/'UniversalSupplier/rc-local/pgpass.conf')
    _,records=read_qa_rows(PostgresConfig('127.0.0.1',55449,'universal_supplier_server','rc_readonly',None,'disable'),expected_system_identifier=7691270601420084116)
    previous=load(PRIOR/'MATCHING_AFTER.json')
    if sha(PRIOR/'MATCHING_AFTER.json')!=applied['prior_matching_sha256']: raise ValueError('Prior proposal changed')
    old={(r['source'],str(r['external_id'])):r for r in previous['rows']}
    keys={(r['source'],str(r['external_id'])) for r in applied['rows']}
    scoped=[r for r in records if (r['supplier_code'],str(r['external_id'])) in keys]
    if len(scoped)!=expected_count: raise ValueError('Scoped SQL coverage mismatch')
    if sha(ROOT/'reports/STERBRUST_REGISTRY.csv')!=previous['registry_sha256']: raise ValueError('Pinned registry changed')
    raw_scoped=deepcopy(scoped); scoped=[visible_context(r) for r in scoped]
    registry=load_registry_from_csv(ROOT/'reports/STERBRUST_REGISTRY.csv')
    retrieval=[]; candidates={}; wanted=set(); identity_index=defaultdict(set)
    for pid,t in registry.products.items():
        m=clean_observed_model({'model':t.get('model') or extract_model(t['name']),'brand':t.get('brand'),'name':t['name']})['model']
        identity_index[(normalize_brand(t.get('brand')),normalize_model(m))].add(pid)
    for record in scoped:
        key=record['supplier_code'],str(record['external_id']); src=_source(_card(record))
        direct={str(t['sterbrust_product_id']) for t in _candidates(src,registry)}
        a=record['raw_data']['enrichment_evidence']['supplier_page_identity']; alias_ids=set()
        for alias in a['aliases']:
            token=model(alias['value'].replace('_',' ').replace('/',' '))
            ids={str(t['sterbrust_product_id']) for t in _candidates(replace(src,name=token,model=token,supplier_article=''),registry)} if token else set()
            alias_ids.update(ids)
            retrieval.append({'source':key[0],'external_id':key[1],**alias,'normalized_alias_model':token,
                'candidate_sterbrust_ids':sorted(ids,key=int),'auto_confirmation_authority':False})
        candidates[key]=direct|alias_ids; wanted.update(candidates[key])
    canonical={}; seen=set(); checksum=hashlib.sha256()
    with (ROOT/'reports/STERBRUST_REGISTRY.jsonl').open('rb') as stream:
        for line in stream:
            checksum.update(line); t=json.loads(line); pid=str(t['sterbrust_product_id'])
            if pid in seen: raise ValueError('Duplicate registry Product ID')
            seen.add(pid)
            if pid in wanted:
                canonical[pid]=t
                registry.products[pid]['properties']={p['property_name']:p['value_flat'] for p in t.get('properties',[]) if p.get('property_name') and p.get('value_flat') not in (None,'')}
    proof=load(EVIDENCE/'BUILD_VERIFIED.json')
    if len(seen)!=59500 or checksum.hexdigest()!=proof['registry_jsonl_sha256']: raise ValueError('Canonical snapshot hash/completeness mismatch')
    fresh=attach_source_evidence(classify_persisted_sources(scoped,registry),scoped,registry)
    mappings=load(ROOT/'reports/RC_LOCAL/OFFLINE_REVIEW_COMPLETE_2026-10-01/SCOPED_CNC_MAPPINGS.json')
    checks=[]; new=[]
    for r in fresh:
        key=r['source'],str(r['external_id']); passed=[]
        for pid in sorted(candidates[key],key=int):
            t=canonical.get(pid)
            if not t: continue
            props=[{'name':p['property_name'],'value':p['value_flat'],'unit':p.get('unit','')} for p in t.get('properties',[]) if p.get('property_name') and p.get('value_flat') not in (None,'','N')]
            target={'sterbrust_product_id':pid,'name':t['name'],'model':t.get('model_raw',''),'brand':t.get('brand_raw',''),
                'active':t.get('active'),'source_category':t.get('category_path',''),'observed_properties':props,'properties':{p['name']:p['value'] for p in props}}
            m=clean_observed_model(target)['model']; target['equivalent_identity_ids']=sorted(identity_index[(normalize_brand(target['brand']),normalize_model(m))],key=int)
            # Requests the same strict pure guard, not acceptance from an alias.
            # Diagnostics from another retrieved candidate cannot be inherited
            # as facts about this target. Evaluate this exact pair independently
            # with the original matcher, then the unchanged strict pure guard.
            card=_card(next(x for x in scoped if (x['supplier_code'],str(x['external_id']))==key))
            pair=match_product(_source(card),[_candidate(registry.products[pid])])
            guard=resolve_existing({**r,'classification':'REVIEW','match_status':'HIGH_CONFIDENCE_MATCH',
                'match_conflicts':pair.conflicts},target,mappings)
            checks.append({'source':key[0],'external_id':key[1],'retrieved_candidate':pid,**guard})
            if guard['confirmed']: passed.append((pid,target,guard))
        if len(passed)==1 and r['classification']!='CONFLICT':
            pid,target,guard=passed[0]
            r.update(classification='EXISTING_CONFIRMED',sterbrust_product_id=pid,sterbrust_name=target['name'],
                proposed_sterbrust_id='',full_model_confirmed=True,visible_identity_canonical_proof=guard)
        elif r['classification']=='EXISTING_CONFIRMED':
            r.update(classification='REVIEW',proposed_sterbrust_id=r['sterbrust_product_id'],sterbrust_product_id='',
                strict_canonical_reason='NO_UNIQUE_FULL_GUARD_PASS')
        r['source_identity_resolution']='SOURCE_IDENTITY_RESOLVED_VISIBLE_CARD'
        new.append(r)
    learned=load(ROOT/'reports/RC_LOCAL/CROSS_SOURCE_FINAL_VERIFIED_2026-10-01/UPDATED_SCOPED_EVIDENCE.json')
    models={normalize_model(r['model']) for r in new}
    peers=[deepcopy(r) for r in previous['rows'] if normalize_model(r.get('model','')) in models and r['classification'] in ('REVIEW','NEW_CANDIDATE')]
    enriched,groups,absence=readiness_proposals(new+peers,learned,load(EVIDENCE/'CANONICAL_ABSENCE_INDEX.json'),absence_review_ref='saved://visible_beka_pinned59500')
    new=[r for r in enriched if (r['source'],str(r['external_id'])) in keys]
    final=deepcopy(previous['rows'])+new
    if len(final)!=len(records) or len({(r['source'],str(r['external_id'])) for r in final})!=len(final): raise ValueError('Merged coverage/duplicate failure')
    for r in final:
        key=r['source'],str(r['external_id'])
        if key in old and old[key]!=r: raise ValueError('Prior decision changed')
    result_dir.mkdir()
    save(result_dir/'RAW_SCOPED_SOURCE.json',raw_scoped)
    save(result_dir/'ALIAS_RETRIEVAL_AUDIT.json',retrieval); save(result_dir/'CANONICAL_GUARD_AUDIT.json',checks)
    save(result_dir/'NEW_GROUP_ADVISORY.json',groups); save(result_dir/'ABSENCE_ADVISORY.json',absence)
    save(result_dir/'MATCHING_AFTER.json',{**previous,'rows':final,'scope':'visible_card_saved29_resolved24_proposals_only',
        'summary':{'commercial':final_counts(final),'beka':final_counts([r for r in final if r['source'] in ('beka_mak','beka_mak_tr')])},
        'source_resolved_new_rows':expected_count,'prior_decisions_preserved':True,'prior_decision_count':len(old)})
    audit=load(OUT/'SOURCE_IDENTITY_AUDIT.json'); verdicts=Counter(a['verdict'] for a in audit)
    counts=lambda rs:{'Existing':sum(r['classification']=='EXISTING_CONFIRMED' for r in rs),
        'READY':sum(r.get('readiness',{}).get('identity_ready',False) for r in rs),
        'Review':sum(terminal_bucket(r) not in ('EXISTING_CONFIRMED','CONFLICT','READY_TO_CREATE_IDENTITY','READY_TO_CREATE_FULL') for r in rs),
        'Conflict':sum(r['classification']=='CONFLICT' for r in rs)}
    summary={'source_verdicts':dict(verdicts),'human_resolved':sum(a['origin']=='HUMAN' and a['verdict']=='SOURCE_IDENTITY_RESOLVED_VISIBLE_CARD' for a in audit),
        'http_review_resolved':sum(a['origin']=='HTTP_REVIEW' and a['verdict']=='SOURCE_IDENTITY_RESOLVED_VISIBLE_CARD' for a in audit),
        'new_source_rows':expected_count,'http_gets':0,'beka':counts([r for r in final if r['source'] in ('beka_mak','beka_mak_tr')]),
        'commercial':counts(final),'source_resolved_canonical_review':counts(new)['Review'],'new_scoped_canonical_counts':counts(new),
        'prior_decisions_preserved':True,'prior_decision_count':len(old),'sql_counts':state['table_counts'],'safety_counts':state['safety_counts'],
        'all_scoped_replay_exact_noop':len(applied['replay'])==expected_count and all(r['exact_noop'] for r in applied['replay']),
        'registry_rows':len(seen),'registry_jsonl_sha256':checksum.hexdigest(),'alias_records':len(retrieval)}
    save(result_dir/'SUMMARY.json',summary); print(json.dumps(summary,ensure_ascii=True))

if __name__=='__main__': main()
