"""Canonical domain and field errors on isolated local fake AI/VK only."""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT.parents[1]/'reports/CONTENT_STUDIO_LINKS_20261111'
checks=[]
def check(name,value):
    checks.append({'name':name,'pass':bool(value)})
    assert value,name

with sync_playwright() as pw:
    browser=pw.chromium.launch(executable_path='C:/Users/man503/AppData/Local/ms-playwright/chromium-1228/chrome-win64/chrome.exe',headless=True)
    page=browser.new_page(viewport={'width':960,'height':900});external=[];streams=[];errors=[]
    page.on('request',lambda r:external.append(r.url) if not r.url.startswith(('http://127.0.0.1:58207/','data:','about:')) else None)
    page.on('request',lambda r:streams.append(r.url) if r.url.endswith('/compose/stream') else None)
    page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto('http://127.0.0.1:58207/');page.locator('#login-code').fill('qa-browser-local');page.locator('#login-submit').click()
    page.locator('#ai-settings').click();page.locator('#ai-add-profile').click();page.locator('#ai-preset').select_option('glm_coding');page.locator('#ai-model').fill('glm-5.3');page.locator('#ai-key').fill('FAKE_LOCAL_ONLY_KEY_123456789');page.locator('#ai-save').click();page.locator('#ai-status').get_by_text('Сохранено на этом компьютере',exact=False).wait_for();page.locator('#ai-close').click()
    page.locator('#welcome-new').click();source='Учебная новость. Токарный станок BELMASH. Мощность двигателя: 600 Вт.'
    page.locator('#compose-source').fill(source);page.locator('[data-compose-channel="news"]').uncheck();page.locator('#compose-engine').select_option('ai');page.locator('#compose-ai-ack').check()
    page.locator('#compose-publication-url').fill('https://wrong.example/catalog/a/');page.locator('#compose-submit').click();page.locator('#compose-publication-url[aria-invalid="true"]').wait_for()
    check('invalid_link_shown_at_field','sterbrust.com' in page.locator('#compose-publication-url-error').inner_text())
    check('invalid_link_does_not_start_ai',not streams)
    check('not_presented_as_provider_failure',not page.locator('#compose-ai-error').is_visible() and not page.locator('#compose-reasoning').is_visible())
    check('source_preserved',page.locator('#compose-source').input_value()==source)
    check('invalid_field_focused',page.locator('#compose-publication-url').evaluate('e=>e===document.activeElement'))
    page.screenshot(path=str(OUT/'invalid-link-960.png'))
    for width in (320,390,960):
        page.set_viewport_size({'width':width,'height':900})
        check('field_error_fits_'+str(width),page.locator('#compose-dialog').evaluate('e=>e.scrollWidth<=e.clientWidth+1'))
        if width==390:page.screenshot(path=str(OUT/'invalid-link-390.png'))
    page.locator('#compose-publication-url').fill('sterbrust.com/catalog/a/');check('field_error_cleared_on_edit',page.locator('#compose-publication-url-error').inner_text()=='')
    page.locator('#compose-url').fill('mailto:post@sterbrust.com');page.locator('#compose-submit').click();page.locator('#compose-url[aria-invalid="true"]').wait_for()
    check('source_link_has_own_field_error',bool(page.locator('#compose-url-error').inner_text()) and not streams)
    page.locator('#compose-url').fill('');page.locator('#compose-submit').click();page.locator('#compose-dialog').wait_for(state='hidden')
    check('com_link_reaches_one_fake_generation',len(streams)==1)
    check('footer_keeps_com_link','https://sterbrust.com/catalog/a/' in page.locator('#plain-editor').input_value())
    check('no_external_calls',not external);check('no_script_errors',not errors)
    page.screenshot(path=str(OUT/'com-success-960.png'));browser.close()
(OUT/'BROWSER_QA.json').write_text(json.dumps({'pass':len(checks),'fail':0,'checks':checks},ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'pass':len(checks),'fail':0}))
