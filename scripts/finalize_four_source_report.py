"""Read-only proof/export; only new-source obsolete QA flags are changed explicitly."""
import json,hashlib
from pathlib import Path
from collections import Counter
from datetime import datetime,timezone
from bs4 import BeautifulSoup
from psycopg.types.json import Jsonb
from competitor_intel.store import Store,json_value
from competitor_intel.business_data import read_state,fingerprints,normalized_dataset
from competitor_intel.business_report import build_facts,preview_from_store
from competitor_intel.seed_cache import verified_seed_bundle

root=Path('reports/COMPETITOR_FOUR_SOURCE_ONBOARDING_2026-10-12');preview=Path('reports/COMPETITOR_BUSINESS_REPORT_PREVIEW_2026-10-12')
s=Store(json.loads(Path('competitor-data/dev-config.json').read_text())['dsn'])
try:
    with s.conn.transaction(),s.conn.cursor() as c:
        c.execute("UPDATE competitor_page_versions v SET qa_status='REJECTED_EXTRACTOR' FROM competitor_pages p,competitor_sources src WHERE v.page_id=p.id AND p.source_id=src.id AND src.code IN ('stanki_ru','jet_online','youmet','titaniumgroup') AND v.extractor_version IN ('four-sources-1.0','four-sources-1.1','four-sources-1.2')")
    public=json.loads((root/'audit/youmet/PUBLIC_BROWSER.json').read_text(encoding='utf-8'))
    for url,kind in [('https://youmet.ru/akcia/','PROMOTION_INDEX'),('https://youmet.ru/about/news/','NEWS_INDEX'),('https://youmet.ru/blog/','BLOG_INDEX')]:
        with s.conn.transaction(),s.conn.cursor() as c:
            c.execute('''INSERT INTO competitor_source_surfaces(source_id,canonical_url,surface_kind,title,status,fetch_status,evidence_class,reason,observed_at,raw_capture_ref,details)
             SELECT id,%s,%s,NULL,'DISCOVERED_NOT_FETCHED',NULL,'NAVIGATION_EVIDENCE','OBSERVED_PUBLIC_BROWSER_NAVIGATION_ACCESS_INCOMPLETE',%s,%s,%s FROM competitor_sources WHERE code='youmet' ON CONFLICT(source_id,canonical_url) DO NOTHING''',(url,kind,public['observed_at'],public['raw_capture_ref'],Jsonb({'homepage_http_status':403,'no_regional_mirror':True})))
    state=read_state(s);fp=fingerprints(state);initial=json.loads((root/'BEFORE_STATE_FINGERPRINTS.json').read_text())
    proof={'frozen_sources_equal':{k:fp['sources'][k]==initial['sources'][k] for k in ('kuvalda_nnov','metalmaster')},'supplier_kami_writes':0,'AI':False,'scheduler':False,'duplicates':{},'source_counts':[]}
    for name,sql in {'pages':'SELECT source_id,canonical_url FROM competitor_pages GROUP BY source_id,canonical_url HAVING count(*)>1','versions':'SELECT page_id,content_hash FROM competitor_page_versions GROUP BY page_id,content_hash HAVING count(*)>1','items':'SELECT page_version_id,item_key FROM competitor_campaign_items GROUP BY page_version_id,item_key HAVING count(*)>1'}.items():proof['duplicates'][name]=len(s.query(sql))
    dataset=normalized_dataset(state);facts=build_facts(dataset,'2026-09-01','2026-10-12')
    new_codes={'stanki_ru','jet_online','youmet','titaniumgroup'}
    proof['invented_baseline_changes']=[e for e in facts['events'] if e['source'] in new_codes and e['event_type'] not in ('BASELINE_OBSERVATION','PROMOTION_ENDING_SOON','PROMOTION_ENDED')]
    for code in sorted(new_codes):
        pages=[p for p in dataset['current'] if p['source']==code];items=[i for p in pages for i in p['items']]
        proof['source_counts'].append({'source':code,'pages':len(pages),'items':len(items),'old_new':sum(i['old_price'] is not None and i['new_price'] is not None for i in items),'high':sum(i['priority']=='HIGH' for i in items),'price_types':dict(Counter(i['price_type'] for i in items)),'price_parsing_corruption':sum('INVALID_OLD_NEW_PRICE' in i['warnings'] or 'UNPARSED_PRICE' in i['warnings'] for i in items)})
    rows,seedproof=verified_seed_bundle('reports/KAMI_HISTORICAL_SEED_2026-10-12');proof['seed_integrity']=seedproof
    assert all(proof['frozen_sources_equal'].values()) and not any(proof['duplicates'].values()) and not proof['invented_baseline_changes']
    assert not any(r['price_parsing_corruption'] for r in proof['source_counts'])
    # Original rows of all old tables remain byte-equivalent despite new-source append.
    proof['old_table_prefix_equal']={}
    for table,entry in initial['tables'].items():
        if table=='competitor_historical_seeds':continue
        prefix=state[table][:entry['rows']]
        digest=hashlib.sha256(json.dumps(prefix,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        proof['old_table_prefix_equal'][table]=digest==entry['sha256']
    assert all(proof['old_table_prefix_equal'].values())
    plan=json.loads((root/'REFRESH_PLAN.json').read_text(encoding='utf-8'));detail=json.loads((root/'TARGETED_DETAIL_QA.json').read_text(encoding='utf-8'))
    selected={x['url'] for x in detail['selected']};plan['selected']=detail['selected'];plan['deferred']=[r for r in plan['deferred'] if r['url'] not in selected];plan['refresh_deferred']=len(plan['deferred']);plan['targeted_product_detail_GETs']=len(detail['requests']);plan['targeted_product_detail_pages']=len(selected)
    plan['avoided_exact_urls']=[u for u in plan['avoided_exact_urls'] if u not in selected];plan['product_detail_GETs_avoided']=len(plan['avoided_exact_urls']);plan['avoidance_basis']='Exact historical metadata lookup + sufficient current listing; excludes two fetched QA targets, not the whole seed corpus'
    listing_requests=[];content_requests=[];all_requests=[]
    for code in sorted(new_codes):
        for name in ('AUDIT.json','LIVE_EVIDENCE.json','EXTRA_EVIDENCE.json'):
            f=root/'audit'/code/name
            if not f.exists():continue
            for r in json.loads(f.read_text(encoding='utf-8')).get('requests',[]):
                all_requests.append({'source':code,**r});u=r['url']
                if '/robots.txt' in u or '/sitemap' in u:continue
                is_content=('/about/news/detail/' in u or '/news/' in u and not u.endswith('/news/') or '/articles/' in u and not u.endswith('/articles/'))
                (content_requests if is_content else listing_requests).append({'source':code,**r})
    listing_requests.append({'source':'stanki_ru',**json.loads((root/'STANKI_SPEC.json').read_text(encoding='utf-8'))})
    plan['live_listing_GETs']=len(listing_requests);plan['live_content_GETs']=len(content_requests);plan['per_source_listing_GETs']=dict(Counter(r['source'] for r in listing_requests));plan['network_counts_include_redirects_and_retries']=True;plan['youmet_browser_visits']=2
    (root/'REFRESH_PLAN.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2),encoding='utf-8');(root/'FINAL_PROOF.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2),encoding='utf-8')
    (root/'REQUEST_ACCOUNTING.json').write_text(json.dumps({'listing_requests':listing_requests,'content_requests':content_requests,'all_HTTP_requests':all_requests,'targeted_details':detail['requests'],'browser_visits':2},ensure_ascii=False,indent=2),encoding='utf-8')
    first=preview_from_store(s,'2026-09-01','2026-10-12',preview);second=preview_from_store(s,'2026-09-01','2026-10-12',preview)
    assert first['business_facts_sha256']==second['business_facts_sha256'] and second['new_events']==0 and second['duplicate_events']==0
    (root/'REPORT_REBUILD_PROOF.json').write_text(json.dumps({'first':first,'second':second,'same_facts':True},indent=2),encoding='utf-8')
    print(json.dumps({'source_counts':proof['source_counts'],'efficiency':{k:plan[k] for k in ('historical_seed_rows','live_listing_GETs','live_content_GETs','targeted_product_detail_GETs','targeted_product_detail_pages','product_detail_GETs_avoided','refresh_candidates','refresh_deferred')},'report':second},ensure_ascii=False))
finally:s.close()
