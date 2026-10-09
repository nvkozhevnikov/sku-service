"""Exact saved Kuvalda captures only. No source HTTP, no MetalMaster rows."""
from datetime import datetime,date
import hashlib
import json
from pathlib import Path
from competitor_intel.store import Store
from competitor_intel.transport import Capture
from competitor_intel.kuvalda_allowed import parse_allowed
from competitor_intel.kuvalda_public_runner import finish_public_run

out=Path('reports/KUVALDA_ALLOWED_SOURCE_EXTENSION_2026-10-09')
store=Store(json.loads(Path('competitor-data/dev-config.json').read_text())['dsn'])
rows=store.query("""SELECT p.canonical_url,v.raw_capture_ref,v.fetched_at,v.payload,v.extractor_version
 FROM competitor_pages p JOIN competitor_page_versions v ON v.page_id=p.id AND v.content_hash=p.current_content_hash
 JOIN competitor_sources s ON s.id=p.source_id WHERE s.code='kuvalda_nnov' ORDER BY p.id""")
source=json.loads((out/'CRAWL_RESULT.json').read_text(encoding='utf-8'))
result={**source,'mode':'OFFLINE_REEXTRACTION','source_run':source['run_id'],'source_HTTP':0,'reparse_deltas':[]}
run=store.run_start(date(2026,9,1)); result['run_id']=run
try:
    for row in rows:
        ref=Path(row['raw_capture_ref']); variants=[]
        if ref.suffix=='.json':
            bundle=json.loads(ref.read_text(encoding='utf-8')); html=Path(bundle['initial']).read_text(encoding='utf-8')
            variants=bundle['tabs']
        else: html=ref.read_text(encoding='utf-8')
        cap=Capture(row['canonical_url'],row['canonical_url'],html,str(ref),hashlib.sha256(ref.read_bytes()).hexdigest(),row['fetched_at'],'OFFLINE_REEXTRACTION')
        p=parse_allowed(row['canonical_url'],html,date(2026,9,1),date(2026,10,9),row['payload'].get('campaign_evidence'))
        for tab in variants:
            tab_page=parse_allowed(row['canonical_url'],Path(tab['ref']).read_text(encoding='utf-8'),date(2026,9,1),date(2026,10,9))
            for i in tab_page.items:
                if i.campaign_name!='Готовимся к зиме': continue
                if (i.product_url,i.campaign_name,i.tab_title) not in {(j.product_url,j.campaign_name,j.tab_title) for j in p.items}: p.items.append(i)
        result['reparse_deltas'].append({'url':row['canonical_url'],'delta':store.save(p,cap,run)})
    finish_public_run(store,run,result)
    (out/f'CRAWL_RUN_{run}.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    (out/'REPARSE_RESULT.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    print(json.dumps({'run_id':run,'source_HTTP':0,'pages':len(rows)},ensure_ascii=False))
except BaseException:
    result['errors'].append({'reason':'OFFLINE_REPARSE_INTERRUPTED'}); store.run_finish(run,result); raise
finally: store.close()
