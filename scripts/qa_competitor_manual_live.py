"""Explicit operator-authorized UI QA: one MetalMaster homepage, no other source crawl."""
from pathlib import Path
import json
from playwright.sync_api import sync_playwright
from competitor_intel.store import Store
from competitor_intel.business_data import read_state,fingerprints
from competitor_intel.execution import CollectionService,execution_lock

root=Path('reports/COMPETITOR_SCHEDULER_2026-10-13');cfg=Path('competitor-data/dev-config.json')
store=Store(json.loads(cfg.read_text())['dsn']);before=fingerprints(read_state(store));store.close()
controller=CollectionService(cfg);checks={};external=[]
with sync_playwright() as p:
    b=p.chromium.launch(channel='msedge',headless=True);ctx=b.new_context(viewport={'width':1600,'height':900})
    def route(r):
        if r.request.url.startswith('http://127.0.0.1:58124/'):r.continue_()
        else:external.append(r.request.url);r.abort()
    ctx.route('**/*',route);page=ctx.new_page()
    page.goto('http://127.0.0.1:58124/competitor-monitoring/sources',wait_until='networkidle')
    checks['scheduler_not_connected']='Планировщик не подключён' in page.inner_text('body') and 'Последний автоматический запуск: Никогда' in page.inner_text('body')
    with execution_lock(controller.runtime):
        # UI deliberately disables the button while it sees a running lock;
        # a stale browser form's POST must still be rejected by the shared runner.
        response=ctx.request.post('http://127.0.0.1:58124/competitor-monitoring/runs',form={'source':'metalmaster','max_pages':'1'},headers={'origin':'http://127.0.0.1:58124'})
        checks['stale_form_lock_rejected']=response.status==409 and response.json()['status']=='SKIP_ALREADY_RUNNING'
    page.reload(wait_until='networkidle');page.select_option('.mon-run-form select[name="source"]','metalmaster');page.fill('.mon-run-form input[name="max_pages"]','1')
    with page.expect_response(lambda r:r.request.method=='POST' and r.url.endswith('/competitor-monitoring/runs'),timeout=120000) as posted:
        page.get_by_role('button',name='Запустить ручной сбор').click(timeout=120000)
    checks['operator_button_POST_redirect']=posted.value.status==303
    page.wait_for_load_state('networkidle');page.screenshot(path=str(root/'MANUAL_AND_SCHEDULER_UI.png'),full_page=True)
    checks['unrelated_settings_still_read_only']=ctx.request.post('http://127.0.0.1:58124/settings').status==403
    checks['browser_external_HTTP_zero']=not external
    ctx.close();b.close()
result=json.loads((controller.runtime/'last-run.json').read_text(encoding='utf-8'))
checks['origin_operator_ui']=result['origin']=='OPERATOR_UI'
checks['only_metalmaster_selected']=list(result['sources'])==['metalmaster']
checks['observation_saved']=result['sources']['metalmaster'].get('stored',0)==1
checks['honest_bounded_partial']=result['status']=='PARTIAL' and result['exit_code']==1
checks['report_rebuilt']=(controller.runtime/'business-report/index.html').exists() and 'report' in result
checks['no_scheduled_run']=not (controller.runtime/'scheduled-last.json').exists() and not result['scheduler_enabled']
store=Store(json.loads(cfg.read_text())['dsn']);after=fingerprints(read_state(store))
checks['other_sources_unchanged']=all(before['sources'][code]==after['sources'][code] for code in before['sources'] if code!='metalmaster')
checks['historical_cache_unchanged']=before['tables']['competitor_historical_seeds']==after['tables']['competitor_historical_seeds']
rows=store.query('SELECT id,status,result FROM competitor_runs WHERE id=%s',(result['run_id'],));checks['run_record_persisted']=rows[0]['status']==result['status'] and rows[0]['result']['origin']=='OPERATOR_UI'
checks['duplicate_pages_zero']=not store.query('SELECT source_id,canonical_url FROM competitor_pages GROUP BY source_id,canonical_url HAVING count(*)>1')
checks['duplicate_versions_zero']=not store.query('SELECT page_id,content_hash FROM competitor_page_versions GROUP BY page_id,content_hash HAVING count(*)>1')
store.close()
receipt={'status':'PASS' if all(checks.values()) else 'FAIL','checks':checks,'run':result,'before':before,'after':after,'live_scope':'robots preflight + one MetalMaster homepage; no product detail'}
(root/'MANUAL_LIVE_QA.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
print(json.dumps({'status':receipt['status'],'checks':checks,'run_status':result['status'],'source_result':result['sources']},ensure_ascii=False))
if receipt['status']!='PASS':raise SystemExit(1)
