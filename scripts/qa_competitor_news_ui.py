"""Local UI/read-only regression QA; no external link fetch or collection POST."""
from pathlib import Path
import base64,json,tempfile
from playwright.sync_api import sync_playwright
from competitor_intel.store import Store
from competitor_intel.business_data import read_state,fingerprints
from competitor_intel.monitoring import MonitoringService,MonitoringRepository

root=Path('reports/COMPETITOR_NEWS_UI_2026-10-13');root.mkdir(exist_ok=True)
config=Path('competitor-data/dev-config.json')
def database_proof():
    s=Store(json.loads(config.read_text())['dsn']);s.conn.read_only=True
    try:return fingerprints(read_state(s))
    finally:s.close()
before=database_proof();service=MonitoringService(MonitoringRepository(config));view=service.view('news',{})
checks={};external=[];errors=[]
with sync_playwright() as p:
    for zoom in (1,1.25):
        with tempfile.TemporaryDirectory(prefix='sterbrust-news-qa-') as profile:
            ext=Path(profile)/'qa-zoom';ext.mkdir()
            (ext/'manifest.json').write_text(json.dumps({'manifest_version':3,'name':'Local NEWS QA zoom','version':'1.0','permissions':['tabs'],'background':{'service_worker':'zoom.js'}}))
            (ext/'zoom.js').write_text('chrome.tabs.onUpdated.addListener((id,info,tab)=>{if(tab.url && tab.url.startsWith("http://127.0.0.1:58124/")) chrome.tabs.setZoom(id,'+str(zoom)+');});')
            ctx=p.chromium.launch_persistent_context(str(Path(profile)/'browser'),channel='msedge',headless=True,ignore_default_args=['--disable-extensions'],args=[f'--disable-extensions-except={ext}',f'--load-extension={ext}'])
            def route(r):
                if r.request.url.startswith('http://127.0.0.1:58124/') and r.request.method=='GET':r.continue_()
                else:external.append(r.request.url);r.abort()
            ctx.route('**/*',route);page=ctx.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
            for width,height in ((1920,1080),(1366,768),(375,812)):
                page.set_viewport_size({'width':width,'height':height})
                page.goto('http://127.0.0.1:58124/competitor-monitoring/news',wait_until='networkidle')
                page.wait_for_function('(z)=>Math.abs(devicePixelRatio-z)<.02',arg=zoom)
                key=f'{width}_{int(zoom*100)}'
                checks[key+'_native_zoom']=page.evaluate('devicePixelRatio')==zoom
                checks[key+'_no_overflow']=page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                checks[key+'_stored_records_render']=page.locator('.mon-news-card').count()==len(view['rows'])
                checks[key+'_news_tab_order']=page.locator('.mon-tabs a').all_text_contents()==['Обзор','Изменения','Цены','Акции','Новости','Ассортимент','Источники']
                checks[key+'_canonical_links']=page.locator('.mon-news-card details a').evaluate_all('(links)=>links.every(a=>/^https?:/.test(a.href) && a.rel.includes("noopener"))')
                bounds=page.evaluate('({width:innerWidth,height:document.documentElement.scrollHeight})');session=ctx.new_cdp_session(page)
                shot=session.send('Page.captureScreenshot',{'format':'png','captureBeyondViewport':True,'clip':{'x':0,'y':0,'width':bounds['width']*zoom,'height':bounds['height']*zoom,'scale':1}})
                (root/f'NEWS_{key}.png').write_bytes(base64.b64decode(shot['data']));session.detach()
            if view['rows']:
                chosen=view['rows'][0];page.select_option('.mon-news-filters select[name="source"]',chosen['source']);page.fill('.mon-news-filters input[name="search"]',chosen['title'][:20]);page.get_by_role('button',name='Показать',exact=True).click();page.wait_for_load_state('networkidle')
                checks[f'{zoom}_source_search_form']=page.locator('.mon-news-card').count()>0 and all(chosen['source_name'] in text for text in page.locator('.mon-news-card .mon-news-meta').all_text_contents())
            page.goto('http://127.0.0.1:58124/competitor-monitoring/news?content_type=BLOG',wait_until='networkidle')
            checks[f'{zoom}_blog_semantics']=page.locator('[data-content-type="NEWS"]').count()==0
            page.goto('http://127.0.0.1:58124/competitor-monitoring/news?only_new=1',wait_until='networkidle')
            checks[f'{zoom}_only_new_actual']=all('NEW' in t for t in page.locator('.mon-content-event').all_text_contents()) and page.locator('.mon-news-card').count()==service.view('news',{'only_new':'1'})['total']
            for tab in ('overview','changes','prices','promotions','assortment','sources'):
                page.set_viewport_size({'width':1366,'height':768});page.goto('http://127.0.0.1:58124/competitor-monitoring/'+tab,wait_until='networkidle')
                checks[f'{zoom}_{tab}_renders']=page.locator('.monitoring h2').inner_text()=='Мониторинг конкурентов'
                checks[f'{zoom}_{tab}_no_overflow']=page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                if tab=='overview':
                    checks[f'{zoom}_overview_news_kpi']=page.get_by_text('Новых новостей',exact=True).count()==1
                    checks[f'{zoom}_overview_latest']=page.locator('.mon-latest-row').count()==len(service.view('overview',{})['materials'])
            ctx.close()
after=database_proof();checks['all_DB_fingerprints_equal']=before==after;checks['external_HTTP_zero']=not external;checks['JS_errors_zero']=not errors
result={'status':'PASS' if all(checks.values()) else 'FAIL','checks':checks,'current_materials':{'NEWS':sum(n['content_type']=='NEWS' for n in view['rows']),'BLOG':sum(n['content_type']=='BLOG' for n in view['rows']),'total':view['total'],'new_news_kpi':service.view('overview',{})['kpi']['new_news']},'before':before,'after':after,'source_HTTP':0,'collection_POST':0}
(root/'NEWS_UI_QA.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'status':result['status'],'checks':len(checks),'failed':[k for k,v in checks.items() if not v],'materials':result['current_materials']},ensure_ascii=False))
if result['status']!='PASS':raise SystemExit(1)
