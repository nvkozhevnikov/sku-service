"""Revisit final saved reviews only. No database dependency or public GET."""
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timezone
import argparse
import csv
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.verify_full_intervesp_outputs import workbook_rows
from universal_supplier.offline_review_resolution import resolve_existing, clean_observed_model, reason_clusters, learn_cnc_mappings
from sterbrust_matching.normalization import normalize_model, normalize_brand, extract_model
from universal_supplier.intervesp_final_proposals import readiness_proposals, final_counts, terminal_bucket
from universal_supplier.proposal_export import new_candidate_id
from universal_supplier.characteristic_evidence import build_evidence, equipment_scope
from scripts.build_characteristic_evidence import section_evidence

BASE=ROOT/'reports/RC_LOCAL/INTERVESP_FULL_2026-10-01/FINAL_PROPOSALS'
EVIDENCE=ROOT/'reports/RC_LOCAL/CHARACTERISTIC_EVIDENCE_2026-10-01_VERIFIED'

def load(p): return json.loads(p.read_text(encoding='utf-8'))
def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''): h.update(block)
    return h.hexdigest()
def save(p,v): p.write_text(json.dumps(v,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
def output_csv(p,rows,columns):
    with p.open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=columns); w.writeheader()
        for r in rows:
            values={}
            for k in columns:
                value=r.get(k)
                if isinstance(value,(list,dict)): value=json.dumps(value,ensure_ascii=False)
                if isinstance(value,str) and value[:1] in ('=','+','-','@'): value="'"+value
                values[k]=value
            w.writerow(values)

def run(output):
    if output.exists(): raise ValueError('Preserve previous offline output; choose a new directory')
    proof=load(BASE/'FINAL_FILES_VERIFIED.json')
    for name,digest in proof['output_sha256'].items():
        if sha(BASE/name)!=digest: raise ValueError('Final source file changed: '+name)
    for name,digest in load(BASE/'FINAL_OUTPUTS_VERIFIED.json')['output_sha256'].items():
        if sha(BASE/name)!=digest: raise ValueError('Pinned producer evidence changed: '+name)
    matching=load(BASE/'MATCHING_FINAL.json'); rows=deepcopy(matching['rows'])
    old={(r['source'],str(r['external_id'])):r for r in matching['rows']}
    reviews=[r for r in rows if terminal_bucket(r) in ('REVIEW','CONFLICT')]
    sheet=workbook_rows(BASE/'STERBRUST_REVIEW.xlsx',len(reviews))
    if {(r['A'],r['B']) for r in sheet}!={(r['source'],str(r['external_id'])) for r in reviews}:
        raise ValueError('Workbook/evidence source identities do not reconcile')
    learned=load(BASE/'SCOPED_CHARACTERISTIC_EVIDENCE.json')
    original_pairs=load(BASE/'CONFIRMED_PAIR_CHARACTERISTICS.json')
    cnc_mappings=learn_cnc_mappings(original_pairs)
    absence=load(EVIDENCE/'CANONICAL_ABSENCE_INDEX.json')
    target_ids={str(r['proposed_sterbrust_id']) for r in reviews if r.get('proposed_sterbrust_id')}
    canonical={}; h=hashlib.sha256(); seen=set()
    with (ROOT/'reports/STERBRUST_REGISTRY.jsonl').open('rb') as f:
        for line in f:
            h.update(line); item=json.loads(line); pid=str(item['sterbrust_product_id'])
            if pid in seen: raise ValueError('Duplicate saved canonical identity')
            seen.add(pid)
            if pid in target_ids:
                props=[{'name':p['property_name'],'value':p['value_flat'],'unit':p.get('unit','')}
                       for p in item.get('properties',[]) if p.get('property_name') and p.get('value_flat') not in (None,'','N')]
                canonical[pid]={'sterbrust_product_id':pid,'name':item['name'],'model':item.get('model_raw',''),
                                'brand':item.get('brand_raw',''),'active':item.get('active'),
                                'source_category':item.get('category_path',''),'observed_properties':props,
                                'properties':{p['name']:p['value'] for p in props},
                                'category_ids':item.get('category_ids') or [item.get('category_id')]}
    baseline=load(EVIDENCE/'BUILD_VERIFIED.json')
    if h.hexdigest()!=baseline['registry_jsonl_sha256'] or len(seen)!=59500:
        raise ValueError('Canonical snapshot hash/completeness failed')
    identity_index=defaultdict(set)
    csv_path=ROOT/'reports/STERBRUST_REGISTRY.csv'
    if sha(csv_path)!=matching['registry_sha256']: raise ValueError('Canonical CSV hash changed')
    with csv_path.open(encoding='utf-8-sig',newline='') as f:
        for item in csv.DictReader(f):
            if item.get('active')!='Y': continue
            brand=normalize_brand(item.get('brand_raw'))
            for model in (item.get('model_raw'),extract_model(item.get('name'))):
                clean=clean_observed_model({'model':model,'brand':item.get('brand_raw'),'name':item.get('name')})['model']
                identity_index[(brand,normalize_model(clean,brand))].add(str(item['sterbrust_product_id']))
    for target in canonical.values():
        brand=normalize_brand(target['brand'])
        clean=clean_observed_model({**target,'model':target['model'] or extract_model(target['name'])})['model']
        key=normalize_model(clean,brand)
        target['equivalent_identity_ids']=sorted(identity_index[(brand,key)])
    audits=[]; changes=[]
    for r in rows:
        if r['classification']=='REVIEW' and r['match_status']=='HIGH_CONFIDENCE_MATCH':
            result=resolve_existing(r,canonical.get(str(r.get('proposed_sterbrust_id'))),cnc_mappings)
            audits.append({'source':r['source'],'external_id':r['external_id'],'source_url':r['source_url'],**result})
            if result['confirmed']:
                normalized=result['evidence']['model_normalization']['model']
                r.update(classification='EXISTING_CONFIRMED',sterbrust_product_id=r['proposed_sterbrust_id'],
                         sterbrust_name=canonical[r['proposed_sterbrust_id']]['name'],full_model_confirmed=True,
                         raw_model=r['model'],raw_execution=r['execution'],model=normalized,execution=normalized,
                         model_key=normalize_model(normalized,r.get('brand')),proposed_sterbrust_id='',
                         offline_existing_resolution=result,readiness={'state':'EXISTING_CONFIRMED','identity_ready':False,'full_ready':False})
                changes.append({'source':r['source'],'external_id':r['external_id'],'before':'REVIEW','after':'EXISTING_CONFIRMED',
                                'evidence':result['evidence']})
    section_path=ROOT/'reports/rest/STERBRUST_SECTIONS_RAW.json'
    if sha(section_path)!=load(BASE/'FINAL_OUTPUTS_VERIFIED.json')['section_snapshot_sha256']:
        raise ValueError('Pinned section snapshot changed')
    sections={str(s['id']):s for s in load(section_path)}
    combined_pairs=deepcopy(original_pairs)
    promoted={(r['source'],str(r['external_id'])) for r in changes}
    for r in rows:
        if (r['source'],str(r['external_id'])) not in promoted: continue
        target=canonical[r['sterbrust_product_id']]
        combined_pairs.append({**r,'equipment_type':equipment_scope(r['name'],r['source_category']),
            'source_properties':r['observed_properties'],'sterbrust_properties':target['observed_properties'],
            'sections':section_evidence(target['category_ids'],sections),
            'confirmation_basis':'exact_unique_full_identity_and_typed_properties_offline_proposal'})
    learned=build_evidence(combined_pairs)
    # Only pre-group NEW status is eligible; prior conflicts and quarantines
    # are never converted to candidate truth or silently dropped from cohorts.
    proposed=[]; new_prechecks=[]
    for r in rows:
        if r.get('match_status')!='NEW_CANDIDATE': continue
        candidate=deepcopy(r); clean=clean_observed_model(r)
        hard=[]
        if not clean['model']: hard.append('FULL_MODEL_MISSING')
        if r.get('detail_review_reason') or r.get('listing_identity_reason'): hard.append('SOURCE_IDENTITY_QUARANTINED')
        if r.get('prior_review_retained'): hard.append('PRIOR_REVIEW_REQUIRES_OPERATOR')
        if r.get('match_conflicts') or r.get('semantic_conflicts'): hard.append('UPSTREAM_CONTRADICTION')
        if r['classification']=='CONFLICT': hard.append('CONFIRMED_CONFLICT')
        if r.get('axis_execution_evidence',{}).get('blocked'): hard.append('AXIS_EXECUTION_GUARD_BLOCKED')
        if r.get('condition_execution_evidence',{}).get('blocked'): hard.append('CONDITION_EXECUTION_GUARD_BLOCKED')
        if not hard:
            candidate.update(classification='NEW_CANDIDATE',raw_model=r['model'],model=clean['model'],execution=clean['model'],
                             new_candidate_id=r.get('new_candidate_id') or new_candidate_id(r['source'],r['external_id'],clean['model']))
        else:
            candidate['classification']='REVIEW'
        candidate['offline_normalization']=clean
        candidate['offline_precheck_reasons']=hard
        proposed.append(candidate)
        new_prechecks.append({'source':r['source'],'external_id':r['external_id'],'blocking_reasons':hard})
    enriched,groups,absence_audit=readiness_proposals(proposed,learned,absence,
        absence_review_ref='saved://offline_review_resolution/pinned_59500_active_inactive_registry')
    by_key={(r['source'],str(r['external_id'])):r for r in enriched}
    final=[by_key.get((r['source'],str(r['external_id'])),r) for r in rows]
    # Immutable original Existing/Conflict. They never participate in this
    # promotion path, even when match_status still carries a historical NEW.
    for i,r in enumerate(final):
        original=old[(r['source'],str(r['external_id']))]
        if original['classification'] in ('EXISTING_CONFIRMED','CONFLICT'): final[i]=deepcopy(original)
    new_audits=[]; grok=[]
    for r in enriched:
        blockers=set(r.get('offline_precheck_reasons',[]))
        blockers.update(r.get('grouping_reasons',[]))
        blockers.update(r.get('readiness',{}).get('identity_blockers',[]))
        advisory=r.get('readiness_advisory',{})
        blockers.update(advisory.get('CHARACTERISTIC_CONTRADICTION',{}).get('reasons',[]))
        for key in advisory.get('CHARACTERISTIC_IDENTITY_SUPPORT',{}).get('missing_required_properties',[]):
            blockers.add('MISSING_CHARACTERISTIC:'+key)
        audit={'source':r['source'],'external_id':r['external_id'],'source_url':r['source_url'],
               'blocking_reasons':sorted(blockers),'classification':r['classification'],
               'readiness':r.get('readiness'),'grouping_reasons':r.get('grouping_reasons',[]),
               'offline_normalization':r.get('offline_normalization'),
               'readiness_advisory':advisory}
        new_audits.append(audit)
        if not r.get('offline_precheck_reasons'):
            grok.append({k:r.get(k) for k in ('source','external_id','new_candidate_id','brand','model','raw_model',
                'execution','model_role','product_kind','source_category','source_url','evidence_ref','properties',
                'observed_properties','grouping_reasons','new_group_id','proposed_section_id','readiness')})
    counts=final_counts(final)
    inter=final_counts([r for r in final if r['source']=='intervesp'])
    identities={(r['source'],str(r['external_id'])) for r in final}
    if len(identities)!=len(final) or identities!=set(old): raise ValueError('Offline source identity coverage changed')
    for r in final:
        original=old[(r['source'],str(r['external_id']))]
        if original['classification'] in ('EXISTING_CONFIRMED','CONFLICT') and r!=original:
            raise ValueError('Original confirmed decision changed')
        if original.get('new_candidate_id') and r.get('new_candidate_id')!=original['new_candidate_id']:
            raise ValueError('Source NEW_CANDIDATE_ID changed')
    # Retrieval cohorts for Grok are explicitly NOT equivalence groups.
    cohorts=defaultdict(list)
    for r in grok:
        cohorts[(normalize_brand(r.get('brand')),normalize_model(r.get('model'),r.get('brand')))].append(r)
    cross=[{'brand_key':key[0],'full_model_key':key[1],'status':'RETRIEVAL_ONLY_NOT_PROVEN_EQUIVALENT',
            'source_records':members} for key,members in sorted(cohorts.items())
           if key[0] and key[1] and len({r['source'] for r in members})>1]
    output.mkdir(parents=True)
    save(output/'MATCHING_OFFLINE_RESOLVED.json',{**matching,'generated_at':datetime.now(timezone.utc).isoformat(),
        'evidence_scope':'saved_snapshot_offline_proposals_only','rows':final,'summary':{'all':counts,'intervesp':inter}})
    save(output/'HIGH_CONFIDENCE_AUDIT.json',audits)
    save(output/'SCOPED_CNC_MAPPINGS.json',cnc_mappings)
    save(output/'UPDATED_CONFIRMED_PAIR_EVIDENCE.json',combined_pairs)
    save(output/'UPDATED_SCOPED_EVIDENCE.json',learned)
    save(output/'NEW_GATE_AUDIT.json',new_audits)
    save(output/'GROK_NEW_EQUIVALENCE_CANDIDATES.json',{'not_confirmed_new':True,'no_product_id_created':True,
        'instructions':'Prove full execution and independent typed equivalence; ambiguity stays REVIEW. No crawl authorization implied.',
        'rows':grok})
    save(output/'GROK_CROSS_SOURCE_RETRIEVAL.json',cross)
    output_csv(output/'GROK_NEW_EQUIVALENCE_CANDIDATES.csv',grok,
        ['source','external_id','new_candidate_id','brand','model','raw_model','execution','model_role','product_kind',
         'source_category','source_url','evidence_ref','properties','grouping_reasons','proposed_section_id','readiness'])
    existing_audit={(a['source'],str(a['external_id'])):a for a in audits}
    new_audit={(a['source'],str(a['external_id'])):a for a in new_audits}
    unresolved=[]
    for r in final:
        if terminal_bucket(r) not in ('REVIEW','CONFLICT'): continue
        key=(r['source'],str(r['external_id']))
        if key in existing_audit: reasons=existing_audit[key]['blocking_reasons']
        elif key in new_audit: reasons=new_audit[key]['blocking_reasons']
        else:
            reasons=[]
            for field,label in (('match_conflicts','MATCH_CONFLICT'),('match_warnings','MATCH_WARNING')):
                value=r.get(field)
                if isinstance(value,dict): reasons.extend(label+':'+k for k in value)
                elif isinstance(value,list): reasons.extend(label+':'+str(k) for k in value)
                elif value: reasons.append(label+':'+str(value))
            if r.get('detail_review_reason'): reasons.append('DETAIL_REVIEW:'+str(r['detail_review_reason']))
            if r.get('listing_identity_reason'): reasons.append('LISTING_IDENTITY:'+r['listing_identity_reason'])
            if r.get('prior_review_retained'): reasons.append('PRIOR_REVIEW_REQUIRES_OPERATOR')
            if r.get('axis_execution_evidence',{}).get('blocked'): reasons.append('AXIS_EXECUTION_GUARD_BLOCKED')
            if r.get('condition_execution_evidence',{}).get('blocked'): reasons.append('CONDITION_EXECUTION_GUARD_BLOCKED')
            if not reasons: reasons.append('UPSTREAM_STATUS:'+r['match_status'])
        unresolved.append({'source':key[0],'external_id':key[1],'classification':terminal_bucket(r),
                           'source_url':r['source_url'],'blocking_reasons':sorted(set(reasons))})
    clusters={'high_confidence':reason_clusters(audits),'new_candidate':reason_clusters(new_audits),
              'all_remaining_review_conflict':reason_clusters(unresolved)}
    save(output/'ALL_UNRESOLVED_GATE_AUDIT.json',unresolved)
    save(output/'REASON_CLUSTERS.json',clusters)
    initial=load(ROOT/'reports/RC_LOCAL/OFFLINE_REVIEW_2026-10-01/HIGH_CONFIDENCE_AUDIT.json')
    first={(a['source'],str(a['external_id'])):set(a['blocking_reasons']) for a in initial}
    cleared=Counter(reason for a in audits for reason in first[(a['source'],str(a['external_id']))]-set(a['blocking_reasons']))
    closed_model=sum(a['evidence']['model_normalization']['changed'] and 'FULL_MODEL_EXECUTION_MISMATCH' not in a['blocking_reasons']
                     for a in audits)
    save(output/'AUTO_CLOSABLE_GATES.json',{'all_other_guards_unchanged':True,
        'full_model_gate_closed_after_source_prefix_proof':closed_model,
        'additional_gate_closures_from_confirmed_pair_typed_mapping':dict(cleared),
        'confirmed_existing_promotions':len(changes),
        'http_plan':[],'http_gets':0,
        'http_policy':'No URL is authorized merely for generic missing evidence. Every future GET requires a named blocking gate and independently plausible resolution.'})
    output_csv(output/'REASON_CLUSTERS.csv',[{'stage':stage,**v} for stage,values in clusters.items() for v in values],
               ['stage','reason','blocked_rows'])
    save(output/'DECISION_CHANGES.json',changes)
    summary={'generated_at':datetime.now(timezone.utc).isoformat(),'DONE':'offline high-confidence and NEW review pass',
        'APPLIED':'new isolated proposal files only; no database or panel mutation','VERIFIED':'pinned workbook/source identities, registry SHA, immutable Existing/Conflict',
        'http_gets':0,'database_accesses':0,'input_review_rows':len(reviews),'high_confidence_review_rows':len(audits),
        'new_status_rows':len(new_audits),'new_grok_rows':len(grok),'existing_promotions':len(changes),
        'before_counts':final_counts(matching['rows']),'after_counts':counts,'intervesp_after':inter,
        'ready_identity_source_rows':sum(bool(r.get('readiness',{}).get('identity_ready')) for r in final),
        'strict_groups':len(groups['groups']),
        'grok_cross_source_retrieval_cohorts':len(cross),
        'safe_cnc_mappings':sum(m['verdict']=='SAFE' for m in cnc_mappings),
        'safe_section_mappings_after_existing_resolution':sum(m['verdict']=='SAFE' for m in learned['section_mappings']),
        'all_unresolved_rows_audited':len(unresolved),
        'input_sha256':{'review_xlsx':sha(BASE/'STERBRUST_REVIEW.xlsx'),'matching_json':sha(BASE/'MATCHING_FINAL.json'),
                       'registry_jsonl':h.hexdigest(),'typed_evidence':sha(BASE/'SCOPED_CHARACTERISTIC_EVIDENCE.json'),
                       'absence_index':sha(EVIDENCE/'CANONICAL_ABSENCE_INDEX.json'),
                       'confirmed_pairs':sha(BASE/'CONFIRMED_PAIR_CHARACTERISTICS.json'),
                       'without_cnc_baseline_audit':sha(ROOT/'reports/RC_LOCAL/OFFLINE_REVIEW_2026-10-01/HIGH_CONFIDENCE_AUDIT.json')},
        'reason_counts_overlap':True,'output_sha256':{p.name:sha(p) for p in output.iterdir()}}
    save(output/'VERIFIED.json',summary)
    print(json.dumps({k:v for k,v in summary.items() if k not in ('output_sha256','input_sha256')},ensure_ascii=True))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True); args=p.parse_args();run(args.output.resolve())
