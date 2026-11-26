"""One observed public navigation URL, no recursive expansion or MetalMaster writes."""
import argparse
from datetime import date
import json
from pathlib import Path
from competitor_intel.store import Store
from competitor_intel.kuvalda_allowed import KuvaldaHttp,parse_allowed
from competitor_intel.kuvalda_public_runner import save_surface,POLICY,finish_public_run
from competitor_intel.transport import BrowserTransport

p=argparse.ArgumentParser(); p.add_argument('--url',required=True); a=p.parse_args()
out=Path('reports/KUVALDA_ALLOWED_SOURCE_EXTENSION_2026-10-09')
store=Store(json.loads(Path('competitor-data/dev-config.json').read_text())['dsn'])
prior=store.query("SELECT t.* FROM competitor_source_surfaces t JOIN competitor_sources s ON s.id=t.source_id WHERE s.code='kuvalda_nnov' AND t.canonical_url=%s",(a.url,))
if not prior: raise SystemExit('URL not proven by allowed source navigation; no fetch')
previous=store.query('SELECT result FROM competitor_runs WHERE finished_at IS NOT NULL ORDER BY id DESC LIMIT 1')[0]['result']
h=KuvaldaHttp(out/'captures'); h.prepare(); run=store.run_start(date(2026,9,1))
result={**previous,'run_id':run,'source_run':previous['run_id'],'mode':'ADDITIONAL_DISCOVERED_SURFACE','additional_requests':h.events}
try:
    cap=h.get(a.url); page=parse_allowed(cap.final_url,cap.html,date(2026,9,1),date(2026,10,9)); fetch='PUBLIC_FETCH_OK'
    if 'BROWSER_RENDER_REQUIRED' in page.warnings:
        cap=BrowserTransport(h,robots_informational=True).get(a.url); page=parse_allowed(cap.final_url,cap.html,date(2026,9,1),date(2026,10,9)); fetch='BROWSER_FETCH_OK'
    delta=store.save(page,cap,run)
    entry={'url':a.url,'kind':'SALE_CATEGORY','status':'LIVE_CRAWLED','fetch_status':fetch,'robots_disallowed':not h.robots['nnov.kuvalda.ru'].decision(a.url)[0],
           'robots_rule':h.robots['nnov.kuvalda.ru'].decision(a.url)[1],'policy':POLICY,'evidence_class':'LIVE_CRAWLED','raw_capture_ref':cap.raw_ref,
           'observed_at':cap.fetched_at.isoformat(),'title':page.title,'page_type':page.page_type,'items':len(page.items),'reason':'OBSERVED_INDUSTRIAL_NAVIGATION_SINGLE_PAGE'}
    save_surface(store,entry)
    result['source_map']=[e for e in result['source_map'] if e['url']!=a.url]+[entry]
    result['additional_delta']=delta
    finish_public_run(store,run,result)
    (out/f'CRAWL_RUN_{run}.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    (out/'CRAWL_RESULT.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    print(json.dumps({'run_id':run,'url':a.url,'items':len(page.items),'delta':delta},ensure_ascii=False))
except BaseException:
    result['errors'].append({'url':a.url,'reason':'ADDITIONAL_SURFACE_INTERRUPTED'}); store.run_finish(run,result); raise
finally: store.close()
