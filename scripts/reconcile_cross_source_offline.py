"""Reconcile pinned external evidence with latest proposals, never SQL or HTTP."""
from collections import defaultdict, Counter
from copy import deepcopy
from pathlib import Path
import csv, hashlib, json, re, subprocess, sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.resolve_reviews_offline import load,save,sha,EVIDENCE
from universal_supplier.offline_review_resolution import resolve_existing,clean_observed_model
from universal_supplier.intervesp_final_proposals import readiness_proposals,final_counts
from universal_supplier.characteristic_evidence import build_evidence,equipment_scope
from scripts.build_characteristic_evidence import section_evidence
from sterbrust_matching.normalization import normalize_brand,normalize_model,extract_model
from universal_supplier.proposal_export import new_candidate_id
from universal_supplier.adapters.bekamak import parse_bekamak_detail
from universal_supplier.adapters.bekamak_official import parse_bekamak_official_detail
HEAD='cdd6d9a5c70d8a6b0b3697b61164243472e6b072'
BASE=ROOT/'reports/RC_LOCAL/OFFLINE_REVIEW_COMPLETE_2026-10-01'
OUT=ROOT/'reports/RC_LOCAL/CROSS_SOURCE_RECONCILED_2026-10-01'

def external(name):
    path='full_discovery/evidence/cross_source/'+name
    raw=subprocess.check_output(['git','show',HEAD+':'+path],cwd=ROOT)
    return json.loads(raw),hashlib.sha256(raw).hexdigest()

def identity(r): return r['source'],str(r['external_id'])

def url_numeric_model_contradiction(url, model):
    """Only explicit same-family differing numeric model, not absent suffixes."""
    from urllib.parse import urlsplit
    token=re.search(r'\b(bmsy|bmso|bmdo)[-_]?(\d+)',urlsplit(url).path.replace('_','-'),re.I)
    parsed=re.match(r'(bmsy|bmso|bmdo)(\d+)',normalize_model(model),re.I)
    return bool(token and parsed and token[1].lower()==parsed[1].lower() and token[2]!=parsed[2])

def empty_hold(value):
    if value in (None,'','{}',{}): return True
    if isinstance(value,dict) and set(value)<= {'prior_source_review_not_auto_cleared','prior_conflicts'}:
        return empty_hold(value.get('prior_conflicts'))
    return False

def reconsiderable_automatic_hold(value):
    """Only old retrieval uncertainty, never factual execution/semantic conflict."""
    if empty_hold(value): return True
    if isinstance(value,str):
        try: value=json.loads(value)
        except ValueError: return False
    if isinstance(value,dict) and set(value)<= {'prior_source_review_not_auto_cleared','prior_conflicts'}:
        return reconsiderable_automatic_hold(value.get('prior_conflicts'))
    return value=={'semantic_identity':['name_compatibility_below_brand_model_threshold']}

def evidence_view(row):
    """Explicit bilingual semantics from own description; never copy peer context."""
    r=deepcopy(row); translations={'Main Drive Motor':'Мощность двигателя',
        'Band Dimensions':'Размеры ленточного полотна','Blade Dimensions':'Размеры ленточного полотна',
        'Cutting Speeds':'Скорость резки','Blade Speed':'Скорость резки'}
    description=r.get('enrichment',{}).get('description_text','')
    own_band=bool(re.search(r'\bband\s*saws?\b',description,re.I))
    if r['source']=='beka_mak_tr' and own_band:
        r['source_category']=r.get('source_category','')+' | ленточнопильный станок'
        r['product_kind']='machine'
        r['context_translation_evidence']={'source_description':description,'basis':'own explicit bandsaw text, not peer model'}
        r['observed_properties']=[{**p,'raw_name':p['name'],'name':translations.get(p['name'],p['name'])}
            for p in r.get('observed_properties',[])]
        r['properties']={p['name']:p['value'] for p in r['observed_properties']}
    return r

def run():
    if OUT.exists(): raise ValueError('Preserve prior output')
    v=load(BASE/'VERIFIED.json')
    for n,s in v['output_sha256'].items():
        if sha(BASE/n)!=s: raise ValueError('Latest proposal changed: '+n)
    bundle,bsha=external('CROSS_SOURCE_RESOLUTION.json'); gaps,gsha=external('BEKA_RC_GAPS.json')
    matching=load(BASE/'MATCHING_OFFLINE_RESOLVED.json'); rows=deepcopy(matching['rows'])
    old={identity(r):deepcopy(r) for r in rows}; current={identity(r):r for r in rows}
    ids={str(r['sterbrust_product_id']) for r in rows if r['classification']=='EXISTING_CONFIRMED'}
    canonical={}; h=hashlib.sha256(); seen=set()
    with (ROOT/'reports/STERBRUST_REGISTRY.jsonl').open('rb') as f:
        for line in f:
            h.update(line); item=json.loads(line); pid=str(item['sterbrust_product_id']); seen.add(pid)
            if pid not in ids: continue
            props=[{'name':p['property_name'],'value':p['value_flat'],'unit':p.get('unit','')} for p in item.get('properties',[])
                   if p.get('property_name') and p.get('value_flat') not in (None,'','N')]
            canonical[pid]={'sterbrust_product_id':pid,'name':item['name'],'brand':item.get('brand_raw',''),
                'model':item.get('model_raw',''),'active':item.get('active'),'source_category':item.get('category_path',''),
                'observed_properties':props,'properties':{p['name']:p['value'] for p in props},
                'category_ids':item.get('category_ids') or [item.get('category_id')]}
    if len(seen)!=59500 or h.hexdigest()!=v['input_sha256']['registry_jsonl']: raise ValueError('Registry changed')
    index=defaultdict(set)
    if sha(ROOT/'reports/STERBRUST_REGISTRY.csv')!=matching['registry_sha256']: raise ValueError('CSV changed')
    with (ROOT/'reports/STERBRUST_REGISTRY.csv').open(encoding='utf-8-sig',newline='') as f:
        for r in csv.DictReader(f):
            if r.get('active')!='Y': continue
            for m in (r.get('model_raw'),extract_model(r.get('name'))):
                clean=clean_observed_model({'model':m,'name':r.get('name'),'brand':r.get('brand_raw')})['model']
                index[(normalize_brand(r.get('brand_raw')),normalize_model(clean))].add(str(r['sterbrust_product_id']))
    for t in canonical.values():
        clean=clean_observed_model(t)['model']
        t['equivalent_identity_ids']=sorted(index[(normalize_brand(t['brand']),normalize_model(clean))])
    audits=[]; promotions=[]; views={}; inputs=load(BASE/'SCOPED_CNC_MAPPINGS.json')
    for g in bundle['strong_groups']:
        members=[current.get((p['source'],str(p['external_id']))) for p in g['records']]
        if any(r is None for r in members):
            audits.append({'group_key':g['group_key'],'blocking_reasons':['SOURCE_RECORD_NOT_IN_CURRENT_RESULT']}); continue
        existing=[r for r in members if r['classification']=='EXISTING_CONFIRMED']
        if any(r['classification']=='CONFLICT' for r in members):
            audits.append({'group_key':g['group_key'],'blocking_reasons':['CURRENT_CONFLICT_IMMUTABLE']}); continue
        if not existing: continue
        for row in members:
            if row['classification']=='EXISTING_CONFIRMED': continue
            pid=str(existing[0]['sterbrust_product_id']); view=evidence_view(row)
            # Synthetic eligibility only requests the pure guard evaluation;
            # inherited holds and contradictions are not cleared by STRONG.
            view['match_status']='HIGH_CONFIDENCE_MATCH'
            result=resolve_existing(view,canonical.get(pid),inputs)
            result['current_existing_anchor']={'source':existing[0]['source'],'external_id':existing[0]['external_id'],'sterbrust_product_id':pid}
            audits.append({'group_key':g['group_key'],'source':row['source'],'external_id':row['external_id'],**result})
            views[identity(row)]=view
            if result['confirmed']:
                row.update(classification='EXISTING_CONFIRMED',sterbrust_product_id=pid,
                    sterbrust_name=canonical[pid]['name'],full_model_confirmed=True,
                    cross_source_resolution=result,readiness={'state':'EXISTING_CONFIRMED','identity_ready':False})
                promotions.append(identity(row))
    learned=load(BASE/'UPDATED_SCOPED_EVIDENCE.json'); pairs=load(BASE/'UPDATED_CONFIRMED_PAIR_EVIDENCE.json')
    sections={str(s['id']):s for s in load(ROOT/'reports/rest/STERBRUST_SECTIONS_RAW.json')}
    for key in promotions:
        r={**views[key],**current[key]}; t=canonical[str(r['sterbrust_product_id'])]
        pairs.append({**r,'equipment_type':equipment_scope(r['name'],r['source_category']),
            'source_properties':r['observed_properties'],'sterbrust_properties':t['observed_properties'],
            'sections':section_evidence(t['category_ids'],sections)})
    if promotions: learned=build_evidence(pairs)
    new_original=[r for r in rows if r.get('match_status')=='NEW_CANDIDATE']
    assert len(new_original)==892
    # Only new-gate rows plus explicit two NEW group counterparts; retain holds.
    targets={identity(r) for r in new_original}
    for g in bundle['strong_groups']:
        if g['new_group_eligible']: targets.update((r['source'],str(r['external_id'])) for r in g['records'])
    candidates=[]
    for key in targets:
        if key not in current: continue
        r=evidence_view(current[key]); r['cross_source_evidence_head']=HEAD
        if r.get('match_status')=='NEW_CANDIDATE' and not r.get('offline_precheck_reasons'):
            r['classification']='NEW_CANDIDATE'
        candidates.append(r)
    absence=load(EVIDENCE/'CANONICAL_ABSENCE_INDEX.json')
    if sha(EVIDENCE/'CANONICAL_ABSENCE_INDEX.json')!=v['input_sha256']['absence_index']: raise ValueError('Absence index changed')
    enriched,grouping,absence_audit=readiness_proposals(candidates,learned,absence,absence_review_ref='saved://pinned_59500_cross_source_recheck')
    for r in enriched:
        if old[identity(r)]['classification'] not in ('EXISTING_CONFIRMED','CONFLICT'): current[identity(r)].update(r)
    new_audits=[]; baseline={identity(a):a for a in load(BASE/'NEW_GATE_AUDIT.json')}
    section_closed=absence_closed=0
    for r in enriched:
        if identity(r) not in baseline: continue
        before=baseline[identity(r)]; after=r['readiness_advisory']
        section_closed+=('NEW_NEEDS_SECTION' in before['blocking_reasons'] and after['SECTION_EVIDENCE']['verified'])
        absence_closed+=('NEW_NEEDS_ABSENCE_PROOF' in before['blocking_reasons'] and after['CANONICAL_ABSENCE_SUPPORT']['verified'])
        new_audits.append({'source':r['source'],'external_id':r['external_id'],'readiness':r['readiness'],'advisory':after})
    group_audits=[]
    for g in bundle['strong_groups']:
        if not g['new_group_eligible']: continue
        members=[current[(p['source'],str(p['external_id']))] for p in g['records']]
        group_audits.append({'models':g['models'],'member_ids':g['member_ids'],
            'identity_ready':all(r.get('readiness',{}).get('identity_ready') for r in members),
            'members':[{'source':r['source'],'external_id':r['external_id'],'readiness':r.get('readiness'),
                'grouping_reasons':r.get('grouping_reasons'),'gates':r.get('readiness_advisory')} for r in members]})
    # Capture inventory: local saved bytes only, no imports of external code.
    captures=defaultdict(list)
    for root in (ROOT/'reports/RC_LOCAL',ROOT/'reports/STAGE5D_2026-09-25',ROOT.parent/'sku-service-full-discovery-rc1/full_discovery/evidence'):
        for path in root.rglob('*.html'):
            try: captures[sha(path)].append(path)
            except OSError: continue
    gap_audits=[]; ingest=[]; gets=[]; humans=[]
    for source,info in gaps['suppliers'].items():
        for gap in info['missing_from_rc']:
            a={**gap,'blocking_reasons':[]}; digest=(gap.get('evidence_ref') or '').removeprefix('sha256:')
            paths=captures.get(digest,[])
            if gap['detail_capture']!='AVAILABLE' or not paths:
                a['blocking_reasons']=['SAVED_DETAIL_MISSING']; gets.append({**a,'get_purpose':'close own title/model/internal identity evidence'})
            elif gap.get('article_status')=='CONFLICT':
                a['blocking_reasons']=['SAVED_ARTICLE_IDENTITY_CONTRADICTION']; humans.append(a)
            else:
                try:
                    html=paths[0].read_text(encoding='utf-8'); parser=parse_bekamak_official_detail if source=='beka_mak_tr' else parse_bekamak_detail
                    p=parser(html,source_url=gap['url'])
                    if not p.site_internal_id or not p.supplier_model or not p.name: raise ValueError('missing source identity')
                    if normalize_model(p.supplier_model)!=normalize_model(extract_model(p.name)): raise ValueError('own title/full execution contradiction')
                    if normalize_model(gap.get('model'))!=normalize_model(p.supplier_model): raise ValueError('expanded detail/parser model contradiction')
                    key=(source,str(p.site_internal_id))
                    if key in current: raise ValueError('URL alias of existing source identity; not a new row')
                    # Known URL/H1 model contradiction never auto-ingested.
                    from urllib.parse import urlsplit
                    tokens=re.findall(r'(?:bmsy|bmso|bmdo)[-_]?\d+(?:[-_][a-z0-9]+)*',urlsplit(gap['url']).path,re.I)
                    if tokens and normalize_model(tokens[-1])!=normalize_model(p.supplier_model): raise ValueError('URL/full model execution contradiction')
                    record={'source':source,'external_id':p.site_internal_id,'source_url':gap['url'],'name':p.name,
                        'model':p.supplier_model,'manufacturer_article':p.manufacturer_article,
                        'source_category':p.source_category,'price_state':p.price.state.value,'price':p.price.current_price,
                        'currency':p.price.currency,'availability':p.availability,'evidence_ref':str(paths[0]),'capture_sha256':digest,
                        'diagnostic_only':True,'canonical_identity_unconfirmed':True,'supplier_disabled':True,'offer_inactive':True}
                    ingest.append(record); a['offline_ingest_ready']=True
                except (ValueError,TypeError) as e:
                    a['blocking_reasons']=[str(e)]; humans.append(a)
            gap_audits.append(a)
    final=list(current.values())
    for key,r in old.items():
        if r['classification'] in ('EXISTING_CONFIRMED','CONFLICT'): assert current[key]==r
        if r.get('new_candidate_id'): assert current[key].get('new_candidate_id')==r['new_candidate_id']
    assert len(final)==len(current)==1789
    OUT.mkdir()
    for n,value in {'MATCHING_CROSS_SOURCE_RESOLVED.json':{**matching,'rows':final},'EXISTING_EQUIVALENCE_AUDIT.json':audits,
        'NEW_892_GATE_AUDIT.json':new_audits,'NEW_GROUP_GATE_AUDIT.json':group_audits,'ABSENCE_RECHECK.json':absence_audit,
        'BEKA_GAPS_AUDIT.json':gap_audits,'BEKA_OFFLINE_INGEST_ROWS.json':ingest,'MINIMAL_GET_PLAN.json':gets,
        'HUMAN_REVIEW_REQUIRED.json':humans,'UPDATED_SCOPED_EVIDENCE.json':learned}.items(): save(OUT/n,value)
    result={'DONE':True,'APPLIED':'isolated proposal/evidence files only','VERIFIED':'pinned current decisions and registry, unchanged Existing/Conflict',
        'counts':final_counts(final),'intervesp_counts':final_counts([r for r in final if r['source']=='intervesp']),
        'identity_ready':sum(bool(r.get('readiness',{}).get('identity_ready')) for r in final),
        'beka_reviews_resolved':len(promotions),'strong_existing_links_checked':sum('source' in a for a in audits),
        'section_blockers_closed':section_closed,'absence_blockers_closed':absence_closed,
        'offline_ingest_rows':len(ingest),'minimal_http_gets':len(gets),'gap_human_reviews':len(humans),
        'new_gate_rows_checked':len(new_audits),'http_performed':0,'db_accesses':0,
        'input_sha256':{'current_result':sha(BASE/'MATCHING_OFFLINE_RESOLVED.json'),'grok_resolution':bsha,'grok_gaps':gsha,'registry':h.hexdigest()},
        'external_evidence_head':HEAD,'output_sha256':{p.name:sha(p) for p in OUT.iterdir()}}
    save(OUT/'VERIFIED.json',result); print(json.dumps(result,ensure_ascii=True))

if __name__=='__main__': run()
