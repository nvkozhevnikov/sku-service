from collections import deque
from datetime import date
import json
from pathlib import Path
from urllib.parse import urlsplit
from .model import SOURCES, canonical
from .parser import parse, discover
from .transport import HttpTransport, BrowserTransport

def crawl(store, sources, since, today, output, max_pages=45, browser=False):
    output = Path(output); output.mkdir(parents=True,exist_ok=True)
    http = HttpTransport(output/'captures')
    run_id = store.run_start(since)
    result = {'run_id':run_id,'since':str(since),'through':str(today),'ai_provider':'none','ai_network_calls':0,
              'sources':{},'errors':[],'source_map':[],'requests':http.events,'transport_policy':'single worker; fresh random 3–7s between HTTP requests; stable UA; no bypass'}
    def checkpoint():
        (output/f'CRAWL_RUN_{run_id}.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    try:
        for source in sources:
            config = SOURCES[source]
            queue = deque(canonical(path,config['base_url']) for path in config['seeds'])
            scheduled = set(queue); processed = set()
            publication_proofs = {}
            counts = {'discovered':len(queue),'fetched':0,'stored_observations':0,'new_pages':0,'new_versions':0,'new_items':0,'excluded':0,'blocked':0,'errors':0,'warnings':[]}
            result['sources'][source] = counts
            while queue and len(processed)<max_pages:
                url = queue.popleft()
                if url in processed: continue
                processed.add(url)
                entry = {'source':source,'url':url,'page_type':None,'selected':True,'why':'Operator seed or bounded same-source navigation','crawl_strategy':'exact source host; no catalog product fetch','date_support':None,'product_price_support':None}
                result['source_map'].append(entry)
                try:
                    cap = http.get(url)
                    page = parse(source,cap.final_url,cap.html,since,today,url,publication_proofs.get(url))
                    if browser and 'BROWSER_RENDER_REQUIRED' in page.warnings:
                        cap = BrowserTransport(http).get(url)
                        page = parse(source,cap.final_url,cap.html,since,today,url,publication_proofs.get(url))
                        for variant in cap.variants:
                            tab_page = parse(source,variant.final_url,variant.html,since,today,url)
                            for item in tab_page.items:
                                if item.campaign_name != 'Готовимся к зиме': continue
                                key=(item.product_url,item.campaign_name,item.tab_title)
                                if key not in {(i.product_url,i.campaign_name,i.tab_title) for i in page.items}: page.items.append(item)
                    counts['fetched'] += 1
                    entry.update(page_type=page.page_type,date_support=bool(page.published_at or page.valid_from or page.valid_to),product_price_support=bool(page.items),transport=cap.transport,raw_capture_ref=cap.raw_ref)
                    delta = store.save(page,cap,run_id)
                    counts['excluded'] += delta['excluded']; counts['new_pages'] += delta['pages']; counts['new_versions'] += delta['versions']; counts['new_items'] += delta['items']
                    if page.inclusion_reason: counts['stored_observations'] += 1
                    for warning in page.warnings:
                        counts['warnings'].append({'url':url,'warning':warning})
                    for found in discover(source,url,cap.html,since,today):
                        target = found['url']
                        if found['published_at']:
                            publication_proofs.setdefault(target,{'published_at':found['published_at'],'listing_url':url,'raw_capture_ref':cap.raw_ref})
                        # ONE first listing page; no unbounded news archive/pagination.
                        if urlsplit(target).query: continue
                        if target not in scheduled:
                            scheduled.add(target); queue.append(target); counts['discovered'] += 1
                except Exception as e:
                    from .transport import SourceBlocked
                    counts['errors'] += 1
                    if isinstance(e,SourceBlocked): counts['blocked'] += 1
                    result['errors'].append({'source':source,'url':url,'type':type(e).__name__,'reason':str(e) if isinstance(e,SourceBlocked) else type(e).__name__})
                    entry['error'] = result['errors'][-1]['reason']
                print(json.dumps({'run':run_id,'source':source,'processed':len(processed),'queued':len(queue),'counts':counts},ensure_ascii=False),flush=True)
                checkpoint()
            counts['not_fetched_due_to_bound'] = len(queue)
            if queue: counts['warnings'].append({'warning':'PAGE_BOUND_REACHED','remaining':len(queue)})
        result['rows_by_source'] = store.counts()
        store.run_finish(run_id,result); checkpoint()
        (output/'CRAWL_RESULT.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
        return result
    except BaseException:
        result['errors'].append({'type':'Interrupted','reason':'Check partial observations before resuming'})
        store.run_finish(run_id,result); checkpoint(); raise
