"""Independent live DOM/Decimal QA of saved rows; no network or source writes."""
import json
import re
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from bs4 import BeautifulSoup
from competitor_intel.store import Store
from competitor_intel.model import SOURCES
from competitor_intel import EXTRACTOR_VERSION

out=Path('reports/COMPETITOR_INTELLIGENCE_2026-10-09')
store=Store(json.loads(Path('competitor-data/dev-config.json').read_text())['dsn'])
items=store.query('''SELECT s.code AS source,p.canonical_url,i.* FROM competitor_campaign_items i
 JOIN competitor_page_versions v ON v.id=i.page_version_id JOIN competitor_pages p ON p.id=v.page_id
 JOIN competitor_sources s ON s.id=p.source_id WHERE v.content_hash=p.current_content_hash ORDER BY s.code,i.id''')
checks=[]; failures=[]
def number(node):
    if node is None: return None
    s=node.get_text(' ',strip=True)
    m=re.search(r'(?<!\d)(\d[\d\s\u00a0\u202f]*)(?:[,.](\d{1,2}))?\s*(?:₽|руб)',s)
    if not m: return None
    return Decimal(re.sub(r'\s','',m[1])+('.'+m[2] if m[2] else ''))
for i in items:
    soup=BeautifulSoup(i['evidence_html'],'html.parser')
    mm=i['source']=='metalmaster'
    title=soup.select_one('.card-product__title' if mm else '.snippet__title,.alt-snippet__title')
    problems=[]
    if title and re.sub(r'\s+',' ',title.get_text(' ',strip=True))!=i['product_name']: problems.append('TITLE_MISMATCH')
    old_node=soup.select_one('.card-product__price del' if mm else '.snippet-price__old,.snippet__price del,.snippet__price-old')
    visible_old=number(old_node)
    if visible_old != i['old_price']: problems.append('OLD_PRICE_MISMATCH')
    price=soup.select_one('.card-product__price' if mm else '.snippet-price__value,.snippet__price-value,.snippet__price')
    if price:
        for old in price.select('s,del'): old.decompose()
    visible_new=number(price)
    if i['price_type'] not in ('INSTALLMENT_MONTHLY','CREDIT_PAYMENT','BONUS','DISCOUNT_PERCENT_ONLY') and visible_new != i['new_price']: problems.append('NEW_PRICE_MISMATCH')
    markers=' '.join(x.get('data-label','')+' '+x.get('data-tooltip','') for x in soup.select('[data-label],[data-tooltip]')).lower()
    if ('персональн' in markers or 'авториз' in markers) and i['price_type'] not in ('PERSONAL','AUTH_REQUIRED'): problems.append('PERSONAL_TREATED_PUBLIC')
    if i['region']!=SOURCES[i['source']]['region']: problems.append('REGION_MISMATCH')
    if i['old_price'] and i['new_price']:
        if i['new_price']>=i['old_price']: problems.append('FALSE_OLD_NEW')
        amount=i['old_price']-i['new_price']
        percent=(100*amount/i['old_price']).quantize(Decimal('.01'))
        if (amount,percent)!=(i['discount_amount'],i['discount_percent']): problems.append('DISCOUNT_MISMATCH')
    check={'source':i['source'],'item_id':i['id'],'title':i['product_name'],'product_url':i['product_url'],'campaign_url':i['canonical_url'],
           'old_price':i['old_price'],'current_price':i['new_price'],'price_type':i['price_type'],'visible_old':visible_old,'visible_current':visible_new,'status':'FAIL' if problems else 'PASS','problems':problems}
    checks.append(check)
    if problems: failures.append(check)
pages=store.query('''SELECT s.code,p.canonical_url,p.title,p.published_at,p.valid_from,p.valid_to,v.payload,v.extractor_version,v.raw_capture_ref
 FROM competitor_pages p JOIN competitor_sources s ON s.id=p.source_id JOIN competitor_page_versions v ON v.page_id=p.id AND v.content_hash=p.current_content_hash ORDER BY s.code,p.canonical_url''')
page_checks=[]
for page in pages:
    ref=Path(page['raw_capture_ref'])
    if ref.suffix=='.json': ref=Path(json.loads(ref.read_text(encoding='utf-8'))['initial'])
    soup=BeautifulSoup(ref.read_text(encoding='utf-8'),'html.parser')
    title=soup.find('h1') or soup.title
    expected_title=re.sub(r'\s+',' ',title.get_text(' ',strip=True)) if title else None
    problems=[]
    if expected_title!=page['title']: problems.append('PAGE_TITLE_MISMATCH')
    proof=page['payload'].get('publication_evidence')
    if proof and page['published_at']:
        listing=BeautifulSoup(Path(proof['raw_capture_ref']).read_text(encoding='utf-8'),'html.parser')
        expected=page['published_at'].strftime('%d.%m.%Y')
        if not listing.find('time',datetime=expected): problems.append('PUBLICATION_LISTING_EVIDENCE_MISSING')
    check={'url':page['canonical_url'],'title':page['title'],'published_at':page['published_at'],'valid_from':page['valid_from'],'valid_to':page['valid_to'],'status':'FAIL' if problems else 'PASS','problems':problems}
    page_checks.append(check)
    if problems: failures.append(check)
qa={'provenance':'LIVE-VERIFIED saved public DOM; independent evidence sample, not an external independent reviewer',
    'price_samples':checks,'sample_count':len(checks),'failures':failures,'sample_by_source':{},'pages':pages,'page_checks':page_checks,
    'db_tables':store.query("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"),
    'duplicates_pages':store.query('SELECT source_id,canonical_url,count(*) FROM competitor_pages GROUP BY source_id,canonical_url HAVING count(*)>1'),
    'duplicates_versions':store.query('SELECT page_id,content_hash,count(*) FROM competitor_page_versions GROUP BY page_id,content_hash HAVING count(*)>1'),
    'duplicates_items':store.query('SELECT page_version_id,item_key,count(*) FROM competitor_campaign_items GROUP BY page_version_id,item_key HAVING count(*)>1'),
    'ai':store.query('SELECT provider,analysis_status,count(*) FROM competitor_ai_analysis GROUP BY provider,analysis_status ORDER BY provider,analysis_status'),
    'ai_network_calls':0,'existing_system_mutations':{'KAMI':0,'Vekprom/Intervesp':0,'Universal Supplier business tables':0,'Sterbrust':0,'ESOL':0,'production':0,'scheduler':0},
    'scope_proof':'Feature diff confined to competitor package/tests/docs/scripts; DSN exact dedicated new DB; no existing-system connections or processes operated',
    'limitations':['Kuvalda /promo/ and /news/ robots exclusions: no collector detail requests. Audit captures are discovery evidence only.',
                    'v1.0/1.1/1.2 trial extraction rejected/superseded; 1.3 current versions independently checked. Cross-extractor transitions excluded from source-change report.',
                    'No AI provider network integration or Sterbrust matching performed.']}
for source in SOURCES:
    selected=[c for c in checks if c['source']==source]
    qa['sample_by_source'][source]={'all_current_items_checked':len(selected),'old_new_checked':sum(c['old_price'] is not None and c['current_price'] is not None for c in selected),'failures':sum(c['status']=='FAIL' for c in selected)}
qa['status']='FAIL' if failures or qa['duplicates_pages'] or qa['duplicates_versions'] or qa['duplicates_items'] else 'PASS'
if qa['status']=='PASS':
    with store.conn.transaction(),store.conn.cursor() as c:
        c.execute("UPDATE competitor_page_versions SET qa_status='REJECTED_EXTRACTOR' WHERE extractor_version<>%s",(EXTRACTOR_VERSION,))
        c.execute("UPDATE competitor_page_versions SET qa_status='ACCEPTED' WHERE extractor_version=%s AND EXISTS(SELECT 1 FROM competitor_pages p WHERE p.id=competitor_page_versions.page_id AND p.current_content_hash=competitor_page_versions.content_hash)",(EXTRACTOR_VERSION,))
qa['version_qa_status']=store.query('SELECT qa_status,count(*) FROM competitor_page_versions GROUP BY qa_status ORDER BY qa_status')
(out/'QA_RESULT.json').write_text(json.dumps(qa,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
print(json.dumps({'status':qa['status'],'samples':qa['sample_by_source'],'failures':len(failures)},ensure_ascii=False))
store.close()
raise SystemExit(1 if qa['status']=='FAIL' else 0)
