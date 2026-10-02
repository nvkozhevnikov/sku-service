"""Compact generated diagnostics and derive a deduplicated, unexecuted plan."""
from pathlib import Path
from collections import Counter,defaultdict
import sys,json,hashlib,gzip
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.close_unresolved_saved_evidence import OUT,PRE,load,save
from universal_supplier.offline_canonical_closure import source_support_inventory

def main():
    assert not (OUT/'CHECKPOINT.json').exists(),'Already finalized; resume checkpoint'
    rows=load(OUT/'MATCHING_AFTER.json')['rows'];byid={(r['source'],str(r['external_id'])):r for r in rows}
    previous={(r['source'],str(r['external_id'])):r for r in load(PRE/'MATCHING_ACCEPTED.json')['rows']}
    audits=load(OUT/'STRICT_EVALUATIONS.json');plans=load(OUT/'GROK_TARGETED_EVIDENCE_PLAN.json')
    byplan={(r['source'],r['external_id']):r for r in plans}
    routes=Counter();requests={};no_exact=[];refined=[];hold=[]
    for audit in audits:
        key=audit['source'],audit['external_id'];r=byid[key];plan=byplan.get(key,{})
        inventory=source_support_inventory(r)
        verdict=audit['verdict']
        if r.get('historical_hold_resolution',{}).get('hold_verdict')=='FACTUAL_OR_UNPARSED_BLOCKER_REQUIRES_REVIEW':
            # Preserved historical execution hold is not a newly proven visible
            # source conflict. A human requirement is not invented.
            hold.append({'source':key[0],'external_id':key[1],'status':'RETAINED_EXECUTION_BLOCKER',
                'human_required_proven':False,'evidence':r['historical_hold_resolution']})
        if verdict.startswith('TARGETED_') and not inventory['independent_source_anchors_present']:
            verdict='TARGETED_SUPPLIER_DETAIL_REQUIRED'
        urls=plan.get('exact_urls',[]) if verdict=='TARGETED_STERBRUST_PAGE_EVIDENCE_REQUIRED' else [r['source_url']]
        if verdict=='TARGETED_STERBRUST_PAGE_EVIDENCE_REQUIRED':
            # A wrong fuzzy/family candidate page cannot prove absence of the
            # requested model. Use exact-page URLs only for an exact retrieval.
            if not audit['retrieval']['exact_full_model_ids']:
                urls=[]
                no_exact.append({'source':key[0],'external_id':key[1],'brand':r.get('brand'),'full_model':r.get('model'),
                    'source_url':r['source_url'],'blocking_reasons':audit['blocking_reasons'],
                    'required':'Targeted canonical search/identity evidence; no fabricated page URL',
                    'requested_queries':[f"{r.get('brand','')} {r.get('model','')}"],
                    'absence_not_inferred':True,'execute_http':False})
        routes[verdict]+=1
        refined.append({**audit,'verdict':verdict,'source_support_inventory':inventory,
                        'exact_urls':sorted(set(urls)),'source_url':r['source_url']})
        for url in sorted(set(urls)):
            if not verdict.startswith('TARGETED_'):continue
            identity=('sterbrust' if 'STERBRUST' in verdict else r['source']),url
            req=requests.setdefault(identity,{'supplier':identity[0],'url':url,'verdict':verdict,
                'source_records':[],'blocking_reasons':set(),'required_evidence':set(),
                'execute_http':False,'fresh_get_is_proposal_only':True})
            req['source_records'].append({'source':key[0],'external_id':key[1]})
            req['blocking_reasons'].update(audit['blocking_reasons'])
            req['required_evidence'].update(['visible own full model/execution','visible article/manufacturer','own typed characteristic table',
                'product kind/accessory/condition/axis context'])
    request_rows=[]
    for identity,req in sorted(requests.items()):
        req['blocking_reasons']=sorted(req['blocking_reasons']);req['required_evidence']=sorted(req['required_evidence'])
        request_rows.append(req)
    section_closed=sum(bool((r.get('readiness_advisory') or {}).get('SECTION_EVIDENCE',{}).get('verified')) and
        not bool((previous[(r['source'],str(r['external_id']))].get('readiness_advisory') or {}).get('SECTION_EVIDENCE',{}).get('verified'))
        for r in rows if r['classification']=='REVIEW')
    # Add a verified compressed copy; preserve the original raw trace untouched.
    # Compact review evidence is a NEW artifact, never an in-place replacement.
    raw=OUT/'NEW_GROUPS.json';archive=OUT/'NEW_GROUPS_RAW.json.gz';h=hashlib.sha256()
    if not archive.exists():
        with raw.open('rb') as source,archive.open('xb') as target,gzip.GzipFile(fileobj=target,mode='wb',mtime=0) as zipped:
            for block in iter(lambda:source.read(1024*1024),b''):h.update(block);zipped.write(block)
        rawsha=h.hexdigest()
        with gzip.open(archive,'rb') as source:
            check=hashlib.sha256()
            for block in iter(lambda:source.read(1024*1024),b''):check.update(block)
        assert check.hexdigest()==rawsha
        proof={'raw_sha256':rawsha,'raw_size':raw.stat().st_size,'compressed_path':archive.name,
            'compressed_sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),'compressed_size':archive.stat().st_size,
            'recoverable_exact_bytes':True}
        save('GROUP_TRACE_ARCHIVE_VERIFIED.json',proof)
    else:proof=load(OUT/'GROUP_TRACE_ARCHIVE_VERIFIED.json')
    assert load(OUT/'SUMMARY.json')['new_groups']==0
    compact={'groups':[],'confirmed_groups':0,'archive_proof':proof,'original_raw_trace_preserved':True,
        'review':[{'source':r['source'],'external_id':str(r['external_id']),
                  'grouping_reasons':r.get('grouping_reasons') or [],'new_group_id':r.get('new_group_id')}
                 for r in rows if r['classification']=='REVIEW']}
    save('NEW_GROUPS_COMPACT.json',compact)
    save('FINAL_OUTCOMES.json',refined);save('GROK_MINIMAL_EXACT_URL_PLAN.json',request_rows)
    save('CANONICAL_SEARCH_NO_KNOWN_PAGE.json',no_exact);save('RETAINED_EXECUTION_EVIDENCE.json',hold)
    result={**load(OUT/'SUMMARY.json'),'outcome_routes':dict(routes),
        'canonical_exact_url_requests':sum(r['supplier']=='sterbrust' for r in request_rows),
        'supplier_detail_exact_url_requests':sum(r['supplier']!='sterbrust' for r in request_rows),
        'canonical_search_no_known_page_rows':len(no_exact),'new_section_gates_closed':section_closed,
        'all_original_routes_offline_closed':{'canonical_side_evidence':0,'missing_identity_section_absence_evidence':0,
                                            'saved_source_identity_quarantine':0,'retained_execution_blocker':0},
        'retained_execution_rows':len(hold),'group_trace_compact_copy_created':True,'original_group_trace_preserved':True}
    save('FINAL_SUMMARY.json',result);print(json.dumps(result),flush=True)
if __name__=='__main__':main()
