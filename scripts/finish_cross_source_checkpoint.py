"""Complete saved reconciliation checkpoint; do not re-run Grok classification."""
from scripts.reconcile_cross_source_offline import *

PRE=OUT
DEST=ROOT/'reports/RC_LOCAL/CROSS_SOURCE_FINAL_VERIFIED_2026-10-01'

def complete():
    if DEST.exists(): raise ValueError('Preserve completed checkpoint')
    proof=load(PRE/'VERIFIED.json')
    for n,s in proof['output_sha256'].items():
        if sha(PRE/n)!=s: raise ValueError('Checkpoint artifact changed')
    matching=load(PRE/'MATCHING_CROSS_SOURCE_RESOLVED.json'); rows=deepcopy(matching['rows'])
    current={identity(r):r for r in rows}; old=deepcopy(current)
    audit=load(PRE/'EXISTING_EQUIVALENCE_AUDIT.json')
    needed={a['current_existing_anchor']['sterbrust_product_id'] for a in audit if a.get('current_existing_anchor')}
    targets={}; h=hashlib.sha256(); seen=set()
    with (ROOT/'reports/STERBRUST_REGISTRY.jsonl').open('rb') as f:
        for line in f:
            h.update(line); item=json.loads(line); pid=str(item['sterbrust_product_id']); seen.add(pid)
            if pid not in needed: continue
            p=[{'name':x['property_name'],'value':x['value_flat'],'unit':x.get('unit','')} for x in item['properties']
               if x.get('property_name') and x.get('value_flat') not in (None,'','N')]
            targets[pid]={'sterbrust_product_id':pid,'name':item['name'],'model':item.get('model_raw',''),
                'brand':item.get('brand_raw',''),'active':item.get('active'),'source_category':item.get('category_path',''),
                'observed_properties':p,'properties':{x['name']:x['value'] for x in p},
                'category_ids':item.get('category_ids') or [item.get('category_id')]}
    assert h.hexdigest()==proof['input_sha256']['registry'] and len(seen)==59500
    # The uniqueness outcome was already verified against active registry;
    # preserve it from the checkpoint, never infer unique from the model alone.
    index=defaultdict(set)
    with (ROOT/'reports/STERBRUST_REGISTRY.csv').open(encoding='utf-8-sig',newline='') as f:
        for x in csv.DictReader(f):
            if x.get('active')!='Y': continue
            for m in (x.get('model_raw'),extract_model(x.get('name'))):
                clean=clean_observed_model({'model':m,'brand':x.get('brand_raw'),'name':x['name']})['model']
                index[(normalize_brand(x.get('brand_raw')),normalize_model(clean))].add(str(x['sterbrust_product_id']))
    for t in targets.values(): t['equivalent_identity_ids']=sorted(index[(normalize_brand(t['brand']),normalize_model(clean_observed_model(t)['model']))])
    decisions=[]; promotions=[]; inputs=load(BASE/'SCOPED_CNC_MAPPINGS.json')
    for a in audit:
        if not a.get('current_existing_anchor'):
            decisions.append(a); continue
        r=current[identity(a)]; view=evidence_view(r); view['match_status']='HIGH_CONFIDENCE_MATCH'
        # Only the empty historical automatic wrapper can be reconsidered on
        # new independent evidence. Real conflicts/manual holds remain intact.
        empty_automatic=(r.get('prior_review_status')=='REVIEW' and reconsiderable_automatic_hold(r.get('prior_review_evidence'))
                         and reconsiderable_automatic_hold(r.get('match_conflicts')))
        if empty_automatic:
            view['prior_review_retained']=False; view['match_conflicts']={}
        pid=str(a['current_existing_anchor']['sterbrust_product_id'])
        result=resolve_existing(view,targets[pid],inputs)
        result['reconsidered_empty_automatic_review_wrapper']=empty_automatic
        decisions.append({'source':r['source'],'external_id':r['external_id'],
            'current_existing_anchor':a['current_existing_anchor'],**result})
        if result['confirmed']:
            r.update(classification='EXISTING_CONFIRMED',sterbrust_product_id=pid,sterbrust_name=targets[pid]['name'],
                full_model_confirmed=True,cross_source_resolution=result,readiness={'state':'EXISTING_CONFIRMED','identity_ready':False})
            promotions.append(identity(r))
    learned=load(PRE/'UPDATED_SCOPED_EVIDENCE.json'); pairs=load(BASE/'UPDATED_CONFIRMED_PAIR_EVIDENCE.json')
    sections={str(s['id']):s for s in load(ROOT/'reports/rest/STERBRUST_SECTIONS_RAW.json')}
    for key in promotions:
        r=evidence_view(current[key]); t=targets[str(r['sterbrust_product_id'])]
        # Learn under original source category, not translated diagnostic context.
        pairs.append({**r,'source_category':old[key]['source_category'],'equipment_type':equipment_scope(r['name'],r['source_category']),
            'source_properties':r['observed_properties'],'sterbrust_properties':t['observed_properties'],
            'sections':section_evidence(t['category_ids'],sections)})
    if promotions: learned=build_evidence(pairs)
    # Keep original full 892 audit as checkpoint. Re-evaluate gates with the
    # changed evidence only; never re-run canonical retrieval/classification.
    newkeys={identity(r) for r in rows if r.get('match_status')=='NEW_CANDIDATE'}; assert len(newkeys)==892
    group_audits=load(PRE/'NEW_GROUP_GATE_AUDIT.json'); additional=set()
    for g in group_audits:
        additional.update(identity(m) for m in g['members'])
    proposed=[]
    for key in newkeys|additional:
        r=evidence_view(current[key])
        if key in newkeys and not r.get('offline_precheck_reasons'): r['classification']='NEW_CANDIDATE'
        # Technical candidate identity is safe to propose, never Product ID.
        if key in additional and not r.get('new_candidate_id'):
            r['new_candidate_id']=new_candidate_id(r['source'],r['external_id'],r['model'])
        proposed.append(r)
    absence=load(EVIDENCE/'CANONICAL_ABSENCE_INDEX.json')
    assert sha(EVIDENCE/'CANONICAL_ABSENCE_INDEX.json')==load(BASE/'VERIFIED.json')['input_sha256']['absence_index']
    enriched,groups,absences=readiness_proposals(proposed,learned,absence,absence_review_ref='saved://checkpoint_pinned_59500_absence')
    for r in enriched:
        if old[identity(r)]['classification'] not in ('EXISTING_CONFIRMED','CONFLICT'): current[identity(r)].update(r)
    finalgroups=[]
    for g in group_audits:
        finalgroups.append({'models':g['models'],'members':[{'source':m['source'],'external_id':m['external_id'],
            'readiness':current[identity(m)]['readiness'],'advisory':current[identity(m)]['readiness_advisory'],
            'grouping_reasons':current[identity(m)].get('grouping_reasons',[])} for m in g['members']]})
    baseline={identity(r):r for r in load(BASE/'NEW_GATE_AUDIT.json')}
    section_closed=[]; absence_closed=[]
    for r in enriched:
        key=identity(r)
        if key not in baseline: continue
        a=r['readiness_advisory']; b=baseline[key]['blocking_reasons']
        if 'NEW_NEEDS_SECTION' in b and a['SECTION_EVIDENCE']['verified']: section_closed.append(key)
        if 'NEW_NEEDS_ABSENCE_PROOF' in b and a['CANONICAL_ABSENCE_SUPPORT']['verified']: absence_closed.append(key)
    # Expand local digest lookup to Grok's decoded text SHA (LF normalization),
    # while preserving actual byte SHA in each ingest proposal.
    gap_audits=load(PRE/'BEKA_GAPS_AUDIT.json'); wanted={a.get('evidence_ref','').removeprefix('sha256:') for a in gap_audits if a.get('evidence_ref')}
    captures={}
    for root in (ROOT/'reports/RC_LOCAL',ROOT/'reports/STAGE5D_2026-09-25'):
        for p in root.rglob('*.html'):
            try:
                for digest in (sha(p),hashlib.sha256(p.read_text(encoding='utf-8').encode('utf-8')).hexdigest()):
                    if digest in wanted: captures[digest]=p
            except (OSError,UnicodeError): continue
    ingest=[]; gets=[]; human=[]; gap_final=[]
    for a in gap_audits:
        a={**a,'blocking_reasons':[]}; path=captures.get((a.get('evidence_ref') or '').removeprefix('sha256:'))
        if a['article_status']=='CONFLICT':
            a['blocking_reasons']=['SAVED_TITLE_SKU_ARTICLE_CONTRADICTION']; human.append(a)
        elif a['detail_capture']!='AVAILABLE' or not path:
            a['blocking_reasons']=['SAVED_DETAIL_MISSING']; gets.append({**a,'get_purpose':'verify own full model and stable source identity'})
        else:
            try:
                parser=parse_bekamak_official_detail if a['source']=='beka_mak_tr' else parse_bekamak_detail
                p=parser(path.read_text(encoding='utf-8'),source_url=a['url'])
                if not p.site_internal_id or not p.supplier_model: raise ValueError('missing stable source identity/full model')
                if normalize_model(p.supplier_model)!=normalize_model(extract_model(p.name)): raise ValueError('OWN_TITLE_ARTICLE_EXECUTION_CONTRADICTION')
                if normalize_model(a['model'])!=normalize_model(p.supplier_model): raise ValueError('EXPANDED_DETAIL_PARSER_CONTRADICTION')
                if identity({'source':a['source'],'external_id':p.site_internal_id}) in current: raise ValueError('EXISTING_SOURCE_IDENTITY_ALIAS')
                # URL/title disagreement was already quarantined in the saved
                # pipeline. Do not silently resolve a known contradictory URL.
                if url_numeric_model_contradiction(a['url'],p.supplier_model):
                    raise ValueError('SAVED_URL_TITLE_MODEL_CONTRADICTION')
                ingest.append({'source':a['source'],'external_id':p.site_internal_id,'source_url':a['url'],
                    'name':p.name,'model':p.supplier_model,'manufacturer_article':p.manufacturer_article,
                    'source_category':p.source_category,'price_state':p.price.state.value,'price':p.price.current_price,
                    'currency':p.price.currency,'availability':p.availability,'capture_path':str(path),
                    'capture_sha256':sha(path),'grok_text_sha256':a['evidence_ref'],'diagnostic_only':True,
                    'supplier_disabled':True,'offer_inactive':True,'canonical_identity_unconfirmed':True})
                a['offline_ingest_ready']=True
            except (ValueError,TypeError) as e: a['blocking_reasons']=[str(e)]; human.append(a)
        gap_final.append(a)
    assert len(gap_final)==42 and len(ingest)+len(gets)+len(human)==42
    final=list(current.values())
    for key,r in old.items():
        if r['classification'] in ('EXISTING_CONFIRMED','CONFLICT'): assert current[key]==r
        if r.get('new_candidate_id'): assert current[key].get('new_candidate_id')==r['new_candidate_id']
    DEST.mkdir()
    for n,value in {'MATCHING_CROSS_SOURCE_RESOLVED.json':{**matching,'rows':final},'EXISTING_CANONICAL_VERIFICATION.json':decisions,
        'NEW_GROUP_GATE_AUDIT.json':finalgroups,'NEW_892_GATE_AUDIT.json':[r for r in enriched if identity(r) in newkeys],
        'ABSENCE_RECHECK.json':absences,'SECTION_BLOCKERS_CLOSED.json':section_closed,'ABSENCE_BLOCKERS_CLOSED.json':absence_closed,
        'BEKA_GAPS_AUDIT.json':gap_final,'BEKA_OFFLINE_INGEST_ROWS.json':ingest,'MINIMAL_GET_PLAN.json':gets,
        'HUMAN_GAP_REVIEW.json':human,'UPDATED_SCOPED_EVIDENCE.json':learned}.items(): save(DEST/n,value)
    summary={**{k:v for k,v in proof.items() if k not in ('output_sha256',)},'counts':final_counts(final),
        'intervesp_counts':final_counts([r for r in final if r['source']=='intervesp']),
        'identity_ready':sum(bool(r.get('readiness',{}).get('identity_ready')) for r in final),
        'beka_reviews_resolved':len(promotions),'section_blockers_closed':len(section_closed),'absence_blockers_closed':len(absence_closed),
        'offline_ingest_rows':len(ingest),'minimal_http_gets':len(gets),'gap_human_reviews':len(human),
        'checkpoint_resumed':str(PRE),'no_grok_reclassification':True,
        'output_sha256':{p.name:sha(p) for p in DEST.iterdir()}}
    save(DEST/'VERIFIED.json',summary); print(json.dumps({k:v for k,v in summary.items() if k!='output_sha256'},ensure_ascii=True))

if __name__=='__main__': complete()
