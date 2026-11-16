"""Exact operator layout and publishing setup on isolated fake AI/VK."""
import io,json
from pathlib import Path
from PIL import Image
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT.parents[1]/'reports/CONTENT_STUDIO_TEMPLATE_VK_20261111'
SOURCE=(ROOT/'tests/fixtures/komtekh-ready-news.html').read_text(encoding='utf-8')
checks=[]
def check(name,value):
    checks.append({'name':name,'pass':bool(value)})
    assert value,name

with sync_playwright() as pw:
    browser=pw.chromium.launch(executable_path='C:/Users/man503/AppData/Local/ms-playwright/chromium-1228/chrome-win64/chrome.exe',headless=True)
    context=browser.new_context(viewport={'width':1440,'height':1000});page=context.new_page();errors=[];external=[];streams=[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    context.on('request',lambda r:external.append(r.url) if not r.url.startswith(('http://127.0.0.1:58207/','data:','about:')) else None)
    page.on('request',lambda r:streams.append(r.url) if r.url.endswith('/compose/stream') else None)
    page.goto('http://127.0.0.1:58207/');page.locator('#login-code').fill('qa-browser-local');page.locator('#login-submit').click()
    page.locator('#welcome-new').click();page.locator('#compose-source').fill(SOURCE);page.locator('#compose-title').fill('Комтех. Изменение цен с 1 декабря 2026 года')
    page.locator('[data-compose-channel="vk"]').uncheck();page.locator('#compose-engine').select_option('ai')
    check('news_html_reports_no_ai_needed','Без запроса к ИИ' in page.locator('#compose-ai-target').inner_text())
    page.locator('#compose-submit').click();page.locator('#compose-dialog').wait_for(state='hidden')
    check('preserved_news_uses_zero_model_requests',not streams)
    with context.expect_page() as pop:page.locator('#open-preview-window').click()
    preview=pop.value;preview.wait_for_load_state('domcontentloaded')
    card=preview.locator('a').filter(has_text='Комтех ЗСВ-20').first
    check('full_window_has_product_cards',preview.locator('section').count()==3 and preview.locator('a[href^="https://sterbrust.com/catalog/"]').count()==14)
    check('css_flex_applies_despite_csp',card.evaluate("e=>getComputedStyle(e.parentElement).display==='flex'"))
    check('css_grid_applies_despite_csp',preview.locator('section').first.locator('div').first.evaluate("e=>getComputedStyle(e).display==='grid'"))
    cta=preview.locator('a').filter(has_text='Связаться с менеджером')
    check('orange_cta_and_white_text',cta.evaluate("e=>getComputedStyle(e).backgroundColor==='rgb(242, 114, 44)' && getComputedStyle(e).color==='rgb(255, 255, 255)'"))
    check('contact_links_retained',preview.locator('a[href^="tel:"]').count()==1 and preview.locator('a[href^="mailto:"]').count()==1)
    for width,height in ((390,844),(960,900),(1440,1000)):
        preview.set_viewport_size({'width':width,'height':height})
        check('standalone_no_horizontal_scroll_'+str(width),preview.locator('body').evaluate('e=>e.scrollWidth<=innerWidth'))
        preview.screenshot(path=str(OUT/f'ready-news-{width}.png'),full_page=True)
    preview.close()
    # A generated upload records the chosen type once, without a separate rights form.
    page.locator('[data-tab="vk"]').click()
    buffer=io.BytesIO();Image.new('RGB',(1200,700),'orange').save(buffer,format='PNG')
    page.locator('#photo-input').set_input_files({'name':'generated-qa.png','mimeType':'image/png','buffer':buffer.getvalue()})
    page.locator('#photos .photo-card').first.wait_for()
    check('generated_rights_recorded',not page.locator('#photos .photo-rights-missing').count())
    page.locator('#vk-settings').click();page.locator('#vk-settings-dialog').wait_for(state='visible')
    check('community_token_limit_explained','пользовательский' in page.locator('#vk-photo-setup-title').inner_text().lower())
    page.screenshot(path=str(OUT/'vk-photo-setup.png'));page.locator('#vk-settings-close').click()
    check('no_script_errors',not errors);check('no_external_calls',not external)
    browser.close()
(OUT/'BROWSER_QA.json').write_text(json.dumps({'pass':len(checks),'fail':0,'checks':checks,'scope':'isolated fake AI/VK; exact user HTML'},ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'pass':len(checks),'fail':0}))
