"""Conservative listing/campaign capture queue derived from saved public navigation."""
import json
from pathlib import Path
from urllib.parse import urljoin,urlsplit
from bs4 import BeautifulSoup
from competitor_intel.source_transport import SourceTransport,decode_public_html
from competitor_intel.model import canonical,parse_date
from urllib.robotparser import RobotFileParser

root=Path('reports/COMPETITOR_FOUR_SOURCE_ONBOARDING_2026-10-12/audit')
cfgs=json.loads(Path('competitor_intel/new_sources.json').read_text(encoding='utf-8'))
for code,cfg in cfgs.items():
    audit=json.loads((root/code/'AUDIT.json').read_text(encoding='utf-8'));h=SourceTransport(cfg,root/code)
    if audit.get('robots_capture'):
        rp=RobotFileParser();rp.parse(Path(audit['robots_capture']).read_text(encoding='utf-8').splitlines())
        for host in cfg['hosts']:h.robots[host]=rp
    captures=audit['captures']+[x for x in audit.get('surfaces',[]) if x.get('raw_capture_ref')]
    queue=[];seen={canonical(x['url']) for x in captures};result=[]
    def add(href,base,kind):
        u=canonical(href,base)
        if h.permitted(u) and u not in seen and u not in {x[0] for x in queue}:queue.append((u,kind))
    for cap in captures:
        s=BeautifulSoup(decode_public_html(Path(cap['raw_capture_ref']).read_bytes()),'html.parser')
        base=urljoin(cap['url'],s.base.get('href','')) if s.base else cap['url']
        if code=='jet_online' and '/special_offers/' in cap['url']:
            for a in s.select('.pagination a[href]'):add(a['href'],base,'SALE_PAGE')
        if code=='jet_online' and cap['url'].endswith('/news/'):
            for a in s.select('a.tile[href]'):
                d=parse_date(a.get_text(' ',strip=True))
                if d and d.isoformat()>='2026-09-01':add(a['href'],base,'NEWS')
        if code=='stanki_ru':
            if cap['url'].endswith('/sale/'):
                for a in s.select('.catalogSectionsGrid__link[href]'):
                    if 'инструмент для' not in a.get_text(' ',strip=True).lower():add(a['href'],base,'SALE_PAGE')
            if cap['url'].endswith('/about/news/'):
                for card in s.select('.news-list-item'):
                    d=parse_date(card.get_text(' ',strip=True));a=card.select_one('.news-list-item__name[href]')
                    if a and d and d.isoformat()>='2026-09-01':add(a['href'],base,'NEWS')
            if cap['url']==cfg['base_url']:
                for a in s.select('a[href]'):
                    if 'close-sale-metall-2026' in a['href']:add(a['href'],base,'PROMOTION')
        if code=='titaniumgroup':
            if cap['url'].endswith('/news/'):
                # Listing exposes no dates. Bounded first six details establish chronology.
                for a in s.select('.blog-tpl a.title[href]')[:6]:add(a['href'],base,'NEWS')
            if cap['url'].endswith('/articles/'):
                for card in s.select('.blog-tpl'):
                    d=parse_date(card.get_text(' ',strip=True));a=card.select_one('a.title[href]')
                    if a and d and d.isoformat()>='2026-09-01':add(a['href'],base,'BLOG')
    while queue and len(result)<70:
        url,kind=queue.pop(0);seen.add(url)
        try:
            cap=h.get(url);s=BeautifulSoup(cap.html,'html.parser')
            row={'url':cap.final_url,'kind':kind,'raw_capture_ref':cap.raw_ref,'raw_hash':cap.raw_hash,'observed_at':cap.fetched_at.isoformat(),'fetch_status':'PUBLIC_FETCH_OK','robots_disallowed':h.robots_disallowed(url)}
            if code=='jet_online' and kind=='SALE_PAGE':
                for a in s.select('.pagination a[href]'):add(a['href'],url,kind)
        except Exception as e:row={'url':url,'kind':kind,'fetch_status':'HTTP_BLOCKED' if type(e).__name__=='SourceBlocked' else 'ERROR','error':type(e).__name__}
        result.append(row)
        (root/code/'LIVE_EVIDENCE.json').write_text(json.dumps({'source':code,'captures':result,'pending':queue,'requests':h.events},ensure_ascii=False,indent=2),encoding='utf-8')
        print(code,row['fetch_status'],url,flush=True)
        if row['fetch_status']=='HTTP_BLOCKED':break
    if not result:(root/code/'LIVE_EVIDENCE.json').write_text(json.dumps({'source':code,'captures':[],'pending':queue,'requests':h.events}),encoding='utf-8')
