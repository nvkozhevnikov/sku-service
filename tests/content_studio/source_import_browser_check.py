import json
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT.parents[1]/'reports/CONTENT_STUDIO_TEXT_SOURCE_20261111'
URL='https://sterbrust.com/catalog/tv7m/'
checks=[]
def check(name,value):
    checks.append({'name':name,'pass':bool(value)})
    assert value,name

with sync_playwright() as p:
    browser=p.chromium.launch(executable_path='C:/Users/man503/AppData/Local/ms-playwright/chromium-1228/chrome-win64/chrome.exe',headless=True)
    page=browser.new_page(viewport={'width':1280,'height':1000});errors=[];external=[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    page.on('request',lambda r:external.append(r.url) if not r.url.startswith(('http://127.0.0.1:58207/','data:','about:')) else None)
    page.goto('http://127.0.0.1:58207/');page.locator('#login-code').fill('qa-browser-local');page.locator('#login-submit').click()
    page.locator('#ai-settings').click();page.locator('#ai-add-profile').click();page.locator('#ai-preset').select_option('glm_coding');page.locator('#ai-model').fill('glm-5.3');page.locator('#ai-key').fill('SOURCE_FAKE_ONLY_NOT_EXTERNAL_123456');page.locator('#ai-save').click();page.locator('#ai-status').get_by_text('Сохранено на этом компьютере',exact=False).wait_for();page.locator('#ai-close').click()
    page.locator('#welcome-new').click();page.locator('#compose-dialog').wait_for(state='visible');check('ai_default_when_connected',page.locator('#compose-engine').input_value()=='ai')
    page.locator('#compose-source').fill(URL);page.locator('#compose-source-status').get_by_text('Страница прочитана',exact=False).wait_for()
    check('url_paste_loads_description','275' in page.locator('#compose-source').input_value())
    check('title_from_page','ТВ-7М' in page.locator('#compose-title').input_value())
    check('source_and_footer_auto',page.locator('#compose-url').input_value()==URL and page.locator('#compose-publication-url').input_value()==URL)
    check('product_template_auto',page.locator('#compose-preset').input_value()=='product')
    page.locator('#compose-publication-url').fill('https://sterbrust.com/catalog/custom/');page.locator('#compose-url').fill('https://sterbrust.com/catalog/other/')
    check('manual_footer_not_replaced',page.locator('#compose-publication-url').input_value().endswith('/custom/'))
    page.locator('#compose-publication-url').fill('');page.locator('#compose-load-page').click();page.locator('#compose-source-status').get_by_text('Страница прочитана',exact=False).wait_for()
    check('removed_footer_stays_empty',page.locator('#compose-publication-url').input_value()=='')
    page.locator('#compose-link-from-source').click();check('explicit_restore_auto_link',page.locator('#compose-publication-url').input_value().endswith('/other/'))
    page.locator('[data-compose-channel="article"]').check();page.locator('#compose-ai-ack').check()
    page.screenshot(path=str(OUT/'source-import-1280.png'))
    for width in (390,960):
        page.set_viewport_size({'width':width,'height':900})
        check('import_form_fits_'+str(width),page.locator('#compose-dialog').evaluate('e=>e.scrollWidth<=e.clientWidth+1'))
        if width==390:page.screenshot(path=str(OUT/'source-import-390.png'))
    page.locator('#compose-submit').click();page.locator('#compose-dialog').wait_for(state='hidden')
    check('all_three_versions_prepared',all(page.get_by_role('tab',name=name,exact=True).is_visible() for name in ['VK','Новость сайта','Статья сайта']))
    check('single_attached_link',page.locator('#plain-editor').input_value().count('https://sterbrust.com/catalog/other/')==1)
    page.locator('#compose-existing').click();page.locator('#compose-publication-url').fill('');page.locator('#compose-engine').select_option('local')
    page.on('dialog',lambda d:d.accept());page.locator('#compose-submit').click();page.locator('#compose-dialog').wait_for(state='hidden')
    check('manual_removal_persists_in_local_post','https://sterbrust.com/catalog/other/' not in page.locator('#plain-editor').input_value())
    page.locator('#home-button').click();page.locator('#welcome-new').click();page.locator('#compose-source').fill('https://sterbrust.com/catalog/slow/')
    page.locator('#compose-source-status').get_by_text('Читаем страницу',exact=False).wait_for();page.locator('#compose-source').fill('Мой ручной текст о новом станке. Он должен сохраниться.')
    page.wait_for_timeout(1400)
    check('late_response_does_not_overwrite_manual_text',page.locator('#compose-source').input_value().startswith('Мой ручной текст'))
    check('source_error_not_model_failure',not page.locator('#compose-ai-error').is_visible())
    check('no_js_errors',not errors);check('no_external_calls',not external);browser.close()
(OUT/'BROWSER_QA.json').write_text(json.dumps({'pass':len(checks),'fail':0,'checks':checks,'scope':'fixture source, fake AI/VK'},ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'pass':len(checks),'fail':0}))
