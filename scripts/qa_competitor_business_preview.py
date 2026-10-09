"""Native Edge file:// QA. All HTTP(S) requests aborted; no competitor refresh."""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

out=Path('reports/COMPETITOR_BUSINESS_REPORT_PREVIEW_2026-10-09').resolve()
url=(out/'index.html').as_uri()
result={'transport':'ordinary Edge / file:// / offline','source_HTTP':0,'checks':[],'blocked_remote_attempts':[]}
def check(name,ok,details=None):
    result['checks'].append({'name':name,'status':'PASS' if ok else 'FAIL','details':details})

with sync_playwright() as p:
    browser=p.chromium.launch(channel='msedge',headless=True)
    context=browser.new_context(viewport={'width':1440,'height':1100})
    def route(r):
        if r.request.url.startswith(('http://','https://')):
            result['blocked_remote_attempts'].append(r.request.url); r.abort()
        else:r.continue_()
    context.route('**/*',route)
    page=context.new_page(); errors=[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto(url,wait_until='load'); page.wait_for_timeout(300)
    check('desktop_layout_no_document_horizontal_overflow',page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
    check('javascript_errors_absent',not errors,errors)
    check('russian_title',page.locator('h1').inner_text()=='Конкурентная разведка STERBRUST')
    check('temporal_changes_separate_from_site_discount',page.locator('.price-table th').last.inner_text().startswith('Изменение между') and all(t=='Нет сравнения' for t in page.locator('.temporal-change').all_text_contents()))
    check('initial_baseline_visible','INITIAL BASELINE' in page.locator('#baseline').inner_text())
    visible=page.locator('body').inner_text()
    check('no_visual_NaN_null_None',not any(t in visible for t in ('NaN','None','null','Invalid Date')))
    check('no_remote_resources_loaded',len(result['blocked_remote_attempts'])==0)
    rows=page.locator('#price-body tr')
    price_samples=[]
    for row in rows.all()[:10]:
        price_samples.append({'name':row.locator('.product-title').inner_text(),'old':row.locator('td').nth(3).inner_text(),'current':row.locator('td').nth(4).inner_text(),
                              'type':row.locator('td').nth(6).inner_text(),'date':row.locator('td').nth(8).inner_text()})
    check('ten_price_rows_reviewed',len(price_samples)==10 and all('₽' in s['current'] for s in price_samples),price_samples)
    check('five_promotions_rendered',page.locator('.promo').count()>=5,[page.locator('.promo-name').nth(i).inner_text() for i in range(min(5,page.locator('.promo').count()))])
    source_blocks=page.locator('.source-card').all_text_contents()
    check('all_source_summaries',len(source_blocks)==2 and any('MetalMaster' in t for t in source_blocks) and any('Кувалда' in t for t in source_blocks),source_blocks)
    page.screenshot(path=str(out/'DESKTOP_TOP.png'),full_page=False)
    page.screenshot(path=str(out/'DESKTOP.png'),full_page=True)
    page.locator('#priority-filter').select_option('HIGH')
    high=page.locator('#price-body tr').all()
    check('five_high_industrial_rows',len(high)>=5 and all(r.get_attribute('data-priority')=='HIGH' for r in high),[r.locator('.product-title').inner_text() for r in high[:5]])
    page.locator('#source-filter').select_option('metalmaster')
    check('competitor_filter',all(r.get_attribute('data-source')=='metalmaster' for r in page.locator('#price-body tr').all()))
    page.locator('#search').fill('XNC')
    check('search_filter',page.locator('#price-body tr .product-title').count()>0 and all('XNC' in t.upper() for t in page.locator('#price-body tr .product-title').all_text_contents()))
    page.locator('#search').fill('');page.locator('#source-filter').select_option('ALL');page.locator('#priority-filter').select_option('ALL')
    page.locator('#type-filter').select_option('AUTH_REQUIRED')
    check('special_price_filter',all('После авторизации' in r.locator('td').nth(6).inner_text() for r in page.locator('#price-body tr').all()))
    page.locator('#type-filter').select_option('ALL');page.locator('#discount-filter').select_option('20')
    check('discount_filter',all('После авторизации' not in r.locator('td').nth(6).inner_text() for r in page.locator('#price-body tr').all()) and page.locator('#price-body tr .product-title').count()>0)
    page.locator('#discount-filter').select_option('ALL');page.locator('#sort').select_option('price')
    price_sorted=page.evaluate("Array.from(document.querySelectorAll('#price-body .new')).map(e=>Number(e.textContent.replace(/[^0-9,]/g,'').replace(',','.')))")
    check('price_sort',price_sorted==sorted(price_sorted),price_sorted[:10])
    page.locator('#sort').select_option('discount')
    discounts=page.evaluate("Array.from(document.querySelectorAll('#price-body td.saving div')).map(e=>Number(e.textContent.replace(/[^0-9,]/g,'').replace(',','.')))")
    check('max_discount_sort',discounts==sorted(discounts,reverse=True),discounts[:10])
    page.locator('#promo-source').select_option('metalmaster')
    check('promotion_source_filter',page.locator('.promo').count()==2 and all('MetalMaster' in t for t in page.locator('.promo').all_text_contents()))
    page.locator('#promo-source').select_option('ALL');page.locator('#promo-status').select_option('ENDED')
    check('promotion_status_filter',page.locator('.promo').count()>=1 and all('Завершена' in t for t in page.locator('.promo').all_text_contents()))
    page.locator('#promo-status').select_option('ALL')
    first_details=page.locator('.promo details').first
    first_details.locator('summary').click()
    check('campaign_products_expand',first_details.get_attribute('open') is not None and first_details.locator('li').count()>0)
    all_links=page.locator('a[target="_blank"]').all()
    check('canonical_external_links_safe',bool(all_links) and all(a.get_attribute('href').startswith(('https://','http://')) and 'utm_' not in a.get_attribute('href') and 'noopener' in (a.get_attribute('rel') or '') for a in all_links))
    with context.expect_page() as opened:
        page.locator('#price-body a[target="_blank"]').first.click()
    popup=opened.value;popup.wait_for_timeout(150)
    check('external_link_opens_new_tab',len(context.pages)==2 and len(result['blocked_remote_attempts'])>=1)
    popup.close()
    page.set_viewport_size({'width':390,'height':844});page.goto(url,wait_until='load')
    check('mobile_no_document_horizontal_overflow',page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
    page.screenshot(path=str(out/'MOBILE_TOP.png'),full_page=False)
    page.screenshot(path=str(out/'MOBILE.png'),full_page=True)
    browser.close()
result['status']='PASS' if all(c['status']=='PASS' for c in result['checks']) else 'FAIL'
(out/'BROWSER_QA.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'status':result['status'],'checks':len(result['checks']),'failed':[c for c in result['checks'] if c['status']=='FAIL']},ensure_ascii=False))
raise SystemExit(1 if result['status']=='FAIL' else 0)
