"""Generic business facts and offline export. No crawler/analyzer dependency."""
from collections import Counter,defaultdict
from datetime import date,datetime
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
from statistics import mean,median
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo
from .business_data import read_state,normalized_dataset,fingerprints
from .change_detection import detect_events,decimal,day,item_identity,PUBLIC_TYPES,PROMO_TYPES
from .report import write_csv

CSV_SCHEMAS={
 'EVENTS.csv':['event_id','event_type','source','entity','title','product_name','product_url','category','priority','before_version','after_version','observed_before','observed_after','relevant_values'],
 'PRICE_CHANGES.csv':['event_id','event_type','source','product_name','product_url','brand','model','category','priority','currency','old_observed_price','new_observed_price','absolute_delta','percent_delta','price_type_before','price_type_after','campaign_context','observed_before','observed_after'],
 'PROMOTIONS.csv':['source','title','url','valid_from','valid_to','status','days_remaining','product_count','high_count','mechanics','observed_at'],
 'CAMPAIGN_PRODUCTS.csv':['source','product_name','product_url','brand','model','category','priority','old_price','new_price','currency','discount_amount','discount_percent','price_type','price_label_raw','campaign','campaign_url','campaign_end','observed_at','in_campaign'],
 'NEWS.csv':['source','title','url','published_at','excerpt','observed_at'],
 'CATEGORY_ACTIVITY.csv':['source','category','active_promo_items','discounted_items','median_discount','max_discount'],
 'ENDING_SOON.csv':['source','title','url','valid_to','days_remaining','urgency','product_count','high_count'],
 'SOURCE_SUMMARY.csv':['code','name','region','capabilities','observation_mode','latest_observed_at','promotions','active_promotions','unknown_date_promotions','sale_items','high_items','latest_news','changes','median_discount','max_discount'],
 'AI_ANALYSIS.csv':['source','entity','summary','content_type','commercial_mechanic_json','strategic_signals_json','sterbrust_relevance','provider','model','prompt_version','analyzed_at']}
ITEM_FIELDS=('product_name','product_url','brand','model','category','priority','old_price','new_price','currency','price_type','price_label_raw','region','availability','campaign_name','tab_title')

def business_item(item):
    return {k:item.get(k) for k in ITEM_FIELDS}

def safe_url(value):
    if not value: return None
    p=urlsplit(str(value))
    return str(value) if p.scheme in ('https','http') and p.netloc and not p.username and not p.password else None

def mechanics(text):
    # Lexical source facts, never an inferred competitor strategy.
    result=[]
    for pattern,label in [(r'подар|комплектац','Подарок / комплект'),(r'сниж|скид|супер цены','Ценовое предложение'),
                           (r'рассроч','Рассрочка'),(r'лизинг','Лизинг'),(r'обмен','Обмен'),(r'зим|сезон','Сезонное предложение')]:
        if re.search(pattern,text or '',re.I): result.append(label)
    return result

def campaign_status(snapshot,report_date,ending_days):
    start,end=day(snapshot.get('valid_from')),day(snapshot.get('valid_to'))
    if end and end<report_date: return 'ENDED'
    if start and start>report_date: return 'UNKNOWN'
    if end and 0<=(end-report_date).days<=ending_days: return 'ENDING_SOON'
    if end: return 'ACTIVE'
    return 'UNKNOWN'

def discount(item):
    old,cur=decimal(item.get('old_price')),decimal(item.get('new_price'))
    if item.get('price_type') not in PUBLIC_TYPES or item.get('currency','RUB')!='RUB' or not old or not cur or not 0<cur<old: return None
    return ((old-cur)*100/old).quantize(Decimal('.01'))

def stats(values):
    return {'mean':mean(values) if values else None,'median':median(values) if values else None,'max':max(values) if values else None}

def build_facts(dataset,since,report_date,ending_days=7):
    since,report_date=day(since),day(report_date)
    if since>report_date: raise ValueError('Invalid report period')
    sources=sorted(dataset['sources'],key=lambda s:s['code'])
    current=[s for s in dataset['current'] if day(s['observed_at'])<=report_date]
    # Current tables are not an archive-as-of reconstruction; expose this limit.
    events_all=detect_events(dataset,report_date,ending_days)
    events=[e for e in events_all if e['event_type'] in ('PROMOTION_ENDING_SOON','PROMOTION_ENDED') or since<=day(e.get('observed_after') or e.get('observed_before'))<=report_date]
    comparable=set()
    grouped=defaultdict(list)
    for s in dataset.get('history',[]):
        if day(s['observed_at'])<=report_date: grouped[(s['source'],s['entity'])].append(s)
    for k,snaps in grouped.items():
        if len({s['observed_at'] for s in snaps})>1 and len({s.get('extractor') for s in snaps})==1: comparable.add(k)
    promotions=[]; products=[]; news=[]
    for s in sorted(current,key=lambda s:(s['source'],s['entity'])):
        is_campaign=s['page_type'] in PROMO_TYPES|{'SALE_PAGE','HOMEPAGE_BLOCK'}
        if s['page_type'] in PROMO_TYPES:
            end=day(s.get('valid_to'))
            promotions.append({'source':s['source'],'title':s['title'],'url':safe_url(s['entity']),'valid_from':s.get('valid_from'),'valid_to':s.get('valid_to'),
                               'status':campaign_status(s,report_date,ending_days),'days_remaining':(end-report_date).days if end else None,
                               'product_count':len(s.get('items',[])),'high_count':sum(i.get('priority')=='HIGH' for i in s.get('items',[])),
                               'mechanics':s.get('mechanics') or mechanics(s.get('text','')),'observed_at':s['observed_at'],
                               'products':[business_item(i) for i in s.get('items',[])],'excerpt':re.sub(r'\s+',' ',s.get('text','')).strip()[:600]})
        if s['page_type']=='NEWS' and (not s.get('published_at') or since<=day(s['published_at'])<=report_date):
            news.append({'source':s['source'],'title':s['title'],'url':safe_url(s['entity']),'published_at':s.get('published_at'),
                         'excerpt':re.sub(r'\s+',' ',s.get('text','')).strip()[:360],'observed_at':s['observed_at']})
        for i in s.get('items',[]):
            row={**business_item(i),'source':s['source'],'product_url':safe_url(i.get('product_url')),'category':i.get('category') or i.get('tab_title') or 'Категория не указана',
                 'campaign':i.get('campaign_name') or s['title'],'campaign_url':safe_url(s['entity']),'campaign_end':s.get('valid_to'),
                 'observed_at':s['observed_at'],'in_campaign':is_campaign,'campaign_status':campaign_status(s,report_date,ending_days) if s['page_type'] in PROMO_TYPES else 'CURRENT_OBSERVATION'}
            row['discount_percent']=discount(row)
            row['discount_amount']=(decimal(i['old_price'])-decimal(i['new_price'])) if row['discount_percent'] is not None else None
            products.append(row)
    products.sort(key=lambda i:({'HIGH':0,'NORMAL':1,'LOW':2}.get(i.get('priority'),1),i['source'],i['product_name'],i['campaign_url'],i.get('tab_title') or ''))
    unique={}
    ambiguous=set()
    for i in products:
        key=(i['source'],i.get('region') or '',i['product_url'] or i['product_name'],i['price_type'],i.get('currency','RUB'))
        if key in unique and unique[key]['new_price']!=i['new_price']: ambiguous.add(key)
        unique[key]=i
    public_discounts=[i for k,i in unique.items() if k not in ambiguous and i['discount_percent'] is not None and i['in_campaign'] and i['campaign_status']!='ENDED']
    values=[i['discount_percent'] for i in public_discounts]
    distribution=[{'label':label,'count':sum(low<=v<high for v in values)} for label,low,high in [('0–5%',0,5),('5–10%',5,10),('10–20%',10,20),('20–30%',20,30),('30%+',30,Decimal('Infinity'))]]
    categories=[]
    groups=defaultdict(list)
    for k,i in unique.items():
        if i['in_campaign'] and i['campaign_status']!='ENDED': groups[(i['source'],i['category'])].append(i)
    for (source,category),rows in sorted(groups.items()):
        d=[r['discount_percent'] for r in rows if r['discount_percent'] is not None and (r['source'],r.get('region') or '',r['product_url'] or r['product_name'],r['price_type'],r.get('currency','RUB')) not in ambiguous]
        categories.append({'source':source,'category':category,'active_promo_items':len(rows),'discounted_items':len(d),'median_discount':median(d) if d else None,'max_discount':max(d) if d else None})
    ending=[{**p,'urgency':'3_DAYS' if p['days_remaining']<=3 else '7_DAYS'} for p in promotions if p['status']=='ENDING_SOON']
    price_events=[e for e in events if e['event_type'] in ('PRICE_DECREASE','PRICE_INCREASE','PRICE_SEMANTICS_CHANGED','PRICE_REVIEW')]
    meaningful=[e for e in events if e['event_type']!='BASELINE_OBSERVATION']
    counts=Counter(e['event_type'] for e in meaningful)
    source_summary=[]
    for src in sources:
        ps=[p for p in promotions if p['source']==src['code']]; ins=[i for i in products if i['source']==src['code']]
        ss=[s for s in current if s['source']==src['code']]; ds=[i['discount_percent'] for i in public_discounts if i['source']==src['code']]
        comparable_count=sum(k[0]==src['code'] for k in comparable)
        source_summary.append({**src,'observation_mode':'COMPARABLE_HISTORY' if comparable_count else 'INITIAL BASELINE',
                               'latest_observed_at':max((s['observed_at'] for s in ss),default=None),'promotions':len(ps),
                               'active_promotions':sum(p['status'] in ('ACTIVE','ENDING_SOON') for p in ps),'unknown_date_promotions':sum(p['status']=='UNKNOWN' for p in ps),
                               'sale_items':sum(i['price_type']=='SALE' or i['campaign_url'] in {s['entity'] for s in ss if s['page_type']=='SALE_PAGE'} for i in ins),
                               'high_items':sum(i.get('priority')=='HIGH' for i in ins),'latest_news':[n for n in news if n['source']==src['code']],
                               'changes':sum(e['source']==src['code'] for e in meaningful),'median_discount':median(ds) if ds else None,'max_discount':max(ds) if ds else None,
                               'distribution':[{'label':b['label'],'count':sum((0 if j==0 else [5,10,20,30][j-1])<=d<([5,10,20,30,Decimal('Infinity')][j]) for d in ds)} for j,b in enumerate(distribution)]})
    summary={'report_date':str(report_date),'since':str(since),'timezone':'Europe/Moscow','observation_mode':'INITIAL BASELINE' if not comparable else 'MIXED / COMPARABLE_HISTORY',
             'latest_observed_at':max((s['observed_at'] for s in current),default=None),'current_pages':len(current),'active_promotions':sum(p['status'] in ('ACTIVE','ENDING_SOON') for p in promotions),
             'unknown_date_promotions':sum(p['status']=='UNKNOWN' for p in promotions),'new_promotions':counts['NEW_PROMOTION'] if comparable else None,
             'changed_promotions':counts['PROMOTION_CHANGED'] if comparable else None,'ending_soon':len(ending),
             'campaign_products':sum(i['in_campaign'] for i in products),'current_price_rows':len(products),'high_industrial':sum(i.get('priority')=='HIGH' for i in products),
             'price_decreases':counts['PRICE_DECREASE'] if comparable else None,'price_increases':counts['PRICE_INCREASE'] if comparable else None,
             'discount_stats':stats(values),'discounted_public_products':len(values),'new_news':counts['NEW_NEWS'] if comparable else None,
             'new_products':counts['NEW_PRODUCT_OBSERVED'] if comparable else None,'event_counts':dict(sorted(Counter(e['event_type'] for e in events).items())),
             'comparable_entities':len(comparable),'ambiguous_price_contexts':len(ambiguous),'ai_network_calls':0,
             'notes':['Старая цена на сайте и изменение цены между наблюдениями — разные показатели.',
                      'В аналитике скидок используются только публичные сопоставимые цены PUBLIC / SALE в рублях. Спеццены исключены.',
                      'Число карточек отражает контексты акций: один товар может встречаться в нескольких блоках.',
                      'Новости показаны с исходным фрагментом; ИИ-резюме без сохранённого анализа не создаётся.']+dataset.get('limitations',[])}
    return {'summary':summary,'sources':source_summary,'events':events,'important':meaningful,'price_changes':price_events,
            'promotions':promotions,'products':products,'news':sorted(news,key=lambda n:(n.get('published_at') or '',n['source'],n['title']),reverse=True),
            'categories':categories,'ending_soon':ending,'distribution':distribution,'analysis':dataset.get('analysis',[])}

def export_preview(dataset,since,report_date,output,ending_days=7):
    from .business_html import render_html
    out=Path(output); out.mkdir(parents=True,exist_ok=True)
    facts=build_facts(dataset,since,report_date,ending_days)
    serialized=json.loads(json.dumps(facts,ensure_ascii=False,default=str))
    (out/'SUMMARY.json').write_text(json.dumps(serialized['summary'],ensure_ascii=False,indent=2,sort_keys=True)+'\n',encoding='utf-8')
    mappings={'EVENTS.csv':'events','PRICE_CHANGES.csv':'price_changes','PROMOTIONS.csv':'promotions','CAMPAIGN_PRODUCTS.csv':'products',
              'NEWS.csv':'news','CATEGORY_ACTIVITY.csv':'categories','ENDING_SOON.csv':'ending_soon','SOURCE_SUMMARY.csv':'sources','AI_ANALYSIS.csv':'analysis'}
    for name,key in mappings.items(): write_csv(out/name,facts[key],CSV_SCHEMAS[name])
    (out/'index.html').write_text(render_html(serialized),encoding='utf-8')
    (out/'EVENTS.json').write_text(json.dumps(serialized['events'],ensure_ascii=False,indent=2,sort_keys=True)+'\n',encoding='utf-8')
    previous=out/'EVENT_REGISTRY.json'
    previous_ids=set(json.loads(previous.read_text())['event_ids']) if previous.exists() else set()
    ids={e['event_id'] for e in facts['events']}
    previous.write_text(json.dumps({'event_ids':sorted(ids)},indent=2)+'\n',encoding='utf-8')
    result={'new_events':len(ids-previous_ids),'duplicate_events':len(facts['events'])-len(ids),'events':len(ids),'business_facts_sha256':hashlib.sha256(json.dumps(serialized,sort_keys=True,ensure_ascii=False).encode()).hexdigest(),
            'report_counts':{k:len(facts[k]) for k in ('sources','promotions','products','news','ending_soon','analysis')},'html_path':str((out/'index.html').resolve())}
    (out/'BUILD_RESULT.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return result

def preview_from_store(store,since,report_date,output,ending_days=7):
    before=read_state(store); proof_before=fingerprints(before)
    result=export_preview(normalized_dataset(before),since,report_date,output,ending_days)
    after=fingerprints(read_state(store))
    proof={'status':'PASS' if proof_before==after else 'FAIL','db_mutations':0 if proof_before==after else 'UNKNOWN','before':proof_before,'after':after,
           'hash_method':'SHA256 sorted compact JSON; normalized Decimal/date serialization; same algorithm before/after',
           'source_HTTP':0,'AI_NETWORK_CALLS':0,'read_mode':'REPEATABLE READ READ ONLY; no DDL/DML'}
    Path(output,'SOURCE_FACT_IMMUTABILITY.json').write_text(json.dumps(proof,indent=2)+'\n',encoding='utf-8')
    if proof['status']!='PASS': raise RuntimeError('Source facts changed during preview; do not accept artifact')
    return result
