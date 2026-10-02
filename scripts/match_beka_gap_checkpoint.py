"""Read-only post-gap matching; preserve latest decisions, no XML publication."""
from pathlib import Path
from copy import deepcopy
import os,sys,json
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
from scripts.resolve_reviews_offline import load,save,sha,EVIDENCE
from scripts.run_beka_gap_ledger import OUT,PRIOR
from scripts.intervesp_full_preflight import read_source_state
from scripts.generate_proposed_matching import read_qa_rows
from scripts.publish_full_intervesp import attach_source_evidence,complete_canonical_properties
from universal_supplier.postgres import PostgresConfig
from universal_supplier.matching_service import load_registry_from_csv
from universal_supplier.proposed_matching import classify_persisted_sources
from universal_supplier.offline_review_resolution import resolve_existing
from universal_supplier.intervesp_final_proposals import final_counts,readiness_proposals
from sterbrust_matching.normalization import normalize_brand,normalize_model,extract_model
from scripts.reconcile_cross_source_offline import clean_observed_model
from collections import defaultdict

def main():
    run=load(OUT/'RUN.json')
    if run['stage']!='INGEST_AND_REPLAY_VERIFIED': raise ValueError('Applied run not complete and verified')
    if (OUT/'MATCHING_AFTER.json').exists(): raise ValueError('Do not replace final checkpoint')
    state=read_source_state()
    os.environ['PGPASSFILE']=str(Path(os.environ['LOCALAPPDATA'])/'UniversalSupplier/rc-local/pgpass.conf')
    _,records=read_qa_rows(PostgresConfig('127.0.0.1',55449,'universal_supplier_server','rc_readonly',None,'disable'),expected_system_identifier=7691270601420084116)
    previous=load(PRIOR/'MATCHING_CROSS_SOURCE_RESOLVED.json'); old={(r['source'],str(r['external_id'])):r for r in previous['rows']}
    beka=[r for r in records if r['supplier_code'] in ('beka_mak','beka_mak_tr')]
    if sha(ROOT/'reports/STERBRUST_REGISTRY.csv')!=previous['registry_sha256']: raise ValueError('Registry CSV changed')
    registry=load_registry_from_csv(ROOT/'reports/STERBRUST_REGISTRY.csv')
    canonical=complete_canonical_properties(registry,beka,load(EVIDENCE/'BUILD_VERIFIED.json'))
    fresh=attach_source_evidence(classify_persisted_sources(beka,registry),beka,registry)
    identities=defaultdict(set)
    for pid,t in registry.products.items():
        m=clean_observed_model({'model':t.get('model') or extract_model(t['name']),'brand':t.get('brand'),'name':t['name']})['model']
        identities[(normalize_brand(t.get('brand')),normalize_model(m))].add(pid)
    new=[]; checks=[]
    learned=load(PRIOR/'UPDATED_SCOPED_EVIDENCE.json'); mappings=load(ROOT/'reports/RC_LOCAL/OFFLINE_REVIEW_COMPLETE_2026-10-01/SCOPED_CNC_MAPPINGS.json')
    for r in fresh:
        key=r['source'],str(r['external_id'])
        if key in old: continue
        if r['classification']=='EXISTING_CONFIRMED':
            pid=str(r['sterbrust_product_id']); t=canonical.get(pid)
            props=[{'name':p['property_name'],'value':p['value_flat'],'unit':p.get('unit','')} for p in t.get('properties',[])
                if p.get('property_name') and p.get('value_flat') not in (None,'','N')]
            target={'sterbrust_product_id':pid,'name':t['name'],'model':t.get('model_raw',''),'brand':t.get('brand_raw',''),
                'active':t.get('active'),'source_category':t.get('category_path',''),'observed_properties':props,
                'properties':{p['name']:p['value'] for p in props}}
            m=clean_observed_model(target)['model']; target['equivalent_identity_ids']=sorted(identities[(normalize_brand(target['brand']),normalize_model(m))])
            guard=resolve_existing({**r,'classification':'REVIEW','match_status':'HIGH_CONFIDENCE_MATCH'},target,mappings)
            checks.append({'source':r['source'],'external_id':r['external_id'],**guard})
            if not guard['confirmed']:
                r.update(classification='REVIEW',proposed_sterbrust_id=pid,sterbrust_product_id='',strict_canonical_review=guard)
        new.append(r)
    new,groups,absence=readiness_proposals(new,learned,load(EVIDENCE/'CANONICAL_ABSENCE_INDEX.json'),absence_review_ref='saved://beka_gap_pinned59500')
    final=deepcopy(previous['rows'])+new
    if len({(r['source'],str(r['external_id'])) for r in final})!=len(final): raise ValueError('Duplicate proposal identity')
    if len(final)!=len(records): raise ValueError('Proposal/commercial DB source coverage mismatch')
    save(OUT/'BEKA_RECOMPUTED_AUDIT.json',fresh)
    save(OUT/'NEW_CANONICAL_GUARDS.json',checks)
    save(OUT/'MATCHING_AFTER.json',{**previous,'rows':final,'summary':{'commercial':final_counts(final),
        'beka':final_counts([r for r in final if r['source'] in ('beka_mak','beka_mak_tr')])},
        'scope':'post_gap_readonly_proposals_no_canonical_links','new_source_count':len(new)})
    save(OUT/'NEW_GROUP_ADVISORY.json',groups)
    save(OUT/'NEW_ABSENCE_ADVISORY.json',absence)
    summary={'DONE':'6offline/13detail ledger completed and matching proposals checked','APPLIED':'isolated RC commercial source observations; no canonical links',
        'VERIFIED':'fresh identity, before/after SQL, exact-noop replay, preserved prior identities/decisions',
        'http_statuses':{str(s):sum(r['http_status']==s for r in run['http_rows']) for s in {r['http_status'] for r in run['http_rows']}},
        'http_gets':run['http_gets'],'new_beka_source_records':run['new_source_products'],'commercial_counts':final_counts(final),
        'beka_counts':final_counts([r for r in final if r['source'] in ('beka_mak','beka_mak_tr')]),
        'identity_ready':sum(bool(r.get('readiness',{}).get('identity_ready')) for r in final),
        'replay_pairs':len(run['replay_rows']),'all_replay_exact_noop':all(r['exact_noop'] for r in run['replay_rows']),
        'sql_after':state['table_counts'],'safety_counts':state['safety_counts'],
        'input_previous_sha256':sha(PRIOR/'MATCHING_CROSS_SOURCE_RESOLVED.json'),
        'output_sha256':{p.name:sha(p) for p in OUT.iterdir() if p.is_file() and p.suffix in ('.json','.dump')}}
    save(OUT/'VERIFIED.json',summary); print(json.dumps(summary,ensure_ascii=True))

if __name__=='__main__': main()
