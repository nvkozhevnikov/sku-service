import json,re
from pathlib import Path
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from competitor_intel.source_transport import SourceTransport,decode_public_html
from competitor_intel.model import parse_date
root=Path('reports/COMPETITOR_FOUR_SOURCE_ONBOARDING_2026-10-12');cfgs=json.loads(Path('competitor_intel/new_sources.json').read_text(encoding='utf-8'))
for code in ('stanki_ru','titaniumgroup'):
    cfg=cfgs[code];h=SourceTransport(cfg,root/'audit'/code);targets=[];out=[]
    if code=='stanki_ru':
        x=json.loads((root/'STANKI_SPEC.json').read_text(encoding='utf-8'));s=BeautifulSoup(decode_public_html(Path(x['raw_capture_ref']).read_bytes()),'html.parser')
        targets=[(urljoin(x['url'],a['href']),'PROMOTION') for a in s.select('a.specList__item[href]')]
    else:
        audit=json.loads((root/'audit'/code/'AUDIT.json').read_text(encoding='utf-8'))
        x=next(x for x in audit['surfaces'] if x['url'].endswith('/articles/'));s=BeautifulSoup(decode_public_html(Path(x['raw_capture_ref']).read_bytes()),'html.parser')
        base=urljoin(x['url'],s.base['href']) if s.base else x['url']
        for card in s.select('.article-tpl'):
            d=parse_date(card.get_text(' ',strip=True));a=card.select_one('a[href]')
            if a and d and d.isoformat()>='2026-09-01':targets.append((urljoin(base,a['href']),'BLOG'))
        robots=Path(audit['robots_capture']).read_text(encoding='utf-8');targets.extend((u,'SITEMAP_INDEX') for u in re.findall(r'^Sitemap:\s*(\S+)',robots,re.M))
    for url,kind in targets:
        try:
            c=h.get(url);row={'url':c.final_url,'kind':kind,'raw_capture_ref':c.raw_ref,'raw_hash':c.raw_hash,'observed_at':c.fetched_at.isoformat(),'fetch_status':'PUBLIC_FETCH_OK'}
        except Exception as e:row={'url':url,'kind':kind,'fetch_status':'ERROR','error':type(e).__name__}
        out.append(row);print(code,row['fetch_status'],url,flush=True)
        (root/'audit'/code/'EXTRA_EVIDENCE.json').write_text(json.dumps({'captures':out,'requests':h.events},ensure_ascii=False,indent=2),encoding='utf-8')
