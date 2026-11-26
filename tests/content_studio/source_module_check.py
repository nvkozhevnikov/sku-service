"""Loads the module only in an isolated QA profile; public site responses are fixtures."""
import json
from uuid import uuid4
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT.parents[1]/'reports/CONTENT_STUDIO_TEXT_SOURCE_20261111'
checks=[]
def check(name,value):
    checks.append({'name':name,'pass':bool(value)});assert value,name

with sync_playwright() as p:
    folder=str((ROOT/'browser-import').resolve())
    context=p.chromium.launch_persistent_context(str(ROOT/'test-results'/('source-module-profile-'+uuid4().hex)),
        executable_path='C:/Users/man503/AppData/Local/ms-playwright/chromium-1228/chrome-win64/chrome.exe',
        headless=True,ignore_default_args=['--disable-extensions'],args=['--disable-extensions-except='+folder,'--load-extension='+folder])
    raw=(ROOT/'tests/fixtures/product-tv7m-dom.html').read_text(encoding='utf-8').replace('Технические характеристики','<input type="password" value="PRIVATE_FIELD_MUST_NOT_TRANSFER">Технические характеристики',1)
    routed=[]
    def fixture(route):
        routed.append(route.request.url);route.fulfill(status=200,content_type='text/html; charset=utf-8',body=raw)
    context.route('https://sterbrust.com/**',fixture)
    page=context.new_page();requests=[];payloads=[];errors=[]
    page.on('request',lambda r:requests.append(r.url))
    page.on('request',lambda r:payloads.append(r.post_data or '') if r.url.endswith('/api/sources/from-browser') else None)
    page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto('http://127.0.0.1:58207/');page.locator('#login-code').fill('qa-browser-local');page.locator('#login-submit').click();page.locator('#welcome-new').click();page.locator('#compose-dialog').wait_for(state='visible')
    page.wait_for_function("() => document.querySelector('#compose-browser-status').textContent.includes('подключён')")
    check('module_detected_in_isolated_browser',True)
    page.locator('#compose-source').fill('https://sterbrust.com/catalog/module-product/')
    page.wait_for_function("() => document.querySelector('#compose-source-status').textContent.includes('Страница прочитана') || document.querySelector('#compose-source-status').dataset.error==='true'",timeout=45000)
    if not payloads:
        print({'status':page.locator('#compose-source-status').inner_text().encode('unicode_escape').decode(),'routed':routed,'pages':[x.url for x in context.pages],'errors':errors})
    check('page_transferred_through_module','275' in page.locator('#compose-source').input_value())
    check('module_avoids_blocked_server_fetch',not any(u.endswith('/api/sources/read') for u in requests))
    check('no_password_fields_transferred',len(payloads)==1 and 'PRIVATE_FIELD_MUST_NOT_TRANSFER' not in payloads[0])
    check('automatic_footer_from_module',page.locator('#compose-publication-url').input_value().endswith('/module-product/'))
    check('no_javascript_errors',not errors)
    page.locator('#compose-browser-help').evaluate('e=>e.open=true')
    page.screenshot(path=str(OUT/'browser-module-qa.png'));context.close()
(OUT/'BROWSER_MODULE_QA.json').write_text(json.dumps({'pass':len(checks),'fail':0,'checks':checks,'scope':'isolated Chromium profile, fixture product responses; user browsers unchanged'},ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'pass':len(checks),'fail':0}))
