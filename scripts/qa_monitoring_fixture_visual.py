"""Clearly marked local-only fixture UI for changes, long content and error states."""
import os,json,runpy,threading,time
from pathlib import Path
os.environ['CONTROL_PLANE_SKIP_DEFAULT_APP']='YES'
import uvicorn
from playwright.sync_api import sync_playwright
from fastapi import Request
from universal_supplier.control_plane.web import create_app
from universal_supplier.control_plane.store import UnavailableControlPlaneStore
from universal_supplier.control_plane.commercial_qa import DisabledCommercialQaService
from competitor_intel.monitoring import MonitoringService,MonitoringUnavailable

fixture=runpy.run_path('tests/test_competitor_monitoring_ui.py');state=fixture['fixture_state']()
state['competitor_source_surfaces'][-1]['canonical_url']='https://fixture.example/'+('long-url-segment-'*60)
class Repo:
    def load(self):return state
class FixtureService(MonitoringService):
    def view(self,tab,query):
        if query.get('qa')=='db-error':raise MonitoringUnavailable()
        return super().view(tab,query)
class FixtureStore(UnavailableControlPlaneStore):
    read_only=True;fixture_preview=True
app=create_app(store=FixtureStore('fixture only'),auth_required=False,commercial_qa_service=DisabledCommercialQaService(),competitor_service=FixtureService(Repo(),clock=lambda:fixture['NOW']))
server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=58125,log_level='error'));thread=threading.Thread(target=server.run,daemon=True);thread.start()
for _ in range(100):
    if server.started:break
    time.sleep(.05)
root=Path('reports/COMPETITOR_MONITORING_UI_LOCAL_2026-10-12');checks={};external=[]
try:
    with sync_playwright() as p:
        b=p.chromium.launch(channel='msedge',headless=True);ctx=b.new_context()
        def route(r):
            if r.request.url.startswith('http://127.0.0.1:58125/'):r.continue_()
            else:external.append(r.request.url);r.abort()
        ctx.route('**/*',route);page=ctx.new_page()
        for width,height in [(1920,1080),(1366,768)]:
            page.set_viewport_size({'width':width,'height':height});page.goto('http://127.0.0.1:58125/competitor-monitoring/changes')
            checks[f'{width}_fixture_banner']='ТЕСТОВЫЕ ДАННЫЕ' in page.locator('.safety').inner_text()
            checks[f'{width}_price_decrease_row']=page.get_by_text('Цена снизилась',exact=True).count()>0
            checks[f'{width}_new_promotion_row']=page.get_by_text('Новая акция',exact=True).count()>0
            checks[f'{width}_long_title_no_document_overflow']=page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            page.screenshot(path=str(root/f'FIXTURE_CHANGES_{width}.png'),full_page=True)
        page.goto('http://127.0.0.1:58125/competitor-monitoring/sources');page.locator('details').last.locator('summary').click()
        checks['long_url_wraps']=page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        checks['source_error_local']=page.locator('.mon-status.error').count()==1
        page.screenshot(path=str(root/'FIXTURE_SOURCE_ERROR.png'),full_page=True)
        page.goto('http://127.0.0.1:58125/competitor-monitoring?qa=db-error')
        checks['database_error_readable']='База мониторинга сейчас недоступна' in page.locator('.monitoring').inner_text() and 'Traceback' not in page.locator('body').inner_text()
        page.screenshot(path=str(root/'FIXTURE_DATABASE_ERROR.png'),full_page=True)
        checks['no_source_http']=not external
        ctx.close();b.close()
finally:server.should_exit=True;thread.join(timeout=5)
result={'fixture_only':True,'real_source_data_writes':0,'checks':checks,'status':'PASS' if all(checks.values()) else 'FAIL'}
(root/'FIXTURE_BROWSER_QA.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(result,ensure_ascii=False))
if result['status']!='PASS':raise SystemExit(1)
