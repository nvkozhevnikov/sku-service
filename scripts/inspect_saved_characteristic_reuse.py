from scripts.reuse_saved_supplier_characteristics import GROK,OUT,load
from universal_supplier.offline_review_resolution import source_title_model
from universal_supplier.beka_canonical_offline import model_key
from lxml import html
from collections import Counter
import json
rows=load(OUT/'MATCHING_AFTER.json')['rows'];stats=Counter();samples=[]
for supplier in ('partner_st','optimum'):
    base=GROK/'catalog_refresh/evidence'/supplier
    idx={r['canonical_url']:r for r in [json.loads(x) for x in (base/'CAPTURE_INDEX.jsonl').read_text().splitlines()]}
    for r in rows:
        if r['source']!=supplier:continue
        cap=idx.get(r['source_url'])
        if not cap:continue
        stats[supplier+' url']+=1
        doc=html.fromstring((base/'captures'/cap['file']).read_bytes());heads=doc.xpath('//h1')
        if not heads:continue
        stats[supplier+' h1']+=1
        visible=' '.join(' '.join(heads[0].itertext()).split())
        observed=source_title_model({'name':visible,'brand':r.get('brand','')})
        matched=model_key({**r,'model':observed})==model_key(r)
        if matched:stats[supplier+' same']+=1
        props=doc.xpath('//*[contains(@class,"product-feature") or contains(@class,"properties-detail-item-name")]')
        if props:stats[supplier+' propnodes']+=1
        if len(samples)<8 and props:samples.append({'supplier':supplier,'visible':visible,'stored':r['model'],'h1_model':observed,'match':matched,'props':len(props)})
print(json.dumps({'stats':dict(stats),'samples':samples},ensure_ascii=True))
