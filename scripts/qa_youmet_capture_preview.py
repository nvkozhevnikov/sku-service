"""Offline provenance UI QA against clearly synthetic rollback-fixture preview."""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright
root=Path('reports/YOUMET_OPERATOR_CAPTURE_SUPPORT_2026-10-12');out=root/'FIXTURE_PREVIEW'
proof={'fixture_only':True,'real_operator_captures':0,'source_HTTP':0,'checks':{},'remote_attempts':[]}
with sync_playwright() as p:
    browser=p.chromium.launch(channel='msedge',headless=True);context=browser.new_context(viewport={'width':1440,'height':1050})
    def route(r):
        if r.request.url.startswith(('http://','https://')):proof['remote_attempts'].append(r.request.url);r.abort()
        else:r.continue_()
    context.route('**/*',route);page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto((out/'index.html').resolve().as_uri(),wait_until='load')
    text=page.locator('.source-card').inner_text()
    proof['checks']['operator_capture_note_visible']='OPERATOR_BROWSER_CAPTURE' in text
    proof['checks']['direct_automated_access_still_incomplete']='Прямой автоматический доступ остаётся неполным' in text and 'HTTP_BLOCKED' in text
    proof['checks']['first_capture_initial_baseline']='INITIAL BASELINE' in text
    proof['checks']['not_misrepresented_as_crawler_fetch']='LIVE_HTTP_OK' not in text and 'PUBLIC_FETCH_OK' not in text
    proof['checks']['javascript_errors_absent']=not errors
    proof['checks']['desktop_no_document_overflow']=page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    page.screenshot(path=str(root/'FIXTURE_DESKTOP.png'),full_page=True)
    page.set_viewport_size({'width':390,'height':844})
    proof['checks']['mobile_no_document_overflow']=page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    page.screenshot(path=str(root/'FIXTURE_MOBILE.png'),full_page=True)
    proof['checks']['no_remote_requests']=not proof['remote_attempts']
    context.close();browser.close()
proof['status']='PASS' if all(proof['checks'].values()) else 'FAIL'
(root/'FIXTURE_UI_QA.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(proof,ensure_ascii=False))
if proof['status']!='PASS':raise SystemExit(1)
