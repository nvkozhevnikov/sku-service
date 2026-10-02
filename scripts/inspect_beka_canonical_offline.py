"""Offline checkpoint inspection only; never HTTP/SQL/source assessment."""
from pathlib import Path
import sys,json,re
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
from scripts.resolve_reviews_offline import load
from sterbrust_matching.normalization import normalize_model
BASE=ROOT/'reports/RC_LOCAL/BEKA_OPERATOR_CLOSED_2026-10-01'
if __name__=='__main__':
    matching=load(BASE/'CANONICAL_VISIBLE_CONTEXT/MATCHING_AFTER.json')
    rows=[r for r in matching['rows'] if r['source'] in ('beka_mak','beka_mak_tr')]
    for r in rows:
        if str(r['external_id']) in ('19154','19166','19171'):
            print(json.dumps({'kind':'source',**{k:r.get(k) for k in ('external_id','model','properties')}},ensure_ascii=True))
    with (ROOT/'reports/STERBRUST_REGISTRY.jsonl').open('rb') as stream:
        for line in stream:
            t=json.loads(line)
            if re.search(r'(?i)(?:320.?GS|360.?CH|440.?CDGS)',str(t.get('model_raw',''))+' '+t['name']):
                from lxml import html
                raw=t.get('raw_data',{})
                print('RAW_KEYS',list(raw)[:20])
                detail=raw.get('detailText','')
                tables=[]
                if detail:
                    for tr in html.fromstring(detail).xpath('//tr'):
                        cells=[' '.join(c.itertext()).strip() for c in tr.xpath('./td|./th')]
                        if cells: tables.append(cells)
                print(json.dumps({'kind':'canonical',**{k:t.get(k) for k in ('sterbrust_product_id','name','model_raw','brand_raw','active')},'tables':tables,'props':{p['property_name']:p['value_flat'] for p in t.get('properties',[]) if p.get('value_flat') not in (None,'','N')}},ensure_ascii=False))
