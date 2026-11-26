"""Two machine campaign members for public detail QA; no catalogue expansion."""
import json
from pathlib import Path
from bs4 import BeautifulSoup
from competitor_intel.source_transport import SourceTransport
root=Path('reports/COMPETITOR_FOUR_SOURCE_ONBOARDING_2026-10-12');plan=json.loads((root/'REFRESH_PLAN.json').read_text(encoding='utf-8'))
cfg=json.loads(Path('competitor_intel/new_sources.json').read_text(encoding='utf-8'))['stanki_ru'];h=SourceTransport(cfg,root/'targeted-details')
out=[]
for target in plan['deferred'][:2]:
    if target['reason']!='CURRENT_CAMPAIGN_MEMBER':raise ValueError('Refresh gate rejected')
    c=h.get(target['url']);s=BeautifulSoup(c.html,'html.parser');title=s.select_one('h1')
    out.append({**target,'purpose':'BOUNDED_QA_OF_CURRENT_CAMPAIGN_MEMBER','fetch_status':'PUBLIC_FETCH_OK','title':title.get_text(' ',strip=True) if title else None,'raw_capture_ref':c.raw_ref,'raw_hash':c.raw_hash,'observed_at':c.fetched_at.isoformat(),'historical_metadata_not_overwritten':True})
(root/'TARGETED_DETAIL_QA.json').write_text(json.dumps({'selected':out,'requests':h.events,'targeted_product_detail_GETs':len(h.events)},ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'targeted_product_detail_GETs':len(h.events),'selected_urls':[x['url'] for x in out]},ensure_ascii=False))
