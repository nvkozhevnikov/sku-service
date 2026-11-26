from pathlib import Path
import json
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT.parents[1]/'reports/CONTENT_STUDIO_VK_20261106';OUT.mkdir(exist_ok=True)
checks=[]
def ck(name,value):checks.append({'name':name,'pass':bool(value)});assert value,name
with sync_playwright() as pw:
    browser=pw.chromium.launch(executable_path='C:/Users/man503/AppData/Local/ms-playwright/chromium-1228/chrome-win64/chrome.exe',headless=True)
    context=browser.new_context(viewport={'width':1440,'height':1000});page=context.new_page();errors=[]
    page.on('pageerror',lambda x:errors.append(str(x)))
    page.goto('http://127.0.0.1:58207/');page.locator('#login-code').fill('qa-browser-local');page.locator('#login-submit').click()
    page.locator('#vk-settings').click();page.locator('#vk-connection-status').get_by_text('Фото API заблокировано',exact=False).wait_for()
    ck('connection_status_without_secret', 'ошибка 27' in page.locator('#vk-connection-status').inner_text())
    page.screenshot(path=str(OUT/'vk-settings-mock.png'),full_page=True)
    page.locator('#vk-settings-close').click();page.locator('#blank-project').click()
    page.locator('#project-title').fill('QA · только имитация VK')
    page.locator('#plain-editor').fill('Проверка интерфейса. Этот текст не отправляется в настоящий VK.')
    page.locator('[data-tab="source"]').click();page.locator('#add-fact').click();page.locator('.fact textarea').fill('Тест интерфейса');page.locator('.fact select').select_option('CONFIRMED');page.locator('.fact input').first.fill('QA fixture')
    page.locator('[data-tab="vk"]').click();page.locator('#approve-button').click();page.locator('#draft-state').get_by_text('Готово',exact=True).wait_for()
    page.locator('#vk-prepare').click();page.locator('#vk-confirm-dialog').wait_for(state='visible')
    ck('immutable_confirmation_text','не отправляется' in page.locator('#vk-confirm-text').inner_text())
    ck('confirmation_required',not page.locator('#vk-confirm-send').is_enabled())
    page.locator('#vk-ack').check();ck('explicit_ack_enables_button',page.locator('#vk-confirm-send').is_enabled())
    page.screenshot(path=str(OUT/'vk-confirm-mock.png'),full_page=True)
    page.locator('#vk-confirm-send').click();page.locator('#vk-attempt-status').get_by_text('Пост создан в VK',exact=True).wait_for()
    ck('mock_receipt_with_link',page.locator('#vk-attempt-status a').get_attribute('href')=='https://vk.com/wall-68800619_123')
    page.locator('#vk-confirm-close').click();page.locator('#vk-settings').click();page.locator('#vk-journal').get_by_text('Пост создан в VK',exact=True).first.wait_for()
    ck('journal_shows_mock_result',page.locator('#vk-journal .vk-entry').count()>=1)
    for width in [390,768,1440]:
        page.locator('#vk-settings-close').click();page.set_viewport_size({'width':width,'height':1000})
        ck(f'no_header_overflow_{width}',page.evaluate('() => document.documentElement.scrollWidth<=innerWidth+1'))
        page.locator('#vk-settings').click()
    ck('no_script_errors',not errors)
    browser.close()
(OUT/'BROWSER_MOCK_QA.json').write_text(json.dumps({'checks':checks,'pass':len(checks),'fail':0,'scope':'MOCK VK ONLY; no real wall.post/upload/save'},ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'pass':len(checks),'fail':0,'mode':'MOCK ONLY'}))
