"""Native localhost UI visual QA; abort every non-local HTTP request."""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright
root=Path('reports/COMPETITOR_MONITORING_UI_LOCAL_2026-10-12');result={'checks':[],'blocked_nonlocal':[],'page_errors':[],'source_HTTP':0}
def check(name,ok):result['checks'].append({'name':name,'status':'PASS' if ok else 'FAIL'})
with sync_playwright() as p:
    b=p.chromium.launch(channel='msedge',headless=True);ctx=b.new_context(viewport={'width':1920,'height':1080})
    def route(r):
        if r.request.url.startswith('http://127.0.0.1:58124/'):r.continue_()
        else:result['blocked_nonlocal'].append(r.request.url);r.abort()
    ctx.route('**/*',route);page=ctx.new_page();page.on('pageerror',lambda e:result['page_errors'].append(str(e)))
    for width,height in [(1920,1080),(1366,768)]:
        page.set_viewport_size({'width':width,'height':height});page.goto('http://127.0.0.1:58124/',wait_until='networkidle')
        check(f'{width}_native_tab',page.locator('header nav a[href="/competitor-monitoring"]').count()==1)
        check(f'{width}_no_horizontal_document_overflow',page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
        check(f'{width}_six_source_cards',page.locator('.mon-source').count()==6)
        manual=page.locator('.mon-source[data-source="youmet"]').inner_text();check(f'{width}_youmet_manual','Ручной мониторинг' in manual and 'Снимок не предоставлен' in manual)
        page.screenshot(path=str(root/f'OVERVIEW_{width}.png'),full_page=True)
    for tab in ('changes','prices','promotions','assortment','sources'):
        page.goto('http://127.0.0.1:58124/competitor-monitoring/'+tab,wait_until='networkidle')
        check(tab+'_renders',page.locator('.monitoring h2').inner_text()=='Мониторинг конкурентов')
        check(tab+'_no_document_overflow',page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
        page.screenshot(path=str(root/(tab.upper()+'.png')),full_page=True)
    page.goto('http://127.0.0.1:58124/competitor-monitoring/sources?source=youmet')
    text=page.locator('.mon-source-detail').inner_text();check('manual_capture_not_provided','HTTP 403' in text and 'Не предоставлен' in text and 'Не подтверждён' in text)
    check('upload_deliberately_disabled',page.get_by_role('button',name='Импортировать HTML').is_disabled())
    page.goto('http://127.0.0.1:58124/competitor-monitoring/sources?source=stanki_ru')
    check('kami_source_working', 'Работает' in page.locator('.mon-status').inner_text());page.locator('summary').click()
    check('kami_page_404_preserved','HTTP 404 подтверждён' in page.locator('.mon-source-detail').inner_text())
    page.goto('http://127.0.0.1:58124/competitor-monitoring/changes');page.select_option('select[name="source"]','youmet');page.get_by_role('button',name='Показать').click()
    check('filter_submitted_and_empty_state','source=youmet' in page.url and 'Изменений за выбранный период нет' in page.locator('.monitoring').inner_text())
    page.goto('http://127.0.0.1:58124/competitor-monitoring/changes?period=custom&from=bad&to=bad')
    check('validation_error_readable_no_traceback','корректный период' in page.locator('.monitoring').inner_text() and 'Traceback' not in page.locator('body').inner_text())
    check('no_iframe',page.locator('iframe').count()==0);check('javascript_errors_absent',not result['page_errors']);check('no_external_requests',not result['blocked_nonlocal'])
    ctx.close();b.close()
result['status']='PASS' if all(c['status']=='PASS' for c in result['checks']) else 'FAIL'
(root/'BROWSER_QA.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'status':result['status'],'checks':len(result['checks']),'failed':[c for c in result['checks'] if c['status']=='FAIL']},ensure_ascii=False))
if result['status']!='PASS':raise SystemExit(1)
