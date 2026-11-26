"""Local-only visual acceptance. Ephemeral extension sets native browser zoom only on localhost."""
import json,math,tempfile,base64
from pathlib import Path
from playwright.sync_api import sync_playwright
root=Path('reports/COMPETITOR_MONITORING_UI_VISUAL_2026-10-12');root.mkdir(exist_ok=True)
result={'checks':[],'browser_zoom':[],'external_requests':[],'errors':[]}
def check(name,ok):result['checks'].append({'name':name,'pass':bool(ok)})
def screenshot(page,ctx,path,zoom):
    # CDP capture uses device-independent pixels; native page zoom needs the
    # full document CSS bounds multiplied by browser zoom (Playwright clips it).
    bounds=page.evaluate('({width:innerWidth,height:document.documentElement.scrollHeight})')
    session=ctx.new_cdp_session(page)
    shot=session.send('Page.captureScreenshot',{'format':'png','captureBeyondViewport':True,'clip':{'x':0,'y':0,'width':bounds['width']*zoom,'height':bounds['height']*zoom,'scale':1}})
    path.write_bytes(base64.b64decode(shot['data']));session.detach()
with sync_playwright() as p:
    for zoom in (1,1.25):
        with tempfile.TemporaryDirectory(prefix='sterbrust-ui-qa-') as profile:
            ext=Path(profile)/'qa-zoom';ext.mkdir()
            (ext/'manifest.json').write_text(json.dumps({'manifest_version':3,'name':'Local Sterbrust QA zoom','version':'1.0','permissions':['tabs'],'background':{'service_worker':'zoom.js'}}),encoding='utf-8')
            (ext/'zoom.js').write_text('chrome.tabs.onUpdated.addListener((id, info, tab) => { if(tab.url && tab.url.startsWith("http://127.0.0.1:58124/")) chrome.tabs.setZoom(id,'+str(zoom)+'); });',encoding='utf-8')
            ctx=p.chromium.launch_persistent_context(str(Path(profile)/'browser'),channel='msedge',headless=True,ignore_default_args=['--disable-extensions'],args=[f'--disable-extensions-except={ext}',f'--load-extension={ext}'],viewport={'width':1920,'height':1080})
            def route(r):
                if r.request.url.startswith('http://127.0.0.1:58124/'):r.continue_()
                else:result['external_requests'].append(r.request.url);r.abort()
            ctx.route('**/*',route);page=ctx.new_page();page.on('pageerror',lambda e:result['errors'].append(str(e)))
            for width,height in ((1920,1080),(1600,900),(1366,768)):
                page.set_viewport_size({'width':width,'height':height})
                for tab in ('overview','changes','prices','promotions','assortment','sources'):
                    page.goto(f'http://127.0.0.1:58124/competitor-monitoring/{tab}',wait_until='networkidle')
                    page.wait_for_function('(expected) => Math.abs(devicePixelRatio-expected)<.02',arg=zoom)
                    metrics=page.evaluate('({dpr:devicePixelRatio,width:innerWidth,overflow:document.documentElement.scrollWidth>innerWidth,cssZoom:getComputedStyle(document.body).zoom})')
                    result['browser_zoom'].append({'viewport':width,'zoom':zoom,'tab':tab,**metrics})
                    check(f'{width}_{zoom}_{tab}_native_zoom',abs(metrics['dpr']-zoom)<.02 and metrics['cssZoom']=='1')
                    check(f'{width}_{zoom}_{tab}_no_overflow',not metrics['overflow'])
                    check(f'{width}_{zoom}_{tab}_local_chip',page.locator('.mon-local').count()==1)
                    if tab=='overview':
                        check(f'{width}_{zoom}_six_kpis',page.locator('.mon-kpi').count()==6)
                        check(f'{width}_{zoom}_only_period_filter',page.locator('.mon-filters select').count()==1)
                    if tab=='sources':check(f'{width}_{zoom}_no_source_filters',page.locator('.mon-filters').count()==0)
                    screenshot(page,ctx,root/f'{tab.upper()}_{width}_{int(zoom*100)}.png',zoom)
                page.goto('http://127.0.0.1:58124/competitor-monitoring/sources?source=youmet',wait_until='networkidle')
                page.locator('summary').click()
                check(f'{width}_{zoom}_manual_not_error',page.locator('.mon-status.manual').count()==1 and page.locator('.mon-status.error').count()==0)
                check(f'{width}_{zoom}_manual_import_disabled',page.get_by_role('button',name='Импортировать HTML').is_disabled())
                screenshot(page,ctx,root/f'YOUMET_DETAIL_{width}_{int(zoom*100)}.png',zoom)
            page.goto('http://127.0.0.1:58124/competitor-monitoring/changes')
            page.select_option('select[name="period"]','custom')
            check(f'{zoom}_custom_dates_revealed',page.locator('input[name="from"]').is_visible() and page.locator('input[name="from"]').is_enabled())
            page.select_option('select[name="period"]','7')
            check(f'{zoom}_custom_dates_hidden',not page.locator('input[name="from"]').is_visible() and page.locator('input[name="from"]').is_disabled())
            page.goto('http://127.0.0.1:58124/competitor-monitoring/promotions')
            page.select_option('[data-promo-status]','ENDED')
            check(f'{zoom}_status_filter_local',page.locator('[data-promo-state]:visible').count()==page.locator('[data-promo-state="ENDED"]').count())
            page.goto('http://127.0.0.1:58124/competitor-monitoring/assortment')
            page.fill('[data-assortment-search]','___no_brand___')
            check(f'{zoom}_assortment_search_local',page.locator('.mon-assortment tbody tr:visible').count()==0)
            ctx.close()
check('no_external_http',not result['external_requests']);check('no_javascript_errors',not result['errors'])
result['status']='PASS' if all(c['pass'] for c in result['checks']) else 'FAIL'
(root/'VISUAL_QA.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'status':result['status'],'checks':len(result['checks']),'failures':[c for c in result['checks'] if not c['pass']]},ensure_ascii=False))
if result['status']!='PASS':raise SystemExit(1)
