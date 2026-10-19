import csv
import json
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from statistics import mean, median

def write_csv(path, rows, columns):
    with path.open('w',newline='',encoding='utf-8-sig') as f:
        w = csv.DictWriter(f,fieldnames=columns,extrasaction='ignore'); w.writeheader()
        for row in rows:
            # Escape formula injection in operator-facing CSV.
            safe = {k:json.dumps(v,ensure_ascii=False,default=str) if isinstance(v,(dict,list)) else v for k,v in row.items()}
            for k,v in safe.items():
                if isinstance(v,str) and v.startswith(('=','+','-','@')): safe[k] = "'"+v
            w.writerow(safe)

def report(store,since,today,output):
    out = Path(output); out.mkdir(parents=True,exist_ok=True)
    pages = store.query('''SELECT s.code AS source,p.*,v.id AS version_id,v.raw_capture_ref,v.normalized_text
                          FROM competitor_pages p JOIN competitor_sources s ON s.id=p.source_id
                          JOIN competitor_page_versions v ON v.page_id=p.id AND v.content_hash=p.current_content_hash
                          WHERE (p.published_at BETWEEN %s AND %s) OR (p.valid_to>=%s AND (p.valid_from IS NULL OR p.valid_from<=%s)) OR (p.published_at IS NULL AND p.inclusion_reason IN ('CURRENT_PROMO','START_DATE_OPEN_END'))
                          ORDER BY s.code,p.canonical_url''',(since,today,since,today))
    items = store.query('''SELECT s.code AS source,p.canonical_url AS campaign_url,i.* FROM competitor_campaign_items i
                          JOIN competitor_page_versions v ON v.id=i.page_version_id JOIN competitor_pages p ON p.id=v.page_id
                          JOIN competitor_sources s ON s.id=p.source_id WHERE v.content_hash=p.current_content_hash
                          ORDER BY CASE i.priority WHEN 'HIGH' THEN 0 WHEN 'NORMAL' THEN 1 ELSE 2 END,s.code,p.canonical_url,i.product_url,i.product_name,i.campaign_name,i.tab_title,i.id''')
    valid_ids = {p['version_id'] for p in pages}; items = [i for i in items if i['page_version_id'] in valid_ids]
    changes = []
    observations = store.query('''SELECT s.code AS source,p.canonical_url,o.observed_at,o.page_version_id,v.payload,v.extractor_version
                                 FROM competitor_observations o JOIN competitor_page_versions v ON v.id=o.page_version_id
                                 JOIN competitor_pages p ON p.id=o.page_id JOIN competitor_sources s ON s.id=p.source_id ORDER BY o.page_id,o.observed_at,o.id''')
    previous = {}
    for obs in observations:
        key = (obs['source'],obs['canonical_url']); prior = previous.get(key)
        if prior and prior['page_version_id'] != obs['page_version_id'] and prior['extractor_version']==obs['extractor_version']:
            def index(o): return {(i.get('product_url'),i.get('product_name'),i.get('campaign_name'),i.get('tab_title')):i for i in o['payload']['items']}
            a,b = index(prior),index(obs)
            for product in sorted(set(a)|set(b),key=str):
                old,new = a.get(product),b.get(product)
                kind = 'APPEARED' if old is None else 'DISAPPEARED' if new is None else 'PRICE_CHANGED'
                if old and new and all(old.get(f)==new.get(f) for f in ('old_price','new_price','price_type')): continue
                changes.append({'source':obs['source'],'campaign_url':obs['canonical_url'],'product_url':product[0],'product_name':product[1],
                                'change_type':kind,'previous_current_price':old.get('new_price') if old else None,'current_price':new.get('new_price') if new else None,
                                'previous_price_type':old.get('price_type') if old else None,'price_type':new.get('price_type') if new else None,'observed_at':obs['observed_at']})
        previous[key] = obs
    write_csv(out/'PAGES.csv',pages,['source','page_type','canonical_url','title','published_at','valid_from','valid_to','inclusion_reason','first_seen_at','last_seen_at','current_content_hash','raw_capture_ref','normalized_text'])
    columns = ['source','campaign_url','campaign_name','tab_title','product_name','product_url','brand','model','category','old_price','new_price','currency','discount_amount','discount_percent','displayed_discount_raw','price_type','price_label_raw','availability','region','priority','observed_at','warnings']
    write_csv(out/'CAMPAIGN_ITEMS.csv',items,columns)
    write_csv(out/'MACHINE_PRIORITY.csv',[i for i in items if i['priority']=='HIGH'],columns)
    write_csv(out/'PRICE_CHANGES.csv',changes,['source','campaign_url','product_url','product_name','change_type','previous_current_price','current_price','previous_price_type','price_type','observed_at'])
    run = store.query('SELECT id,result FROM competitor_runs WHERE finished_at IS NOT NULL AND result IS NOT NULL ORDER BY id DESC LIMIT 1')
    crawl_result = (run[0]['result'] or {}) if run else {}
    (out/'CRAWL_RESULT.json').write_text(json.dumps(crawl_result,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    source_map = crawl_result.get('source_map',[])
    lines = ['# Source map','', '| URL | Page type | Why useful | Crawl strategy | Date | Product/price | Selected | Transport / outcome |','|---|---|---|---|---|---|---|---|']
    for s in source_map:
        mapped={**s,'selected':'NO' if s.get('error') else 'YES','transport':s.get('error') or s.get('transport','UNKNOWN')}
        lines.append('| '+' | '.join(str(mapped.get(k,'UNKNOWN')).replace('|','/') for k in ['url','page_type','why','crawl_strategy','date_support','product_price_support','selected','transport'])+' |')
    lines += ['', 'All catalog product links: NO detail crawl. Archive pagination: NO. Regional fallback: NO.', 'Kuvalda seasonal block: all six winter tabs captured. Other tabbed blocks: initial visible tab only.', 'Kuvalda sale navigation: two machine categories only; other category links discovered but not selected.']
    (out/'SOURCE_MAP.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    lines = ['# Competitor intelligence','',f'Scope: {since} → {today}. LIVE-VERIFIED public observations; independent local DB. AI: disabled, network calls 0.','',
             '| Source | Promotions | News | Campaigns/blocks | Product observations | Old + new | HIGH | Errors | Blocked |','|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    for source in sorted({p['source'] for p in pages}|set(crawl_result.get('sources',{}))):
        p = [x for x in pages if x['source']==source]; i = [x for x in items if x['source']==source]; c = crawl_result.get('sources',{}).get(source,{})
        values = [source,sum(x['page_type']=='PROMOTION' for x in p),sum(x['page_type']=='NEWS' for x in p),sum(x['page_type'] in ('CAMPAIGN','HOMEPAGE_BLOCK','SALE_PAGE') for x in p),len(i),sum(x['old_price'] is not None and x['new_price'] is not None for x in i),sum(x['priority']=='HIGH' for x in i),c.get('errors',0),c.get('blocked',0)]
        lines.append('| '+' | '.join(map(str,values))+' |')
    discounts = [i for i in items if i['old_price'] and i['new_price'] and i['price_type']=='SALE']
    # Unique competitor+regional product to avoid weighting repeated campaign cards.
    unique = {(i['source'],i['region'],i['product_url'] or i['product_name']):i for i in discounts}
    percentages = [i['discount_percent'] for i in unique.values()]
    lines += ['', '## COMPETITOR SIGNALS WITHOUT AI','',f'- Public SALE products with evidenced old/new prices: {len(unique)}.',f'- Price/product transitions in accumulated history: {len(changes)}.']
    if percentages:
        lines += [f'- Calculated discount mean / median: {mean(percentages):.2f}% / {median(percentages):.2f}%. Source displayed discount remains separately preserved.', f'- Largest calculated discount: {max(percentages):.2f}%.']
    for p in pages:
        if p['valid_to'] and today<=p['valid_to']<=today+timedelta(days=14): lines.append(f"- Ending within 14 days: {p['title']} ({p['valid_to']}).")
    lines += ['', 'No inferred model identities, historical backfill prices, competitor→Sterbrust matching or AI interpretation.', 'Current prices are observation-time facts, not reconstructed September prices.', 'Unknown campaign start/publication dates remain NULL. Missing cards are not evidence of campaign expiration.', '', '## Evidence / QA','', 'Raw captures linked in PAGES.csv and persisted observations; price-role samples in QA_RESULT.json.', 'Repeated live pages may change legitimately; inspect recorded semantic deltas rather than assuming unchanged sources.']
    running=store.query("SELECT id FROM competitor_runs WHERE status='RUNNING' ORDER BY id")
    if running: lines += ['',f'PARTIAL SNAPSHOT: runs still active: {[r["id"] for r in running]}. Latest completed crawl metadata is used; full report acceptance deferred until completion.']
    (out/'REPORT.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    return {'pages':len(pages),'items':len(items),'changes':len(changes)}
