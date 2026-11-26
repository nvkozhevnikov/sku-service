"""Correct trial extraction from exact retained captures, zero source HTTP.

Correction is a separate local observation with OFFLINE_REEXTRACTION transport;
timestamps come from the original capture, never masquerade as fresh live reads.
"""
from datetime import date,datetime
import json
import argparse
import hashlib
from pathlib import Path
from urllib.parse import urlsplit
from competitor_intel.model import SOURCES
from competitor_intel.parser import parse,discover
from competitor_intel.transport import Capture
from competitor_intel.store import Store

out=Path('reports/COMPETITOR_INTELLIGENCE_2026-10-09')
argparser=argparse.ArgumentParser(); argparser.add_argument('--source-run',type=int,required=True); args=argparser.parse_args()
saved=json.loads((out/f'CRAWL_RUN_{args.source_run}.json').read_text(encoding='utf-8'))
since=date(2026,9,1); today=date(2026,10,9)
store=Store(json.loads(Path('competitor-data/dev-config.json').read_text())['dsn'])
run=store.run_start(since)
result={'run_id':run,'mode':'OFFLINE_REEXTRACTION','source_run':args.source_run,'source_HTTP':0,'errors':saved['errors'],'corrections':[],'sources':saved['sources'],'source_map':saved['source_map']}
proofs={}
caps={}
for event in saved['requests']:
    if event.get('status')!=200 or not event.get('raw_capture_ref') or event['url'].endswith('/robots.txt'): continue
    source=next((s for s,cfg in SOURCES.items() if urlsplit(cfg['base_url']).netloc==urlsplit(event['url']).netloc),None)
    if not source: continue
    ref=Path(event['raw_capture_ref'])
    cap=Capture(event['url'],event['url'],ref.read_text(encoding='utf-8'),str(ref),event['raw_hash'],datetime.fromisoformat(event['observed_at']),'OFFLINE_REEXTRACTION')
    caps[(source,event['url'])]=cap
    for link in discover(source,event['url'],cap.html,since,today):
        if link['published_at']: proofs.setdefault(link['url'],{'published_at':link['published_at'],'listing_url':event['url'],'raw_capture_ref':str(ref)})
for (source,url),cap in caps.items():
    manifest=Path(cap.raw_ref.replace('.browser.html','.manifest.json'))
    if cap.raw_ref.endswith('.browser.html') and manifest.exists():
        bundle=json.loads(manifest.read_text(encoding='utf-8'))
        cap.raw_ref=str(manifest)
        cap.raw_hash=hashlib.sha256(manifest.read_bytes()).hexdigest()
        for tab in bundle['tabs']:
            ref=Path(tab['ref']); cap.variants.append(Capture(url,url,ref.read_text(encoding='utf-8'),str(ref),tab['hash'],datetime.fromisoformat(tab['observed_at']),'OFFLINE_REEXTRACTION'))
    page=parse(source,url,cap.html,since,today,url,proofs.get(url))
    for variant in cap.variants:
        tab_page=parse(source,url,variant.html,since,today,url)
        for item in tab_page.items:
            if item.campaign_name!='Готовимся к зиме': continue
            if (item.product_url,item.campaign_name,item.tab_title) not in {(i.product_url,i.campaign_name,i.tab_title) for i in page.items}: page.items.append(item)
    delta=store.save(page,cap,run)
    result['corrections'].append({'source':source,'url':url,'delta':delta,'items':len(page.items),'published_at':page.published_at,'valid_from':page.valid_from,'valid_to':page.valid_to})
result['rows_by_source']=store.counts()
store.run_finish(run,result)
(out/'EXTRACTION_CORRECTION.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
print(json.dumps(result['corrections'],ensure_ascii=False,default=str))
store.close()
