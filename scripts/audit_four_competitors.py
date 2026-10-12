"""Bounded homepage/navigation/robots audit. No product details or full catalogue."""
import json
from pathlib import Path
from urllib.parse import urljoin,urlsplit
from bs4 import BeautifulSoup
from competitor_intel.source_transport import SourceTransport
from competitor_intel.model import canonical

configs=json.loads(Path('competitor_intel/new_sources.json').read_text(encoding='utf-8'))
root=Path('reports/COMPETITOR_FOUR_SOURCE_ONBOARDING_2026-10-12/audit');root.mkdir(parents=True,exist_ok=True)
for source,config in configs.items():
    out=root/source;out.mkdir(exist_ok=True)
    h=SourceTransport(config,out); result={'source':source,'captures':[],'candidates':[],'errors':[]}
    try:
        robots=h.prepare();result['robots_capture']=robots.raw_ref
        cap=h.get(config['base_url']);soup=BeautifulSoup(cap.html,'html.parser')
        result['captures'].append({'url':cap.final_url,'raw_capture_ref':cap.raw_ref,'raw_hash':cap.raw_hash,'observed_at':cap.fetched_at.isoformat(),'robots_disallowed':h.robots_disallowed(cap.final_url)})
        result['title']=soup.title.get_text(' ',strip=True) if soup.title else None
        links={}
        for a in soup.select('a[href]'):
            label=a.get_text(' ',strip=True)
            try: url=canonical(a['href'],cap.final_url)
            except ValueError: continue
            if not h.permitted(url): continue
            combined=(label+' '+urlsplit(url).path).lower()
            if any(t in combined for t in ['акци','распрод','спецпред','новост','стать','блог','promo','sale','action','special_offer','news','blog','article']):
                # Navigation evidence only; no regional mirrors/unrelated catalogue.
                if urlsplit(url).path.startswith('/catalog/') and not any(t in label.lower() for t in ['акци','распрод','спецпред']): continue
                links[url]={'url':url,'label':label,'robots_disallowed':h.robots_disallowed(url)}
        result['candidates']=list(links.values())[:40]
    except Exception as e: result['errors'].append({'type':type(e).__name__,'reason':str(e) if type(e).__name__=='SourceBlocked' else type(e).__name__})
    result['requests']=h.events
    (out/'AUDIT.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:result[k] for k in ('source','candidates','errors')},ensure_ascii=False),flush=True)
