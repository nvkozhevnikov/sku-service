"""One provisional five-namespace matching/grouping pass; read-only SQL."""
from pathlib import Path
from copy import deepcopy
from collections import Counter,defaultdict
from decimal import Decimal
import json,sys,hashlib
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.apply_fresh_refresh_rc import connect,OUT,COMMIT,save,load
from scripts.intervesp_full_preflight import read_source_state
from psycopg.rows import dict_row
from universal_supplier.models import ProductCard,PropertyValue,CategoryNode
from universal_supplier.matching_service import load_registry_from_csv,match_cards,_source
from universal_supplier.beka_canonical_offline import canonical_view,SavedCandidateIndex,evaluate_row
from universal_supplier.offline_review_resolution import clean_observed_model
from universal_supplier.new_group_proposals import propose_new_groups
from universal_supplier.intervesp_final_proposals import readiness_proposals,final_counts
from universal_supplier.proposal_export import new_candidate_id
from sterbrust_matching.normalization import normalize_brand,normalize_model,model_tokens,brand_evidenced_by_name
from sterbrust_matching.product_identity import classify_model_role,classify_product_kind
from universal_supplier.execution_evidence import axis_execution_guard,condition_execution_guard

NAMESPACES=frozenset(('partner_st','optimum','intervesp','beka_mak','beka_mak_tr'))
def summary(rows):
    c=Counter(r['classification'] for r in rows);ready=sum(r.get('readiness',{}).get('identity_ready',False) for r in rows)
    return {'Existing':c['EXISTING_CONFIRMED'],'READY_TO_CREATE_IDENTITY':ready,'Review':len(rows)-c['EXISTING_CONFIRMED']-c['CONFLICT']-ready,'Conflict':c['CONFLICT']}
def read_records():
    read_source_state()
    with connect(True) as c,c.cursor(row_factory=dict_row) as cur:
        cur.execute("""SELECT s.code supplier_code,sp.id source_product_id,sp.external_id,sp.name,sp.sku,
            sp.source_url,sp.canonical_url,sp.brand_raw,sp.raw_data,sp.description_text,sp.description_html,
            o.id offer_id,o.price,o.currency,o.availability_raw,o.availability_normalized,
            COALESCE((SELECT jsonb_agg(jsonb_build_object('name',p.raw_name,'value',p.raw_value,'unit',p.raw_unit))
                 FROM product_properties p WHERE p.source_product_id=sp.id AND p.offer_id IS NULL),'[]') properties,
            COALESCE((SELECT jsonb_agg(jsonb_build_object('name',sc.name,'url',sc.source_url))
                 FROM source_product_categories pc JOIN supplier_categories sc ON sc.id=pc.category_id
                 WHERE pc.source_product_id=sp.id),'[]') categories
            FROM source_products sp JOIN suppliers s ON s.id=sp.supplier_id
            JOIN offers o ON o.source_product_id=sp.id AND o.offer_kind='default' ORDER BY s.code,sp.external_id""")
        records=cur.fetchall()
    assert len(records)==4407 and len({(r['supplier_code'],r['external_id']) for r in records})==4407
    return records
def card(r):
    raw=r['raw_data'] or {};content=raw.get('source_content') or {};fresh=(raw.get('_catalog_refresh') or {}).get('current') or {}
    props=r['properties'] or [{'name':k,'value':v,'unit':''} for k,v in content.get('technical_properties',[])]
    categories=r['categories'] or ([{'name':content.get('source_category'),'url':content.get('source_category_url')}]
        if content.get('source_category') else [])
    if not categories and fresh.get('category_name'):categories=[{'name':fresh['category_name'],'url':fresh.get('category_url')}]
    identity=raw.get('source_identity') or {}
    brand=r.get('brand_raw') or identity.get('brand') or brand_evidenced_by_name(r['name'])
    if not brand and r['supplier_code'] in ('beka_mak','beka_mak_tr'):brand='Beka-Mak'
    return ProductCard(supplier_code=r['supplier_code'],external_id=r['external_id'],sku=r['sku'] or '',
        name=r['name'],requested_url=r['source_url'],final_url=r['source_url'],canonical_url=r['canonical_url'],
        http_status=200,redirect_urls=(),price=r['price'],old_price=None,currency=r['currency'] or '',
        availability_raw=r['availability_raw'] or '',availability_normalized=r['availability_normalized'] or 'unknown',
        quantity=None,description_text=r['description_text'] or content.get('description_text',''),description_html=r['description_html'] or '',
        brand=brand,properties=tuple(PropertyValue(p['name'],str(p['value'] or ''),p.get('unit') or '') for p in props),
        categories=tuple(CategoryNode(p['name'],p.get('url') or '',i) for i,p in enumerate(categories)),raw_data=raw)
def reassess_historical_hold(row,index):
    view=deepcopy(row);old_conflicts=view.get('match_conflicts') or {}
    factual=[]
    # Remove the hold only in the evaluation view; the saved original remains
    # audit evidence. A source/semantic/typed contradiction is never discarded.
    if isinstance(old_conflicts,dict) and old_conflicts.get('prior_source_review_not_auto_cleared'):
        nested=old_conflicts.get('prior_conflicts',{})
        if isinstance(nested,str):
            try:nested=json.loads(nested)
            except ValueError:factual.append('UNPARSED_PRIOR_CONFLICT');nested={'unparsed':True}
        if not nested:view['match_conflicts']={}
        elif set(nested)=={'model'}:
            # Prior retrieved canonical mismatch is not a visible-field conflict.
            # Every current target still undergoes the full exact execution test.
            view['match_conflicts']={}
        elif nested in (['prior_review_requires_manual_identity_confirmation'],['source_url_not_in_pinned_full_manifest']):
            view['match_conflicts']={} # historical workflow/scope hold, not factual contradiction
        elif (isinstance(nested,dict) and set(nested)=={'semantic_identity'}
                and nested['semantic_identity']==['name_compatibility_below_brand_model_threshold']):
            view['match_conflicts']={} # insufficient old score; current strict evidence decides
        else:factual.append('RETAINED_FACTUAL_CONFLICT');view['match_conflicts']=nested
    elif old_conflicts:factual.append('RETAINED_UPSTREAM_CONFLICT')
    view['prior_review_retained']=False
    audit=evaluate_row(view,index);audit['historical_hold_evaluated']=True
    audit['historical_hold_original_conflicts']=old_conflicts
    if factual:audit['confirmed']=None;audit['blocking_reasons']=sorted(set(audit['blocking_reasons']+factual))
    if audit['confirmed']:audit['hold_verdict']='HISTORICAL_HOLD_RELEASED_STRICT_PROOF'
    elif factual or any(x in audit['blocking_reasons'] for x in ('SOURCE_IDENTITY_QUARANTINED','SOURCE_EXECUTION_FIELD_DISAGREES')):
        audit['hold_verdict']='FACTUAL_OR_UNPARSED_BLOCKER_REQUIRES_REVIEW'
    else:audit['hold_verdict']='CANONICAL_SIDE_EVIDENCE_REQUIRED_NOT_HUMAN_BY_DEFAULT'
    return audit
def main():
    if (OUT/'CONSOLIDATED_MATCHING.json').exists():raise RuntimeError('Completed pass exists; use checkpoint, do not rerun')
    applied=load(OUT/'APPLIED.json');assert applied['replay_exact_no_op']
    predecessor=load(ROOT/'reports/RC_LOCAL/BEKA_CANONICAL_OFFLINE_2026-10-02/ACCEPTED/MATCHING_AFTER.json')
    old={(r['source'],str(r['external_id'])):r for r in predecessor['rows']}
    records=read_records();cards=[card(r) for r in records]
    registry=load_registry_from_csv(ROOT/'reports/STERBRUST_REGISTRY.csv')
    fresh_path=Path(load(OUT/'EVIDENCE_VERIFIED.json')['fresh_raw_external_path'])
    targets={};absence={'complete':True,'sha256':load(OUT/'EVIDENCE_VERIFIED.json')['fresh_raw_sha256'],
            'models':defaultdict(list),'families':defaultdict(list)};h=hashlib.sha256();ids=set()
    import re
    for line in fresh_path.open('rb'):
        h.update(line);r=json.loads(line);pid=str(r['sterbrust_product_id']);assert pid not in ids;ids.add(pid)
        brand=normalize_brand(r.get('brand_raw'))
        for key in ({normalize_model(r.get('model_raw'),brand)}|set(model_tokens(r.get('name',''))))-{''}:
            absence['models'][key].append(pid)
            family=re.match(r'[a-zа-я]+\d+',key)
            absence['families'][family.group(0) if family else key].append(pid)
        if pid in registry.products:
            registry.products[pid]['properties']={p['property_name']:p['value_flat'] for p in r.get('properties',[]) if p.get('property_name') and p.get('value_flat') not in (None,'')}
        if brand=='beka-mak':targets[pid]=canonical_view(r)
    assert len(ids)==59500 and h.hexdigest()==absence['sha256']
    index=SavedCandidateIndex(targets)
    # Exactly one global matcher call, then predecessor reconciliation. Old
    # confirmed decisions are not replaced by a different fuzzy retrieval.
    cache=OUT/'MATCHER_PASS_CACHE.json'
    card_sha=hashlib.sha256(json.dumps([c.as_jsonable() for c in cards],sort_keys=True,ensure_ascii=False,default=str).encode()).hexdigest()
    if cache.exists():
        stored=load(cache);assert stored['card_sha256']==card_sha and stored['registry_sha256']==absence['sha256']
        matches=stored['matches']
    else:
        matches=match_cards(cards,registry)
        save('MATCHER_PASS_CACHE.json',{'card_sha256':card_sha,'registry_sha256':absence['sha256'],'matches':matches})
    assert len(matches)==4407
    rows=[];holds=[];legacy_audits=[]
    for record,c,m in zip(records,cards,matches):
        key=c.supplier_code,c.external_id
        if key in old:
            row=deepcopy(old[key])
            if row.get('prior_review_retained') and row['classification']!='EXISTING_CONFIRMED' and row['source'] in ('beka_mak','beka_mak_tr'):
                audit=reassess_historical_hold(row,index);holds.append(audit)
                if audit['confirmed']:
                    chosen=audit['confirmed'];row.update(classification='EXISTING_CONFIRMED',sterbrust_product_id=chosen['sterbrust_product_id'],
                        proposed_sterbrust_id='',sterbrust_name=chosen['name'],full_model_confirmed=True,
                        prior_review_retained=False,historical_hold_resolution=audit)
            rows.append(row);continue
        own=_source(c);model=own.model or '';target=registry.products.get(m['sterbrust_product_id'])
        target_keys=({normalize_model(target.get('model'),target.get('brand'))}|set(model_tokens(target.get('name','')))) if target else set()
        full=bool(model and normalize_model(model,c.brand) in target_keys)
        confirmed=m['status'] in ('EXACT_MATCH','HIGH_CONFIDENCE_MATCH') and m['auto_accepted']=='TRUE' and full
        axis=axis_execution_guard(c.name,{p.name:p.value for p in c.properties},target.get('name','') if target else '',target.get('properties',{}) if target else {})
        condition=condition_execution_guard(c.name,{p.name:p.value for p in c.properties},target.get('name','') if target else '',target.get('properties',{}) if target else {})
        if axis['blocked'] or condition['blocked']:confirmed=False
        conflict=(axis.get('contradiction') or condition.get('contradiction'))
        # Fuzzy candidate disagreement alone is never a factual Conflict.
        classification='EXISTING_CONFIRMED' if confirmed else ('CONFLICT' if conflict else ('NEW_CANDIDATE' if m['status']=='NEW_CANDIDATE' else 'REVIEW'))
        fresh=(record['raw_data'].get('_catalog_refresh') or {})['current']
        row={'source':c.supplier_code,'external_id':c.external_id,'source_product_id':record['source_product_id'],'offer_id':record['offer_id'],
            'source_url':c.canonical_url,'name':c.name,'brand':c.brand,'model':model,'execution':model,
            'source_category':' / '.join(p.name for p in c.categories),'model_role':classify_model_role(c.name,' / '.join(p.name for p in c.categories),model).role,
            'product_kind':classify_product_kind(c.name,' / '.join(p.name for p in c.categories)).product_kind,
            'properties':{p.name:p.value for p in c.properties},'observed_properties':[{'name':p.name,'value':p.value,'unit':p.unit} for p in c.properties],
            'classification':classification,'sterbrust_product_id':m['sterbrust_product_id'] if confirmed else '',
            'proposed_sterbrust_id':m['sterbrust_product_id'] if not confirmed else '',
            'match_status':m['status'],'match_conflicts':m['conflicts'],'full_model_confirmed':full,
            'price':fresh['normalized_price'],'price_state':fresh['price_state'],'availability':c.availability_normalized,
            'evidence_ref':f'git://{COMMIT}/catalog_refresh/evidence/{c.supplier_code}/CATALOG.jsonl#{c.external_id}',
            'new_candidate_id':new_candidate_id(c.supplier_code,c.external_id,model) if model and classification=='NEW_CANDIDATE' else '',
            'provisional':c.supplier_code=='optimum','axis_execution_evidence':axis,'condition_execution_evidence':condition}
        legacy_audits.append({'source':c.supplier_code,'external_id':c.external_id,'matcher':m,'full_model_confirmed':full,
                              'decision':classification,'axis':axis,'condition':condition})
        rows.append(row)
    assert len(holds)==80
    learned=load(ROOT/'reports/RC_LOCAL/BEKA_CANONICAL_OFFLINE_2026-10-02/ACCEPTED/CONFIRMED_ONLY_CHARACTERISTIC_EVIDENCE.json')
    enriched,grouping,absence_audit=readiness_proposals(rows,learned,absence,absence_review_ref=f'saved://{COMMIT}/fresh59500',allowed_sources=NAMESPACES)
    groups=grouping['groups']
    # Every previously confirmed source is immutable without new contradiction.
    for r in enriched:
        prev=old.get((r['source'],str(r['external_id'])))
        if prev and prev['classification']=='EXISTING_CONFIRMED':assert r==prev
        if r['classification']=='NEW_CANDIDATE':r['classification']='REVIEW' # absence alone is not ready
    bysource={s:summary([r for r in enriched if r['source']==s]) for s in sorted(NAMESPACES)}
    holdcounts=dict(Counter(a['hold_verdict'] for a in holds))
    result={'by_source':bysource,'overall':summary(enriched),'source_counts':dict(Counter(r['source'] for r in enriched)),
            'retained_80_reassessment':holdcounts,'new_groups':len(groups),
            'cross_source_groups':sum(len({r['source'] for r in g['source_records']})>1 for g in groups),
            'prior_confirmed_preserved':True,'accepted_global_matcher_passes':1,
            'discarded_postprocessing_attempts':2,'sql_mutations_by_matching':0,
            'optimum_provisional':True,'final_exports_published':False,'enrichment_started':False}
    save('CONSOLIDATED_MATCHING.json',{'rows':enriched,'summary':result,'diagnostic_only':True})
    save('BEKA_RETAINED_HOLDS_AUDIT.json',holds);save('LEGACY_CANONICAL_AUDIT.json',legacy_audits)
    save('NEW_GROUP_PROPOSALS.json',groups);save('CANONICAL_ABSENCE_AUDIT.json',absence_audit)
    save('INTEGRATION_SUMMARY.json',result)
    print(json.dumps(result,ensure_ascii=True,indent=2))
if __name__=='__main__':main()
