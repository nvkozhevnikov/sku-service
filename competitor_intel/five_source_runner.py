"""Bounded baseline adapter invoked exclusively through the shared CollectionService."""
from collections import deque
from datetime import date,datetime,timezone
from pathlib import Path
from urllib.parse import urlsplit,parse_qs
import hashlib,json,time
from bs4 import BeautifulSoup
from psycopg.types.json import Jsonb
from .source_transport import SourceTransport
from .transport import Capture,SourceBlocked
from .five_source_extract import extract,discover,public_url
from .store import json_value

AUDIT=Path(__file__).resolve().parents[1]/'reports/FIVE_NEW_SOURCES_2026-10-13/audit'
CONTENT={'NEWS','BLOG','EVENT','MODERNIZATION','CONTENT','NEW_PRODUCT_ANNOUNCEMENT'}

def initial_audit_cache(audit,has_prior_observations):
    """Audit captures bootstrap once; subsequent snapshots must use fresh transport."""
    return {} if has_prior_observations else {c['url']:c for c in audit.get('captures',[])}

def collect_baseline(repo,code,run,since,output,max_pages,deadline):
    config=json.loads(Path(__file__).with_name('five_sources.json').read_text(encoding='utf-8'))[code]
    audit_file=AUDIT/code/'SOURCE_MAP.json'
    audit=json.loads(audit_file.read_text(encoding='utf-8')) if audit_file.exists() else {'captures':[]}
    prior=bool(repo.store.query('SELECT p.id FROM competitor_pages p JOIN competitor_sources s ON s.id=p.source_id WHERE s.code=%s LIMIT 1',(code,)))
    dest=Path(output)/code;dest.mkdir(parents=True,exist_ok=True)
    h=SourceTransport(config,dest/'captures');cached=initial_audit_cache(audit,prior);queue=deque();scheduled=set();processed=set();maps=[];proofs={};saved=[];errors=[];price_types={};campaign_pages={}
    stats={'status':'COMPLETED','stored':0,'pages':0,'versions':0,'items':0,'errors':errors,'HTTP_GETs':0,'reused_audit_captures':0,'full_catalog':False,'baseline':True,'product_detail_GETs':0,'campaign_coverage':{}}
    from urllib.robotparser import RobotFileParser
    robots=audit.get('robots_metadata') or {}
    if robots.get('raw_capture_ref'):
        rp=RobotFileParser();rp.parse(Path(robots['raw_capture_ref']).read_text(encoding='utf-8').splitlines())
        for host in config['hosts']:h.robots[host]=rp
    def enqueue(url,kind,published=None):
        if not h.permitted(url) or any(k in url for k in ('arrFilter','reviews=','sort=','order=')):return
        if published:proofs[url]=published
        if url not in scheduled:scheduled.add(url);queue.append((url,kind))
    enqueue(config['base_url'],'HOMEPAGE_BLOCK')
    for url in config['operator_urls']:enqueue(url,'BLOG_INDEX' if '/articles' in url else 'NOVELTY_INDEX')
    for cap in audit.get('captures',[]):
        if any(x in cap['url'] for x in ('arrFilter','/help/offer/')):continue
        # Failed stale detail URLs aren't retried just to inflate coverage.
        hint=cap['kind']
        if code=='amur_stan' and '/discount/' in cap['url']:hint='PROMOTION_INDEX' if cap['url'].rstrip('/').endswith('/discount') else 'PROMOTION'
        if code=='jettools_ru' and '/about/news/' in cap['url'] and not cap['url'].rstrip('/').endswith('/news'):
            # Homepage explicitly dates these archived news before September.
            continue
        enqueue(cap['url'],hint)
        if cap['url'] in cached:
            raw=Path(cap['raw_capture_ref']).read_bytes();cap_obj=Capture(cap['url'],cap['url'],raw.decode('utf-8'),cap['raw_capture_ref'],cap['raw_hash'],datetime.fromisoformat(cap['observed_at']),'HTTP')
            for found in discover(code,cap_obj):
                if found.get('published_at'):proofs[found['url']]=found['published_at']
                enqueue(found['url'],found['kind'],found.get('published_at'))
    targets=getattr(repo,'collection_options',{}).get(code)
    if targets:
        queue.clear();scheduled.clear()
        for target in targets:enqueue(target['url'],target['kind'],target.get('published_at'))
    def remaining():
        n=deadline-time.monotonic()
        if n<=0:raise TimeoutError('MAX_RUNTIME')
        return n
    def sleep(n):
        if n>=remaining():raise TimeoutError('MAX_RUNTIME')
        time.sleep(n)
    h.sleep=sleep;get=h.session.get
    def bounded_get(*a,**k):
        b=min(35,remaining()/2);k['timeout']=(b,b);return get(*a,**k)
    h.session.get=bounded_get
    def surface(entry):
        with repo.store.conn.transaction(),repo.store.conn.cursor() as c:
            c.execute('''INSERT INTO competitor_source_surfaces(source_id,canonical_url,surface_kind,title,status,robots_disallowed,fetch_status,evidence_class,reason,observed_at,raw_capture_ref,details)
              SELECT id,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s FROM competitor_sources WHERE code=%s
              ON CONFLICT(source_id,canonical_url) DO UPDATE SET status=excluded.status,fetch_status=excluded.fetch_status,reason=excluded.reason,observed_at=excluded.observed_at,raw_capture_ref=excluded.raw_capture_ref,details=excluded.details''',
              (entry['url'],entry['kind'],entry.get('title'),entry['status'],entry.get('robots_disallowed'),entry['fetch_status'],'LIVE_CRAWLED' if entry['status']=='LIVE_CRAWLED' else 'TRANSPORT_ERROR',entry.get('reason','NORMAL_PUBLIC_BASELINE'),entry.get('observed_at',datetime.now(timezone.utc)),entry.get('raw_capture_ref'),Jsonb(json_value(entry)),code))
    try:
        if prior or not robots.get('raw_capture_ref'):h.prepare()
        if code=='harvey_rus':
            home=cached.get(config['base_url'])
            if home is None:
                home_cap=h.get(config['base_url'])
                home={'url':home_cap.final_url,'raw_capture_ref':home_cap.raw_ref,'raw_hash':home_cap.raw_hash,'observed_at':home_cap.fetched_at.isoformat()}
                cached[config['base_url']]=home
            soup=BeautifulSoup(Path(home['raw_capture_ref']).read_text(encoding='utf-8'),'html.parser') if home else None
            styles=soup.select('link[rel=stylesheet][href]') if soup else []
            def style_priority(node):
                href=node['href'].split('?')[0]
                preferred=['/local/templates/harvey/css/styles.css','/local/templates/harvey/styles.css','/local/templates/harvey/css/main.css','/local/templates/harvey/template_styles.css']
                return preferred.index(href) if href in preferred else 10
            styles.sort(key=style_priority)
            proven=False;style_evidence=[]
            for style in styles:
                target=public_url(style['href'],config['base_url'])
                if not h.permitted(target):continue
                asset=h.get(target)
                proven=bool(__import__('re').search(r'\.base__price(?=[\s,:{])[^{}]*\{[^}]*text-decoration[^}]*line-through',asset.html))
                style_evidence.append({'url':asset.final_url,'raw_capture_ref':asset.raw_ref,'raw_hash':asset.raw_hash,'base_price_strikethrough_verified':proven})
                if proven or len(style_evidence)>=5:break
            (dest/'PRICE_STYLE_EVIDENCE.json').write_text(json.dumps({'assets':style_evidence,'base_price_strikethrough_verified':proven},indent=2),encoding='utf-8')
            if not proven:raise ValueError('OLD_PRICE_STYLE_NOT_PROVEN')
        while queue and len(processed)<max_pages:
            remaining();url,hint=queue.popleft()
            if url in processed:continue
            processed.add(url);entry={'url':url,'kind':hint,'robots_disallowed':h.robots_disallowed(url),'selected':True,'reason':'AUDITED_NAVIGATION_CURRENT_SURFACE_OR_DATED_CONTENT','status':'DISCOVERED_NOT_FETCHED','fetch_status':None};maps.append(entry)
            # Map/checkpoint exists before each new request.
            (dest/'SOURCE_MAP.json').write_text(json.dumps(json_value({'source':code,'entries':maps,'pending':list(queue)}),ensure_ascii=False,indent=2),encoding='utf-8')
            try:
                if url in cached:
                    row=cached[url];raw=Path(row['raw_capture_ref']).read_bytes();assert hashlib.sha256(raw).hexdigest()==row['raw_hash']
                    cap=Capture(url,url,raw.decode('utf-8'),row['raw_capture_ref'],row['raw_hash'],datetime.fromisoformat(row['observed_at']),'HTTP');stats['reused_audit_captures']+=1
                else:cap=h.get(url)
                page=extract(code,cap,hint,since,datetime.now(timezone.utc).date(),proofs.get(url))
                entry.update(title=page.title,page_type=page.page_type,content_kind=page.content_kind,http_status=200,status='LIVE_CRAWLED',fetch_status='PUBLIC_FETCH_OK',raw_capture_ref=cap.raw_ref,raw_hash=cap.raw_hash,observed_at=cap.fetched_at.isoformat(),published_at=page.published_at,valid_from=page.valid_from,valid_to=page.valid_to,items=len(page.items),included=bool(page.inclusion_reason),price_support=bool(page.items),old_new_price_support=any(i.old_price and i.new_price for i in page.items),browser_requirement='NOT_REQUIRED_FOR_CAPTURED_DOM')
                if any(w in ('INVALID_OLD_NEW_PRICE','UNPARSED_PRICE') for i in page.items for w in i.warnings):raise ValueError('PRICE_QA_REJECTED')
                if not page.inclusion_reason:entry['selected']=False;entry['reason']='DATE_WINDOW_NOT_SUPPORTED';surface(entry);continue
                if page.page_type=='PROMOTION':
                    campaign_root=url.split('?')[0];campaign_pages.setdefault(campaign_root,[]).append(url)
                    page.scope_key=campaign_root;page.campaign_coverage='PARTIAL'
                delta=repo.store.save(page,cap,run)
                for k in ('pages','versions','items'):stats[k]+=delta[k]
                stats['stored']+=1
                with repo.store.conn.transaction(),repo.store.conn.cursor() as c:c.execute("UPDATE competitor_page_versions v SET qa_status='ACCEPTED' FROM competitor_pages p,competitor_sources src WHERE v.page_id=p.id AND p.source_id=src.id AND src.code=%s AND p.canonical_url=%s AND v.content_hash=%s",(code,page.canonical_url,page.content_hash()))
                saved.append({'url':cap.final_url,'kind':hint,'published_at_proof':proofs.get(url),'raw_capture_ref':cap.raw_ref,'raw_hash':cap.raw_hash,'observed_at':cap.fetched_at.isoformat(),'canonical_url':page.canonical_url})
                surface(entry)
                if not targets:
                    for found in discover(code,cap):enqueue(found['url'],found['kind'],found.get('published_at'))
                print(json.dumps({'source':code,'processed':len(processed),'queued':len(queue),'stored':stats['stored'],'items':stats['items']},ensure_ascii=False),flush=True)
            except Exception as e:
                entry.update(status='ERROR',fetch_status='HTTP_BLOCKED' if isinstance(e,SourceBlocked) else 'ERROR',reason=type(e).__name__)
                if h.events and h.events[-1].get('status'):entry['http_status']=h.events[-1]['status']
                surface(entry);errors.append({'url':url,'reason':type(e).__name__,'http_status':entry.get('http_status')})
                if isinstance(e,(SourceBlocked,TimeoutError)):break
            (dest/'SAVED_CAPTURES.json').write_text(json.dumps(saved,ensure_ascii=False,indent=2),encoding='utf-8')
        stats['not_fetched_due_to_bound']=len(queue);stats['HTTP_GETs']=len(h.events)
        for root,parts in campaign_pages.items():
            nums={int(parse_qs(urlsplit(u).query).get('PAGEN_1',['1'])[0]) for u in parts}
            pending=sum(u.split('?')[0]==root for u,k in queue)
            complete=not pending and nums==set(range(1,max(nums)+1)) and not any(e['url'].split('?')[0]==root for e in errors)
            stats['campaign_coverage'][root]={'status':'COMPLETE' if complete else 'PARTIAL','public_pagination':'COMPLETE' if complete else 'PARTIAL','comparable_membership_asserted':False,'observed_public_pages':len(parts),'reason':'OBSERVED_PUBLIC_PAGINATION_EXHAUSTED' if complete else 'BOUNDED_OR_ERROR','pagination_pending':pending}
        if queue or errors:stats['status']='PARTIAL' if saved else 'FAILED';stats['reason']='BOUNDED_OR_ACCESS_GAPS'
        (dest/'SAVED_CAPTURES.json').write_text(json.dumps(saved,ensure_ascii=False,indent=2),encoding='utf-8')
        (dest/'SOURCE_MAP.json').write_text(json.dumps(json_value({'source':code,'entries':maps,'pending':list(queue),'result':stats,'requests':h.events}),ensure_ascii=False,indent=2),encoding='utf-8')
        (dest/'RESULT.json').write_text(json.dumps(stats,ensure_ascii=False,indent=2),encoding='utf-8')
        return stats
    finally:h.session.close()
