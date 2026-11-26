"""Fetch only homepage-discovered listing surfaces; preserve independent access status."""
import json
from pathlib import Path
from bs4 import BeautifulSoup
from competitor_intel.source_transport import SourceTransport,decode_public_html
configs=json.loads(Path('competitor_intel/new_sources.json').read_text(encoding='utf-8'))
root=Path('reports/COMPETITOR_FOUR_SOURCE_ONBOARDING_2026-10-12/audit')
for code,cfg in configs.items():
    directory=root/code; audit=json.loads((directory/'AUDIT.json').read_text(encoding='utf-8'))
    h=SourceTransport(cfg,directory)
    if audit.get('robots_capture'):
        from urllib.robotparser import RobotFileParser
        rp=RobotFileParser();rp.parse(Path(audit['robots_capture']).read_text(encoding='utf-8').splitlines())
        for host in cfg['hosts']:h.robots[host]=rp
    if audit['captures']:
        home=audit['captures'][0]; soup=BeautifulSoup(decode_public_html(Path(home['raw_capture_ref']).read_bytes()),'html.parser')
        audit['title']=soup.title.get_text(' ',strip=True)
        for link in audit['candidates']:
            a=soup.find('a',href=lambda x:x and link['url'].endswith(x))
            if a:link['label']=a.get_text(' ',strip=True)
    urls=[x['url'] for x in audit['candidates'] if x['url'].split(cfg['base_url'])[-1] in ('sale/','about/news/','special_offers/','news/','articles/','actions/')]
    if code=='youmet': urls=[cfg['base_url']] # robots failure does not prove homepage access
    result=[]
    for url in urls:
        try:
            cap=h.get(url);s=BeautifulSoup(cap.html,'html.parser')
            row={'url':cap.final_url,'raw_capture_ref':cap.raw_ref,'raw_hash':cap.raw_hash,'observed_at':cap.fetched_at.isoformat(),'fetch_status':'PUBLIC_FETCH_OK','robots_disallowed':h.robots_disallowed(url),'title':s.title.get_text(' ',strip=True) if s.title else None}
        except Exception as e:row={'url':url,'fetch_status':'HTTP_BLOCKED' if type(e).__name__=='SourceBlocked' else 'ERROR','error':type(e).__name__,'robots_disallowed':h.robots_disallowed(url)}
        result.append(row)
        if row['fetch_status']=='HTTP_BLOCKED':break
    audit['surfaces']=result;audit['requests']+=h.events
    (directory/'AUDIT.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'source':code,'surfaces':result},ensure_ascii=False),flush=True)
