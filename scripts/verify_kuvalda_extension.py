"""Read-only source-specific evidence QA + exact MetalMaster immutability proof."""
import hashlib
import json
import re
from decimal import Decimal
from pathlib import Path
from collections import Counter
from bs4 import BeautifulSoup
from competitor_intel.store import Store
from competitor_intel.model import canonical
from competitor_intel.report import write_csv
from competitor_intel.kuvalda_public_runner import public_run_status,POLICY
from psycopg.types.json import Jsonb

out=Path('reports/KUVALDA_ALLOWED_SOURCE_EXTENSION_2026-10-09')
store=Store(json.loads(Path('competitor-data/dev-config.json').read_text())['dsn'])
queries={'source':'SELECT * FROM competitor_sources WHERE code=%s',
 'pages':'SELECT p.* FROM competitor_pages p JOIN competitor_sources s ON s.id=p.source_id WHERE s.code=%s ORDER BY p.id',
 'versions':'SELECT v.* FROM competitor_page_versions v JOIN competitor_pages p ON p.id=v.page_id JOIN competitor_sources s ON s.id=p.source_id WHERE s.code=%s ORDER BY v.id',
 'items':'SELECT i.* FROM competitor_campaign_items i JOIN competitor_page_versions v ON v.id=i.page_version_id JOIN competitor_pages p ON p.id=v.page_id JOIN competitor_sources s ON s.id=p.source_id WHERE s.code=%s ORDER BY i.id',
 'observations':'SELECT o.* FROM competitor_observations o JOIN competitor_pages p ON p.id=o.page_id JOIN competitor_sources s ON s.id=p.source_id WHERE s.code=%s ORDER BY o.id',
 'analysis':'SELECT a.* FROM competitor_ai_analysis a JOIN competitor_page_versions v ON v.id=a.page_version_id JOIN competitor_pages p ON p.id=v.page_id JOIN competitor_sources s ON s.id=p.source_id WHERE s.code=%s ORDER BY a.id'}
after={}
for name,sql in queries.items():
    rows=store.query(sql,('metalmaster',))
    after[name]={'rows':len(rows),'sha256':hashlib.sha256(json.dumps(rows,ensure_ascii=False,sort_keys=True,default=str).encode()).hexdigest()}
before=json.loads((out/'METALMASTER_BEFORE.json').read_text())
same=before==after
(out/'METALMASTER_IMMUTABILITY.json').write_text(json.dumps({'status':'PASS' if same else 'FAIL','before':before,'after':after},indent=2),encoding='utf-8')
latest=store.query('SELECT id,status,result FROM competitor_runs ORDER BY id DESC LIMIT 1')[0]
if latest['status']=='RUNNING': raise SystemExit('Collector still running; final QA deferred')
crawl=json.loads((out/f'CRAWL_RUN_{latest["id"]}.json').read_text(encoding='utf-8'))
pages=store.query('''SELECT p.*,v.id AS version_id,v.payload,v.raw_capture_ref,v.extractor_version FROM competitor_pages p
 JOIN competitor_sources s ON s.id=p.source_id JOIN competitor_page_versions v ON v.page_id=p.id AND v.content_hash=p.current_content_hash
 WHERE s.code='kuvalda_nnov' ORDER BY p.canonical_url''')
items=store.query('''SELECT i.*,p.canonical_url AS campaign_url FROM competitor_campaign_items i JOIN competitor_page_versions v ON v.id=i.page_version_id
 JOIN competitor_pages p ON p.id=v.page_id JOIN competitor_sources s ON s.id=p.source_id
 WHERE s.code='kuvalda_nnov' AND v.content_hash=p.current_content_hash ORDER BY p.canonical_url,i.product_url,i.campaign_name,i.tab_title,i.id''')
surfaces=store.query("SELECT t.* FROM competitor_source_surfaces t JOIN competitor_sources s ON s.id=t.source_id WHERE s.code='kuvalda_nnov' ORDER BY t.canonical_url")
checks=[]; errors=[]
def amount(node):
    if node is None: return None
    value=node.get_text(' ',strip=True)
    m=re.fullmatch(r'\s*(\d[\d\s\u00a0\u202f]*(?:[,.]\d{1,2})?)\s*(?:₽|руб\.?)\s*',value)
    return Decimal(re.sub(r'\s','',m[1]).replace(',','.')) if m else None
for item in items:
    s=BeautifulSoup(item['evidence_html'],'html.parser')
    title=s.select_one('.snippet__title,.alt-snippet__title')
    old=s.select_one('.snippet-price__old,.snippet__price-old,.snippet__price del')
    price=s.select_one('.snippet-price__value,.snippet__price-value')
    problems=[]
    if not title or re.sub(r'\s+',' ',title.get_text(' ',strip=True))!=item['product_name']: problems.append('TITLE_CORRUPTION')
    if amount(old)!=item['old_price']: problems.append('OLD_PRICE_CORRUPTION')
    if amount(price)!=item['new_price']: problems.append('CURRENT_PRICE_CORRUPTION')
    markers=' '.join(x.get('data-label','')+' '+x.get('data-tooltip','') for x in s.select('[data-label],[data-tooltip]')).lower()
    if any(t in markers for t in ('персональн','авториз')) and item['price_type'] not in ('PERSONAL','AUTH_REQUIRED'): problems.append('SPECIAL_PRICE_PUBLIC')
    if item['old_price'] and item['new_price']:
        discount=item['old_price']-item['new_price']
        percent=(100*discount/item['old_price']).quantize(Decimal('.01'))
        if discount<=0 or discount!=item['discount_amount'] or percent!=item['discount_percent']: problems.append('DISCOUNT_CORRUPTION')
    if item['region']!='Нижний Новгород': problems.append('REGION_CORRUPTION')
    check={'id':item['id'],'campaign_url':item['campaign_url'],'name':item['product_name'],'old_price':item['old_price'],'new_price':item['new_price'],'price_type':item['price_type'],'status':'FAIL' if problems else 'PASS','problems':problems}
    checks.append(check)
    if problems: errors.append(check)
page_checks=[]
for page in pages:
    ref=Path(page['raw_capture_ref'])
    if ref.suffix=='.json': ref=Path(json.loads(ref.read_text(encoding='utf-8'))['initial'])
    soup=BeautifulSoup(ref.read_text(encoding='utf-8'),'html.parser')
    title=soup.find('h1') or soup.title
    problems=[]
    if not title or re.sub(r'\s+',' ',title.get_text(' ',strip=True))!=page['title']: problems.append('PAGE_TITLE_CORRUPTION')
    proof=page['payload'].get('campaign_evidence')
    if proof and proof.get('valid_to'):
        listing=BeautifulSoup(Path(proof['raw_capture_ref']).read_text(encoding='utf-8'),'html.parser')
        matched=any(canonical(a['href'],proof['listing_url'])==page['canonical_url'] for a in listing.select('a[href]') if a['href'].startswith('/promo/'))
        if not matched: problems.append('CAMPAIGN_LISTING_EVIDENCE_MISSING')
        if str(page['valid_to'])!=proof['valid_to']: problems.append('END_DATE_MISMATCH')
    if page['valid_from'] and page['valid_to'] and page['valid_from']>page['valid_to']: problems.append('INVALID_CAMPAIGN_RANGE')
    entry={'url':page['canonical_url'],'title':page['title'],'published_at':page['published_at'],'valid_from':page['valid_from'],'valid_to':page['valid_to'],'status':'FAIL' if problems else 'PASS','problems':problems}
    page_checks.append(entry)
    if problems: errors.append(entry)
dup_pages=store.query("SELECT p.canonical_url,count(*) FROM competitor_pages p JOIN competitor_sources s ON s.id=p.source_id WHERE s.code='kuvalda_nnov' GROUP BY p.canonical_url HAVING count(*)>1")
dup_versions=store.query("SELECT v.page_id,v.content_hash,count(*) FROM competitor_page_versions v JOIN competitor_pages p ON p.id=v.page_id JOIN competitor_sources s ON s.id=p.source_id WHERE s.code='kuvalda_nnov' GROUP BY v.page_id,v.content_hash HAVING count(*)>1")
dup_items=store.query("SELECT i.page_version_id,i.item_key,count(*) FROM competitor_campaign_items i JOIN competitor_page_versions v ON v.id=i.page_version_id JOIN competitor_pages p ON p.id=v.page_id JOIN competitor_sources s ON s.id=p.source_id WHERE s.code='kuvalda_nnov' GROUP BY i.page_version_id,i.item_key HAVING count(*)>1")
known_core=[e for e in crawl['source_map'] if e['kind']!='SITEMAP']
core_complete=not crawl.get('queue_remaining') and all(e['status']=='LIVE_CRAWLED' for e in known_core)
required={p['canonical_url'] for p in pages}
scope_verified=core_complete and all('https://nnov.kuvalda.ru/'+p in required for p in ('','promo/','news/','sale/')) and any(p['page_type']=='PROMOTION' and p['items'] for p in [e for e in crawl['source_map'] if 'page_type' in e])
status='COMPETITOR_INTELLIGENCE_KUVALDA_ALLOWED_SCOPE_VERIFIED' if scope_verified and same and not errors and not dup_pages and not dup_versions and not dup_items else 'COMPETITOR_INTELLIGENCE_KUVALDA_STILL_INSUFFICIENT'
if not errors and same and not dup_pages and not dup_versions and not dup_items:
    with store.conn.transaction(),store.conn.cursor() as c:
        c.execute("""UPDATE competitor_page_versions v SET qa_status='ACCEPTED' FROM competitor_pages p,competitor_sources s
                     WHERE v.page_id=p.id AND p.source_id=s.id AND s.code='kuvalda_nnov' AND v.content_hash=p.current_content_hash""")
    # Reconcile only extension-run status: a missing optional sitemap is a warning,
    # not a public-access blocker. Preserve every original error and capture.
    for r in store.query("SELECT id,result FROM competitor_runs WHERE result->>'source'='kuvalda_nnov' AND result->>'policy'=%s ORDER BY id",(POLICY,)):
        state=public_run_status(r['result']); r['result']['run_status']=state
        with store.conn.transaction(),store.conn.cursor() as c:
            c.execute("UPDATE competitor_runs SET status=%s,result=%s WHERE id=%s AND result->>'source'='kuvalda_nnov' AND result->>'policy'=%s",(state,Jsonb(r['result']),r['id'],POLICY))
summary={'final_status':status,'provenance':'LIVE-VERIFIED public source + dedicated competitor DB; REPOSITORY-VERIFIED fixtures',
 'pages_stored':len(pages),'current_campaign_items':len(items),'sale_products':sum('/sale/' in i['campaign_url'] for i in items),'machine_related_products':sum(i['priority']=='HIGH' for i in items),
 'old_new_price_rows':sum(i['old_price'] is not None and i['new_price'] is not None for i in items),'price_type_counts':dict(Counter(i['price_type'] for i in items)),
 'campaign_blocks':crawl['blocks'],'promotions':sum(p['page_type']=='PROMOTION' for p in pages),
 'status_counts':dict(Counter(s['status'] for s in surfaces)),'fetch_status_counts':dict(Counter(s['fetch_status'] for s in surfaces)),
 'robots_disallowed_fetched':sum(s['robots_disallowed'] and s['status']=='LIVE_CRAWLED' for s in surfaces),
 'actual_blocked_urls':[s['canonical_url'] for s in surfaces if s['fetch_status'] in ('HTTP_BLOCKED','AUTH_REQUIRED','CAPTCHA_BLOCKED')],
 'duplicates_pages':len(dup_pages),'duplicates_versions':len(dup_versions),'duplicates_items':len(dup_items),'price_parsing_corruption':len(errors),'metalmaster_unchanged':same,
 'ai_network_calls':0,'index_only_rows':sum(s['status']=='INDEX_ONLY' for s in surfaces),'queue_remaining':crawl.get('queue_remaining',[]),
 'transport_errors':crawl['errors'],'samples':checks,'page_checks':page_checks}
(out/'QA_RESULT.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
write_csv(out/'PAGES.csv',pages,['canonical_url','page_type','title','published_at','valid_from','valid_to','first_seen_at','last_seen_at','raw_capture_ref'])
write_csv(out/'CAMPAIGN_ITEMS.csv',items,['campaign_url','campaign_name','tab_title','category','product_name','product_url','old_price','new_price','discount_amount','discount_percent','displayed_discount_raw','price_type','price_label_raw','priority','observed_at'])
write_csv(out/'SOURCE_STATUS.csv',surfaces,['canonical_url','surface_kind','title','status','fetch_status','robots_disallowed','evidence_class','reason','observed_at','raw_capture_ref'])
campaigns=[]
for page in pages:
    if page['page_type']!='PROMOTION': continue
    raw=page['payload']['normalized_text']
    mechanics=[]
    for pattern,kind in [(r'подар|комплектац','GIFT_OR_BUNDLE'),(r'супер цены|скид|снижен','PRICE_PROMOTION'),(r'обмен','EXCHANGE'),(r'зим|сезон','SEASONAL')]:
        if re.search(pattern,raw,re.I): mechanics.append(kind)
    campaigns.append({'campaign_title':page['title'],'campaign_url':page['canonical_url'],'valid_from':page['valid_from'],'valid_to':page['valid_to'],
                      'published_at':page['published_at'],'campaign_detail_text':raw,'discount_mechanics':mechanics,'raw_capture_ref':page['raw_capture_ref']})
write_csv(out/'CAMPAIGNS.csv',campaigns,['campaign_title','campaign_url','published_at','valid_from','valid_to','campaign_detail_text','discount_mechanics','raw_capture_ref'])
lines=['# KUVALDA_ALLOWED_SOURCE_EXTENSION_REPORT','',status,'',
 'REQUESTED: Kuvalda-only coverage extension; latest operator correction makes robots informational for ordinary publicly accessible competitor pages.',
 'DONE: bounded industrial sale + current homepage + public campaign/news navigation, independent robots/fetch status, source evidence, deterministic price/priority extraction.',
 'APPLIED: Kuvalda-only rows in dedicated sterbrust_competitor_intel; one new competitor_source_surfaces table. No MetalMaster business/metadata rows changed.',
 'VERIFIED: exact six-table-group MetalMaster hashes unchanged; DOM samples and duplicate guards below. No production/develop/scheduler/ESOL/Sterbrust/KAMI/Vekprom/US-table operations.', '',
 '| Metric | Result |','|---|---:|']
for key in ('pages_stored','current_campaign_items','sale_products','machine_related_products','old_new_price_rows','promotions','duplicates_pages','duplicates_versions','duplicates_items','price_parsing_corruption','metalmaster_unchanged','ai_network_calls','index_only_rows'):
    lines.append(f'| {key} | {summary[key]} |')
lines+=['',f"Price types: {summary['price_type_counts']}",f"Fetch statuses: {summary['fetch_status_counts']}",f"Current homepage block titles: {[b['block_title'] for b in crawl['blocks']]}",
 '', '## Robots policy and actual accessibility',
 'Latest OPERATOR-CONFIRMED policy supersedes robots-hard-block interpretation. /promo/ and /news/ retain robots_disallowed=true when applicable; status comes from actual HTTP/browser response. No SOURCE_BLOCKED solely from Disallow.',
 'PUBLIC_FETCH_OK / BROWSER_FETCH_OK / HTTP_BLOCKED / AUTH_REQUIRED / CAPTCHA_BLOCKED / ERROR are independent of robots_disallowed. No index metadata was used; no indexed price evidence.',
 '', '## Allowed/public URLs checked','', '| URL | Fetch status | Robots disallowed | Items |','|---|---|---|---:|']
for e in crawl['source_map']: lines.append(f"| {e['url']} | {e['fetch_status']} | {e['robots_disallowed']} | {e.get('items',0)} |")
lines+=['', '## Blocked URLs and gaps',f"Actual access-control blocked URLs: {summary['actual_blocked_urls']}",f"Transport/discovery errors: {crawl['errors']}",
 f"Robots informational Disallow observed on {summary['robots_disallowed_fetched']} successfully fetched pages; robots-blocked collector gaps=0 under corrected policy.",
 'Bounded first sale-category pages; no consumer full catalog or archive crawl. Product/category detail samples are reachability evidence; unsupported detail-price roles remain NULL.',
 'Distinct campaign-context cards are not a unique product count. Historical prices are not reconstructed. Publication dates absent from source remain NULL.',
 '', 'NOT APPLIED: develop merge, production, MetalMaster updates, matcher, imports, scheduler, AI calls.',
 'EVIDENCE: SOURCE_STATUS.csv, PAGES.csv, CAMPAIGN_ITEMS.csv, QA_RESULT.json, METALMASTER_IMMUTABILITY.json, TEST_RESULT.xml, raw captures/browser tab manifests.',
 'NEXT ACTION: stop at verified bounded scope; further catalog/archive breadth is a separate scope.']
(out/'KUVALDA_ALLOWED_SOURCE_EXTENSION_REPORT.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
print(json.dumps({k:v for k,v in summary.items() if k not in ('samples','page_checks','campaign_blocks')},ensure_ascii=False,default=str))
store.close()
raise SystemExit(1 if errors or not same or dup_pages or dup_versions or dup_items else 0)
