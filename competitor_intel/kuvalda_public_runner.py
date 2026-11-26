"""Scoped public Kuvalda crawl under operator-corrected informational robots policy."""
from collections import deque
from datetime import date,datetime,timezone
import json
import re
from pathlib import Path
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET
import requests
from psycopg.types.json import Jsonb
from .model import canonical
from .parser import discover
from .transport import BrowserTransport,SourceBlocked
from .kuvalda_allowed import BASE,HOST,KuvaldaHttp,navigation,parse_allowed,blocks

POLICY='OPERATOR_CONFIRMED_2026_10_09_ROBOTS_INFORMATIONAL_PUBLIC_ONLY'
SALE_PARENTS={'Станки','Деревообрабатывающие станки','Металлообрабатывающие станки','Сварочное оборудование','Воздушные компрессоры','Автосервисное оборудование и инструмент','Промышленное оборудование','Электродвигатели общепромышленные стандарта ГОСТ'}

def public_run_status(result):
    sitemap_urls={e['url'] for e in result.get('source_map',[]) if e.get('kind')=='SITEMAP'}
    blocking=bool(result.get('queue_remaining')) or any(e.get('url') not in sitemap_urls for e in result.get('errors',[]))
    return 'BLOCKED' if blocking else 'DONE_WITH_WARNINGS' if result.get('errors') else 'DONE'

def finish_public_run(store,run_id,result):
    state=public_run_status(result)
    result['run_status']=state
    store.run_finish(run_id,result)
    with store.conn.transaction(),store.conn.cursor() as c:
        c.execute("UPDATE competitor_runs SET status=%s WHERE id=%s AND result->>'source'='kuvalda_nnov' AND result->>'policy'=%s",(state,run_id,POLICY))

def init_surfaces(store):
    with store.conn.transaction(),store.conn.cursor() as c:
        c.execute(Path(__file__).with_name('kuvalda_surfaces.sql').read_text(encoding='utf-8'))

def save_surface(store,entry):
    with store.conn.transaction(),store.conn.cursor() as c:
        c.execute('''INSERT INTO competitor_source_surfaces(source_id,canonical_url,surface_kind,title,status,robots_disallowed,fetch_status,evidence_class,reason,observed_at,raw_capture_ref,details)
                     SELECT id,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s FROM competitor_sources WHERE code='kuvalda_nnov'
                     ON CONFLICT(source_id,canonical_url) DO UPDATE SET surface_kind=excluded.surface_kind,title=excluded.title,status=excluded.status,
                     robots_disallowed=excluded.robots_disallowed,fetch_status=excluded.fetch_status,evidence_class=excluded.evidence_class,reason=excluded.reason,
                     observed_at=excluded.observed_at,raw_capture_ref=excluded.raw_capture_ref,details=excluded.details''',
                  (entry['url'],entry.get('kind','PAGE'),entry.get('title') or entry.get('label'),entry['status'],entry.get('robots_disallowed'),entry.get('fetch_status'),
                   entry['evidence_class'],entry.get('reason','PUBLIC_SOURCE'),entry.get('observed_at',datetime.now(timezone.utc)),entry.get('raw_capture_ref'),Jsonb(json.loads(json.dumps(entry,default=str,ensure_ascii=False)))))

def crawl_public(store,output,since=date(2026,9,1),today=date(2026,10,9),max_pages=35,product_samples=2,category_samples=2):
    out=Path(output); out.mkdir(parents=True,exist_ok=True)
    h=KuvaldaHttp(out/'captures'); robots=h.prepare(); policy=h.robots[HOST]
    init_surfaces(store); run=store.run_start(since)
    queue=deque([(BASE,'HOMEPAGE'),(BASE+'promo/','PROMOTION_INDEX'),(BASE+'news/','NEWS_INDEX'),(BASE+'sale/','SALE_INDEX'),(BASE+'sitemap.xml','SITEMAP')])
    for candidate in policy.sitemaps[:2]:
        if h.permitted(candidate): queue.append((candidate,'SITEMAP'))
    scheduled={u for u,_ in queue}; processed=set(); proofs={}; sample_counts={'PRODUCT':0,'CATEGORY':0}
    result={'run_id':run,'policy':POLICY,'since':str(since),'through':str(today),'source':'kuvalda_nnov','robots_capture_ref':robots.raw_ref,
            'sources':{'kuvalda_nnov':{}},'errors':[],'source_map':[],'requests':h.events,'blocks':[],'resource_gaps':h.resource_gaps,'tab_gaps':h.tab_gaps,'rows_delta':{'pages':0,'versions':0,'items':0},'stored_observations':0}
    def checkpoint(): (out/f'CRAWL_RUN_{run}.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    def schedule(url,kind):
        if url not in scheduled: scheduled.add(url); queue.append((url,kind))
    try:
        while queue and len(processed)<max_pages:
            url,kind=queue.popleft()
            if url in processed: continue
            processed.add(url)
            entry={'url':url,'kind':kind,'status':'DISCOVERED_NOT_FETCHED','fetch_status':None,'evidence_class':'NAVIGATION_EVIDENCE',
                   'robots_disallowed':not policy.decision(url)[0],'robots_rule':policy.decision(url)[1],'policy':POLICY}
            result['source_map'].append(entry)
            try:
                cap=h.get(url)
                entry.update(status='LIVE_CRAWLED',fetch_status='PUBLIC_FETCH_OK',evidence_class='LIVE_CRAWLED',raw_capture_ref=cap.raw_ref,observed_at=cap.fetched_at.isoformat(),reason='NORMAL_PUBLIC_HTTP_RESPONSE')
                if kind=='SITEMAP':
                    if len(cap.html)>8_000_000: raise ValueError('SITEMAP_SIZE_BOUND')
                    if '<!DOCTYPE' in cap.html.upper() or '<!ENTITY' in cap.html.upper(): raise ValueError('SITEMAP_ENTITY_DECLARATION')
                    root=ET.fromstring(cap.html)
                    locs=[el.text.strip() for el in root.iter() if el.tag.split('}')[-1]=='loc' and el.text]
                    entry['sitemap_entries']=len(locs); entry['title']='Public sitemap audit'; entry['selected_entries']=[]
                    # No catalog sitemap expansion/full catalog crawl.
                    for loc in locs[:200]:
                        if not h.permitted(loc): continue
                        target=canonical(loc)
                        if urlsplit(target).path in ('/sale/','/promo/','/news/'):
                            entry['selected_entries'].append(target); schedule(target,'PUBLIC_INDEX')
                    entry['discovery_bound']='first200loc; no child catalog sitemap traversal'
                else:
                    page=parse_allowed(cap.final_url,cap.html,since,today,proofs.get(url))
                    # Browser-accessibility probe is mandatory for /news/; promo
                    # listing probe also proves the newly authorized DOM path.
                    if urlsplit(url).path in ('/news/','/promo/') or 'BROWSER_RENDER_REQUIRED' in page.warnings:
                        cap=BrowserTransport(h,robots_informational=True).get(url)
                        page=parse_allowed(cap.final_url,cap.html,since,today,proofs.get(url))
                        for variant in cap.variants:
                            tab_page=parse_allowed(variant.final_url,variant.html,since,today)
                            for item in tab_page.items:
                                if item.campaign_name!='Готовимся к зиме': continue
                                if (item.product_url,item.campaign_name,item.tab_title) not in {(i.product_url,i.campaign_name,i.tab_title) for i in page.items}: page.items.append(item)
                        entry.update(fetch_status='BROWSER_FETCH_OK',reason='ORDINARY_PUBLIC_EDGE_RENDER',raw_capture_ref=cap.raw_ref,observed_at=cap.fetched_at.isoformat())
                    if urlsplit(url).path.startswith(('/promo/','/news/')):
                        entry['access_class']='PUBLIC_BROWSER_ACCESSIBLE' if entry['fetch_status']=='BROWSER_FETCH_OK' else 'PUBLIC_HTTP_ACCESSIBLE'
                    delta=store.save(page,cap,run)
                    for key in result['rows_delta']: result['rows_delta'][key]+=delta[key]
                    if page.inclusion_reason: result['stored_observations']+=1
                    entry.update(title=page.title,page_type=page.page_type,items=len(page.items),published_at=page.published_at,valid_from=page.valid_from,valid_to=page.valid_to,warnings=page.warnings)
                    if kind=='HOMEPAGE': result['blocks']=blocks(cap.html)
                    # Scope-aware campaign/news discovery, with raw listing evidence.
                    for found in discover('kuvalda_nnov',url,cap.html,since,today):
                        path=urlsplit(found['url']).path
                        if (path=='/promo/' or re.fullmatch(r'/promo/item-\d+/',path) or (path.startswith('/news/') and not path.startswith('/news/page-'))):
                            if found['valid_to']: proofs[found['url']]={**found,'listing_url':url,'raw_capture_ref':cap.raw_ref}
                            schedule(found['url'],'PROMOTION' if path.startswith('/promo/') else 'NEWS')
                    for nav in navigation(url,cap.html):
                        target=nav['url']
                        chosen=(nav['kind']=='SALE_CATEGORY' and nav['label'] in SALE_PARENTS)
                        if nav['kind'] in sample_counts and sample_counts[nav['kind']]<({'PRODUCT':product_samples,'CATEGORY':category_samples}[nav['kind']]):
                            if h.permitted(target): chosen=True; sample_counts[nav['kind']]+=1
                        if chosen: schedule(target,nav['kind'])
                        elif target not in scheduled:
                            nav.update(status='DISCOVERED_NOT_FETCHED',fetch_status=None,evidence_class='NAVIGATION_EVIDENCE',robots_disallowed=not policy.decision(target)[0],observed_at=cap.fetched_at.isoformat(),raw_capture_ref=cap.raw_ref)
                            save_surface(store,nav)
            except Exception as e:
                reason=str(e) if isinstance(e,(SourceBlocked,ValueError)) else type(e).__name__
                if isinstance(e,requests.HTTPError) and e.response is not None and e.response.status_code==401: fetch='AUTH_REQUIRED'
                elif h.events and h.events[-1].get('access_failure'): fetch=h.events[-1]['access_failure']
                elif h.events and urlsplit(h.events[-1].get('redirect_to','')).path.startswith(('/auth/','/account/','/member/')): fetch='AUTH_REQUIRED'
                elif isinstance(e,SourceBlocked): fetch='HTTP_BLOCKED'
                else: fetch='ERROR'
                entry.update(status='ERROR',fetch_status=fetch,evidence_class='TRANSPORT_ERROR',reason=reason)
                result['errors'].append({'url':url,'fetch_status':fetch,'reason':reason})
            save_surface(store,entry); checkpoint()
            print(json.dumps({'run':run,'processed':len(processed),'queued':len(queue),'url':url,'fetch_status':entry['fetch_status'],'items':entry.get('items',0)},ensure_ascii=False),flush=True)
        result['queue_remaining']=[{'url':u,'kind':k} for u,k in queue]
        for pending in result['queue_remaining']:
            save_surface(store,{**pending,'status':'DISCOVERED_NOT_FETCHED','fetch_status':None,'evidence_class':'NAVIGATION_EVIDENCE','robots_disallowed':not policy.decision(pending['url'])[0],'reason':'PAGE_BOUND'})
        result['sources']['kuvalda_nnov']={'fetched':sum(e['status']=='LIVE_CRAWLED' for e in result['source_map']),'discovered':len(scheduled),'errors':len(result['errors'])}
        finish_public_run(store,run,result); checkpoint()
        (out/'CRAWL_RESULT.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
        return result
    except BaseException:
        result['errors'].append({'reason':'Interrupted'}); store.run_finish(run,result); checkpoint(); raise
