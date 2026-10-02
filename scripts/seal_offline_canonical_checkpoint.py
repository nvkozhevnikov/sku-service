"""Seal separate proposals; derive new-GET exclusions from saved captures."""
from pathlib import Path
from collections import Counter
import sys,json,hashlib
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.close_unresolved_saved_evidence import OUT,PRE,load,save

def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()

def main():
    assert not (OUT/'CHECKPOINT.json').exists(),'Already sealed; use checkpoint'
    plans=load(OUT/'GROK_MINIMAL_EXACT_URL_PLAN.json')
    audits=load(OUT/'FINAL_OUTCOMES.json');byid={(r['source'],r['external_id']):r for r in audits}
    saved={};grok=ROOT.parent/'sku-service-fresh-catalog-refresh-rc1'
    for supplier in ('partner_st','optimum'):
        for line in (grok/f'catalog_refresh/evidence/{supplier}/CAPTURE_INDEX.jsonl').read_text(encoding='utf-8').splitlines():
            r=json.loads(line);saved[supplier,r['canonical_url']]=r
    need=[];reuse=[]
    for p in plans:
        cap=saved.get((p['supplier'],p['url']))
        near=any(any(len(e['blocking_reasons'])<=2 for e in byid[(r['source'],r['external_id'])]['evaluations']) for r in p['source_records'])
        p={**p,'priority':'NEAR_COMPLETE_STRICT_IDENTITY' if near else 'ADDITIONAL_EVIDENCE_NOT_A_CRAWL_ORDER'}
        if cap:
            reuse.append({**p,'saved_sha256':cap['sha256'],'saved_capture_file':cap['file'],
                'fresh_get_justified':False,'next_action':'Saved capture available; reused selectors yielded no additional acceptable typed proof. Seek missing independent evidence/documents, not automatic refetch.'})
        else:need.append(p)
    # Existing raw Intervesp evidence references are present in many proposals;
    # do not claim those URLs have never been fetched. New GET is only proposed
    # if it can address the attached remaining reason, not guaranteed sufficient.
    save('GROK_NO_DUPLICATE_GET_PLAN.json',need)
    save('SAVED_SUPPLIER_CAPTURE_NO_REFETCH.json',reuse)
    save('GROK_PRIORITY_NEAR_COMPLETE.json',[p for p in need if p['priority']=='NEAR_COMPLETE_STRICT_IDENTITY'])
    summary=load(OUT/'FINAL_SUMMARY.json')
    summary.update(supplier_new_detail_url_proposals=sum(p['supplier']!='sterbrust' for p in need),
        canonical_page_url_proposals=sum(p['supplier']=='sterbrust' for p in need),
        saved_capture_urls_excluded_from_fresh_get=len(reuse),
        priority_near_complete_unique_urls=sum(p['priority']=='NEAR_COMPLETE_STRICT_IDENTITY' for p in need),
        new_source_properties_from_saved_partner_optimum=0,
        actual_closed_decisions=0,original_454_existing_preserved=True,original_259_conflicts_preserved=True)
    save('VERIFIED_SUMMARY.json',summary)
    previous=load(PRE/'MATCHING_ACCEPTED.json');current=load(OUT/'MATCHING_AFTER.json')
    old={(r['source'],str(r['external_id'])):r for r in previous['rows']}
    assert len(old)==len(current['rows'])==4407
    for r in current['rows']:
        key=r['source'],str(r['external_id']);before=old[key]
        if before['classification'] in ('EXISTING_CONFIRMED','CONFLICT'):assert r==before
        for k in ('name','model','execution','external_id','source_url'):assert r.get(k)==before.get(k)
    archive=load(OUT/'GROUP_TRACE_ARCHIVE_VERIFIED.json')
    assert sha(OUT/'NEW_GROUPS.json')==archive['raw_sha256']
    assert sha(OUT/archive['compressed_path'])==archive['compressed_sha256']
    save('CHECKPOINT.json',{'status':'OFFLINE_CANONICAL_EXPANSION_COMPLETE_EXTERNAL_EVIDENCE_REQUIRED',
        'resume_from':'reports/RC_LOCAL/OFFLINE_CANONICAL_EXPANSION_2026-10-02/CHECKPOINT.json',
        'predecessor':str(PRE/'CHECKPOINT.json'),'authoritative_matching':'MATCHING_AFTER.json',
        'authoritative_outcomes':'FINAL_OUTCOMES.json','summary':'VERIFIED_SUMMARY.json',
        'grok_exact_urls':'GROK_NO_DUPLICATE_GET_PLAN.json','grok_first_priority':'GROK_PRIORITY_NEAR_COMPLETE.json',
        'canonical_no_known_page_queries':'CANONICAL_SEARCH_NO_KNOWN_PAGE.json',
        'overall':summary['overall'],'http':0,'sql':0,'global_matcher_repeated':False,
        'source_ingestion_repeated':False,'beka_audits_repeated':False,'optimum_audit_repeated':False,
        'new_section_gates_closed':54,'new_existing':0,'new_ready':0,'human_required_proven':0,
        'completed_do_not_repeat':['3694-row reason clustering','saved canonical description/structured property expansion',
            '454-Existing-only learning','strict exact candidate proof evaluation','cross-source grouping/readiness',
            'saved Partner/Optimum source capture reuse check'],
        'next_action':'Review targeted Grok evidence plan; acquire only evidence that addresses attached blockers. No automatic crawl permission.',
        'final_xml_xlsx_published':False,'original_group_trace_preserved':True,'hashes':'ARTIFACT_SHA256.json',
        'limitations':['0 complete independent typed NEW identities','Exact model absence alone is not NEW',
                       'source-capture selectors extracted no additional acceptable new properties',
                       '21 canonical cases have no verified page URL; do not fabricate one',
                       '137 prior tests plus final focused additions; see TESTS_VERIFIED.json']})
    hashes={p.relative_to(OUT).as_posix():sha(p) for p in OUT.rglob('*') if p.is_file() and p.name!='ARTIFACT_SHA256.json'}
    save('ARTIFACT_SHA256.json',hashes)
    assert all(sha(OUT/p)==value for p,value in hashes.items())
    print(json.dumps(summary),flush=True)
if __name__=='__main__':main()
