"""Independent DOM-cell comparison facts, never semantic PASS by parser equality."""
from decimal import Decimal
import re
import sys
from scripts import qa_ltt_uss_latest as q
r,o,OUT=q.r,q.o,q.OUT

def cell_price(raw,label=''):
    cleaned=' '.join(raw.split());low=cleaned.lower()
    if not cleaned:return dict(state='missing',amount=None,currency=None,raw=raw,unit=None)
    if re.search(r'запрос|уточняйте',low):return dict(state='price_on_request',amount=None,currency=None,raw=raw,unit=None)
    amount=re.match(r'^\s*(\d[\d\s\u00a0]*(?:[.,]\d{1,2})?)',cleaned)
    if not amount:return dict(state='UNRESOLVED',amount=None,currency=None,raw=raw,unit=None)
    value=Decimal(re.sub(r'\s','',amount.group(1)).replace(',','.'))
    currency='USD' if re.search(r'\bUSD\b',cleaned,re.I) else 'RUB' if re.search(r'руб|₽',cleaned+' '+label,re.I) else None
    unit=cleaned.split('/',1)[1].strip() if '/' in cleaned else None
    return dict(state='numeric_public' if value>0 else 'zero_invalid',amount=str(value) if value>0 else None,currency=currency if value>0 else None,raw=raw,unit=unit)

def stock(raw,discontinued=False):
    low=raw.lower()
    if discontinued:return 'OutOfStock'
    if 'нет в наличии' in low or 'нет на складе' in low:return 'OutOfStock'
    if 'под заказ' in low:return 'PreOrder'
    if re.search(r'на\s+складе|в\s+наличии|заканчиваются',low):return 'InStock'
    return None

def read(entry):
    path=OUT/'SEMANTIC_PACKETS'/entry['supplier']/(o.ROOT/entry['journal']).name
    packet=r.load(path);db=r.load(OUT/entry['persisted_record']);p=db['parsed_product'];supplier=entry['supplier'];issues=[]
    if supplier in {'ltt_msk','ltt_sib'}:
        numeric=packet['numeric_price_cells'];por=packet['por_cells'];raw=' | '.join(numeric or por);label=''
        ids=sorted(set(packet['stable_ids']));own_title=packet['own_title'];stocks=packet['stock_spans'];discontinued=False
    elif supplier=='ltt_yug':
        raw=' | '.join(packet['numeric_price_cells']);label='';own_title=packet['own_title'];stocks=packet['stock_spans'];discontinued=False
        normalize=lambda s:re.sub(r'[\s\-–—]+','',s).casefold()
        ids=sorted({a['id'] for a in packet['stable_ids'] if normalize(own_title) in normalize(a['label'])})
    else:
        ids=['uss:'+entry['url'].split('.com/')[1].removesuffix('index.htm').rstrip('/')]
        own_title=next((h['text'] for h in packet['headings'] if h['tag']=='h1'),None) or next((h['text'] for h in packet['headings'] if h['tag']=='h2'),None)
        vertical=[(t,v) for t in packet['tables'] for v in t['rows'] if len(v)==2 and v[0].lower().startswith('цена')]
        if len(vertical)==1:
            table,v=vertical[0];label,raw=v;stocks=[v[1] for v in table['rows'] if len(v)==2 and v[0].lower()=='наличие']
        else:
            candidates=[]
            for t in packet['tables']:
                for hi,header in enumerate(t['rows']):
                    ai=[j for j,c in enumerate(header) if re.search(r'номер\s*арт|артикул',c,re.I)];pi=[j for j,c in enumerate(header) if c.lower().startswith('цена')];si=[j for j,c in enumerate(header) if c.lower()=='наличие']
                    if len(ai)==len(pi)==len(si)==1:
                        for row in t['rows'][hi+1:]:
                            if len(row)==len(header) and re.sub(r'\s','',row[ai[0]])==re.sub(r'\s','',p['manufacturer_article'] or ''):
                                candidates.append((row[pi[0]],header[pi[0]],row[si[0]]))
            if len(candidates)==1:raw,label,s=candidates[0];stocks=[s]
            else:raw='UNRESOLVED_COLUMN_OWNERSHIP';label='';stocks=[];issues.append('SOURCE_COMMERCIAL_OWNERSHIP_NEEDS_REVIEW')
        discontinued=bool(re.search(r'снят[оа]?\s+с\s+производства',packet['content_text'],re.I))
    expected=cell_price(raw,label);expected_stock=stock(' | '.join(stocks),discontinued)
    if ids!=[entry['external_id']]:issues.append('OWN_SOURCE_ID_MISMATCH')
    if own_title!=p['name']:issues.append('OWN_TITLE_MISMATCH')
    if expected['state']!=p['price']['state']:issues.append('PRICE_STATE_MISMATCH')
    if expected['amount'] is not None and Decimal(expected['amount'])!=Decimal(str(p['price']['current_price'])):issues.append('PRICE_AMOUNT_MISMATCH')
    if expected['amount'] is None and p['price']['current_price'] is not None:issues.append('FABRICATED_AMOUNT')
    if expected['currency']!=p['price']['currency']:issues.append('CURRENCY_MISMATCH')
    if expected_stock!=p['availability']:issues.append('AVAILABILITY_MISMATCH')
    return dict(supplier=supplier,index=entry['index'],external_id=entry['external_id'],url=entry['url'],title=own_title,parsed_title=p['name'],source_ids=ids,source_price=expected,parsed_price=p['price'],source_stocks=stocks,expected_availability=expected_stock,parsed_availability=p['availability'],source_discontinued=discontinued,kind=p['enrichment_evidence']['product_kind'],source_model=p['supplier_model'],source_article=p['manufacturer_article'],issues=issues,packet=str(path.relative_to(OUT)),packet_sha256=r.digest(path.read_bytes()),persisted_record=entry['persisted_record'],persisted_record_sha256=r.digest((OUT/entry['persisted_record']).read_bytes()),semantic_decision=None)

def show(supplier,start=0,end=100):
    entries=[e for e in r.load(OUT/'FRESH_400_SAMPLE_MANIFEST.json')['rows'] if e['supplier']==supplier]
    for e in entries[start:end]:
        f=read(e);r.save(OUT/'SEMANTIC_FACTS'/supplier/f"{e['index']:06d}.json",f)
        print(f"{f['index']}|{f['title']}|kind={f['kind']['product_kind']}|source={f['source_price']['raw']} ->{f['parsed_price']['current_price']} {f['parsed_price']['currency']} {f['parsed_price']['state']}|stock={f['source_stocks']} ->{f['parsed_availability']}|issues={f['issues']}")

if __name__=='__main__':show(sys.argv[1],int(sys.argv[2]) if len(sys.argv)>2 else 0,int(sys.argv[3]) if len(sys.argv)>3 else 100)
