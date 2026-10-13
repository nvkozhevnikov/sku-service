"""Bounded public surfaces; existing extractors, persistence and change/report engine."""
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from collections import deque
import json,time
from psycopg.types.json import Jsonb
from .store import Store,json_value
from .model import SOURCES,canonical
from .source_transport import SourceTransport
from .transport import SourceBlocked

class Repository:
    def __init__(self,config=None):
        self.store=Store(json.loads(Path(config).read_text(encoding='utf-8'))['dsn'] if config else None)
    def close(self):self.store.close()
    def enabled_sources(self):return {r['code'] for r in self.store.query('SELECT code FROM competitor_sources WHERE enabled=true')}
    def start(self,since,result):
        run=self.store.run_start(since)
        with self.store.conn.transaction(),self.store.conn.cursor() as c:c.execute('UPDATE competitor_runs SET result=%s WHERE id=%s',(Jsonb(json_value(result)),run))
        return run
    def finish(self,run,result):
        with self.store.conn.transaction(),self.store.conn.cursor() as c:c.execute('UPDATE competitor_runs SET status=%s,finished_at=now(),result=%s WHERE id=%s',(result['status'],Jsonb(json_value(result)),run))

def collect_source(repo,code,run,since,output,max_pages,deadline):
    if code in ('jettools_ru','rustan','belmash','harvey_rus','amur_stan'):
        from .five_source_runner import collect_baseline
        return collect_baseline(repo,code,run,since,output,max_pages,deadline)
    if code=='youmet':return {'status':'MANUAL_PENDING','reason':'OPERATOR_HTML_REQUIRED','HTTP_GETs':0,'stored':0}
    configs=json.loads(Path(__file__).with_name('new_sources.json').read_text(encoding='utf-8'))
    cfg=configs.get(code) or {**SOURCES[code],'hosts':[urlsplit(SOURCES[code]['base_url']).netloc]}
    h=SourceTransport(cfg,Path(output)/code/'captures')
    def remaining():
        left=deadline-time.monotonic()
        if left<=0:raise TimeoutError('MAX_RUNTIME')
        return left
    def sleep(seconds):
        if seconds>=remaining():raise TimeoutError('MAX_RUNTIME')
        time.sleep(seconds)
    h.sleep=sleep
    original_get=h.session.get
    def bounded_get(*args,**kwargs):
        bound=min(35,remaining()/2);kwargs['timeout']=(bound,bound)
        return original_get(*args,**kwargs)
    h.session.get=bounded_get
    stats={'status':'COMPLETED','stored':0,'pages':0,'versions':0,'items':0,'HTTP_GETs':0,'errors':[],
           'historical_seed_rows':0,'refresh_candidates':0,'refresh_deferred':0,'targeted_product_detail_GETs':0,'product_detail_GETs_avoided':0,'robots_policy':'INFORMATIONAL','full_catalog':False}
    # Start with stored, observed navigation, never manufacture new source paths.
    rows=repo.store.query('''SELECT sf.canonical_url url,sf.surface_kind kind FROM competitor_source_surfaces sf
      JOIN competitor_sources s ON s.id=sf.source_id WHERE s.code=%s ORDER BY sf.id''',(code,))
    for row in repo.store.query('''SELECT p.canonical_url url,p.page_type kind FROM competitor_pages p
      JOIN competitor_sources s ON s.id=p.source_id WHERE s.code=%s ORDER BY p.id''',(code,)):
        if row['url'] not in {x['url'] for x in rows}:rows.append(row)
    queue=deque([(cfg['base_url'],'HOMEPAGE_BLOCK')]);seen=set();scheduled={canonical(cfg['base_url'])}
    excluded={'PRODUCT','PRODUCT_DETAIL','CATEGORY','SITEMAP','SITEMAP_INDEX','ROBOTS'}
    # Listing surfaces first; catalog/product paths are never enqueued wholesale.
    rows.sort(key=lambda r:('INDEX' not in r['kind'] and r['kind']!='SALE_PAGE',r['url']))
    for row in rows:
        url=canonical(row['url'])
        if row['kind'] not in excluded and h.permitted(url) and url not in scheduled:
            queue.append((url,row['kind']));scheduled.add(url)
    lookup={}
    if code=='stanki_ru':
        lookup={r['payload']['product_url']:r['payload'] for r in repo.store.query('SELECT payload FROM competitor_historical_seeds WHERE is_live=false')}
        stats['historical_seed_rows']=len(lookup)
    campaign_products=set();avoided=set()
    try:
        try:h.prepare()
        except SourceBlocked:raise
        except Exception:stats['robots_evidence']='UNAVAILABLE';remaining()
        while queue and len(seen)<max_pages:
            remaining();url,kind=queue.popleft()
            if url in seen:continue
            seen.add(url)
            try:
                cap=h.get(url)
                if code in SOURCES:
                    if code=='kuvalda_nnov':
                        from .kuvalda_allowed import parse_allowed
                        page=parse_allowed(cap.final_url,cap.html,since,datetime.now(timezone.utc).date())
                    else:
                        from .parser import parse
                        page=parse(code,cap.final_url,cap.html,since,datetime.now(timezone.utc).date())
                else:
                    from .four_source_extract import extract
                    kind='NAVIGATION_INDEX' if kind.endswith('_INDEX') else kind
                    page=extract(code,cap,kind,since,datetime.now(timezone.utc).date())
                if 'BROWSER_RENDER_REQUIRED' in page.warnings:raise ValueError('BROWSER_RENDER_REQUIRED_NO_UNATTENDED_FALLBACK')
                if '\ufffd' in page.normalized_text or any(w in ('INVALID_OLD_NEW_PRICE','UNPARSED_PRICE') for item in page.items for w in item.warnings):raise ValueError('EXTRACTOR_QA_REJECTED')
                # Scope is deliberately incomplete: no fabricated disappearance events.
                page.item_scope_complete=False;page.source_discovery_complete=False
                if code=='stanki_ru':
                    page.historical_context={i.product_url:{k:lookup[i.product_url].get(k) for k in ('seed_id','product_name','brand','model','category','original_capture_time','evidence_class','is_live')} for i in page.items if i.product_url in lookup}
                    campaign_products.update(i.product_url for i in page.items if page.page_type in ('SALE_PAGE','PROMOTION','HOMEPAGE_BLOCK') or getattr(page,'campaign_evidence',{}).get('current_campaign_in_news'))
                    avoided.update(u for u in page.historical_context)
                delta=repo.store.save(page,cap,run)
                for k in ('pages','versions','items'):stats[k]+=delta[k]
                if page.inclusion_reason:
                    stats['stored']+=1
                    with repo.store.conn.transaction(),repo.store.conn.cursor() as c:
                        c.execute("UPDATE competitor_page_versions v SET qa_status='ACCEPTED' FROM competitor_pages p,competitor_sources s WHERE v.page_id=p.id AND p.source_id=s.id AND s.code=%s AND p.canonical_url=%s AND v.content_hash=%s",(code,page.canonical_url,page.content_hash()))
                with repo.store.conn.transaction(),repo.store.conn.cursor() as c:
                    c.execute('''INSERT INTO competitor_source_surfaces(source_id,canonical_url,surface_kind,title,status,robots_disallowed,fetch_status,evidence_class,reason,observed_at,raw_capture_ref,details)
                      SELECT id,%s,%s,%s,'LIVE_CRAWLED',%s,'PUBLIC_FETCH_OK','LIVE_CRAWLED','NORMAL_PUBLIC_HTTP_RESPONSE',%s,%s,%s FROM competitor_sources WHERE code=%s
                      ON CONFLICT(source_id,canonical_url) DO UPDATE SET status=excluded.status,robots_disallowed=excluded.robots_disallowed,fetch_status=excluded.fetch_status,reason=excluded.reason,observed_at=excluded.observed_at,raw_capture_ref=excluded.raw_capture_ref''',
                      (page.canonical_url,kind,page.title,h.robots_disallowed(url),cap.fetched_at,cap.raw_ref,Jsonb({'run_id':run,'scope_complete':False}),code))
                if code in SOURCES:
                    from .parser import discover
                    found=[(r['url'],'PUBLIC_INDEX') for r in discover(code,url,cap.html,since,datetime.now(timezone.utc).date()) if not urlsplit(r['url']).query]
                else:found=[]
                if code=='jet_online' and '/special_offers/' in url:
                    from bs4 import BeautifulSoup
                    found.extend((canonical(a['href'],url),'SALE_PAGE') for a in BeautifulSoup(cap.html,'html.parser').select('.pagination a[href]'))
                for target,target_kind in found:
                    target=canonical(target)
                    if h.permitted(target) and target not in scheduled:queue.append((target,target_kind));scheduled.add(target)
            except Exception as error:
                reason=type(error).__name__;stats['errors'].append({'url':url,'reason':reason})
                with repo.store.conn.transaction(),repo.store.conn.cursor() as c:
                    c.execute("UPDATE competitor_source_surfaces sf SET status='ERROR',fetch_status=%s,reason='ACTUAL_ACCESS_FAILURE',observed_at=now() FROM competitor_sources s WHERE sf.source_id=s.id AND s.code=%s AND sf.canonical_url=%s",('HTTP_BLOCKED' if isinstance(error,SourceBlocked) else 'ERROR',code,url))
                if isinstance(error,(SourceBlocked,TimeoutError)):break
        stats['not_fetched_due_to_bound']=len(queue)
        if queue or stats['errors']:stats['status']='PARTIAL' if stats['stored'] else 'FAILED';stats['reason']='BOUNDED_OR_ACCESS_INCOMPLETE'
    finally:
        stats['HTTP_GETs']=len(h.events);stats['refresh_candidates']=len(campaign_products);stats['refresh_deferred']=len(campaign_products);stats['product_detail_GETs_avoided']=len(avoided)
        h.session.close()
    return stats

def rebuild_report(repo,output,config):
    from .business_report import preview_from_store
    from zoneinfo import ZoneInfo
    return preview_from_store(repo.store,datetime.fromisoformat(config['since']).date(),datetime.now(ZoneInfo(config['timezone'])).date(),output)
