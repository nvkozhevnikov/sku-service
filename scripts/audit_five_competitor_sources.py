"""Conservative normal-public source audit, shared execution lock, no DB facts import."""
from pathlib import Path
from urllib.parse import urlsplit
from bs4 import BeautifulSoup
import json,re
from competitor_intel.source_transport import SourceTransport
from competitor_intel.model import canonical
from competitor_intel.execution import execution_lock
from competitor_intel.transport import SourceBlocked

ROOT=Path('reports/FIVE_NEW_SOURCES_2026-10-13/audit');ROOT.mkdir(parents=True,exist_ok=True)
cfgs=json.loads(Path('competitor_intel/five_sources.json').read_text(encoding='utf-8'))
def kind(url):
    path=urlsplit(url).path.lower()
    for pat,k in [(r'articles?|blog|stati|reviews','BLOG_INDEX'),(r'events|meropriy','EVENT_INDEX'),(r'moderniz','MODERNIZATION_INDEX'),(r'news|novost','NEWS_INDEX'),(r'outlet|discount|utsenk|ucenk','OUTLET_INDEX'),(r'/new(?:/|$)|novinki','NOVELTY_INDEX'),(r'actions?|akcii|sale|offers?|promo|rasprodazh|skidk','PROMOTION_INDEX')]:
        if re.search(pat,path):return k
    return None

with execution_lock('competitor-data/collection'):
    for code,cfg in cfgs.items():
        dest=ROOT/code;h=SourceTransport(cfg,dest/'captures');rows=[];captures=[];seen=set();queue=[(cfg['base_url'],'HOMEPAGE_BLOCK')]+[(u,kind(u) or 'PUBLIC_INDEX') for u in cfg['operator_urls']]
        result={'source':code,'source_map':rows,'captures':captures,'requests':h.events,'robots_metadata':None,'status':'AUDITING','full_catalog':False}
        try:
            try:
                robots=h.prepare();result['robots_metadata']={'raw_capture_ref':robots.raw_ref,'raw_hash':robots.raw_hash,'url':robots.final_url}
            except SourceBlocked:raise
            except Exception as e:result['robots_metadata']={'status':'ERROR','reason':type(e).__name__}
            while queue and len(rows)<9:
                url,surface_kind=queue.pop(0)
                if canonical(url) in seen:continue
                seen.add(canonical(url))
                entry={'url':url,'page_type':surface_kind,'selected':True,'reason':'OPERATOR_URL_OR_OBSERVED_NAVIGATION','robots_disallowed':h.robots_disallowed(url),'http_status':None,'publication_dates':False,'campaign_dates':False,'price_support':False,'old_new_price_support':False,'availability':False,'pagination':[],'browser_requirement':'UNKNOWN'};rows.append(entry)
                try:
                    cap=h.get(url);soup=BeautifulSoup(cap.html,'html.parser');text=soup.get_text(' ',strip=True)
                    entry.update(http_status=200,fetch_status='PUBLIC_FETCH_OK',canonical_url=canonical(cap.final_url),title=soup.h1.get_text(' ',strip=True) if soup.h1 else soup.title.get_text(' ',strip=True) if soup.title else '',price_support=bool(re.search(r'₽|руб\.?',text,re.I)),old_new_price_support=bool(soup.select('del,s,[class*=old-price],[class*=old_price]')),availability=bool(re.search('в наличии|под заказ|нет в наличии',text,re.I)),publication_dates=bool(soup.select('time,[class*=date]')),campaign_dates=bool(re.search(r'действует|с\s+\d.*по\s+\d|до\s+\d{1,2}',text,re.I)),browser_requirement='NOT_REQUIRED_FOR_VISIBLE_HTML' if soup.select('h1,a[href]') else 'MAY_REQUIRE_RENDERING')
                    entry['pagination']=[canonical(a['href'],cap.final_url) for a in soup.select('.pagination a[href],.pager a[href],.page-numbers a[href]') if h.permitted(canonical(a['href'],cap.final_url))][:20]
                    captures.append({'url':cap.final_url,'kind':surface_kind,'raw_capture_ref':cap.raw_ref,'raw_hash':cap.raw_hash,'observed_at':cap.fetched_at.isoformat(),'fetch_status':'PUBLIC_FETCH_OK','robots_disallowed':entry['robots_disallowed']})
                    if surface_kind=='HOMEPAGE_BLOCK':
                        for a in soup.select('a[href]'):
                            try:u=canonical(a['href'],cap.final_url)
                            except ValueError:continue
                            k=kind(u)
                            if k and h.permitted(u) and u not in seen and u not in {canonical(q[0]) for q in queue} and len(queue)<12:
                                queue.append((u,k))
                except Exception as e:
                    entry.update(fetch_status='HTTP_BLOCKED' if isinstance(e,SourceBlocked) else 'ERROR',reason=type(e).__name__)
                    if isinstance(e,SourceBlocked):break
                (dest/'SOURCE_MAP.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
            result['status']='AUDITED_PUBLIC' if captures else 'ACCESS_INCOMPLETE';result['pending_discovered_not_fetched']=queue
        except Exception as e:
            result['status']='ACCESS_INCOMPLETE';result['error']=type(e).__name__
        finally:
            h.session.close();(dest/'SOURCE_MAP.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'source':code,'status':result['status'],'urls':[{'url':r['url'],'kind':r['page_type'],'status':r.get('fetch_status')} for r in rows]},ensure_ascii=False),flush=True)
