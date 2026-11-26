"""Real Chromium checks against isolated local QA server; no external calls."""
from pathlib import Path
import io,json,time
from PIL import Image,ImageDraw
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT.parents[1]/'reports/CONTENT_STUDIO_LOCAL_20261106'
OUT.mkdir(exist_ok=True)
checks=[]

def record(name,condition):
    checks.append({'name':name,'pass':bool(condition)})
    assert condition,name


with sync_playwright() as pw:
    browser=pw.chromium.launch(executable_path='C:/Users/man503/AppData/Local/ms-playwright/chromium-1228/chrome-win64/chrome.exe',headless=True)
    context=browser.new_context(viewport={'width':1440,'height':1100},accept_downloads=True)
    page=context.new_page();errors=[];external=[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    page.on('request',lambda r:external.append(r.url) if not r.url.startswith(('http://127.0.0.1:58207/','data:','about:')) else None)
    page.goto('http://127.0.0.1:58207/')
    page.locator('#login-code').fill('qa-browser-local');page.locator('#login-submit').click()
    page.locator('#blank-project').click()
    page.locator('#project-title').fill('Локальный обзор · тестовый материал')
    page.locator('#plain-editor').fill('Готовим материал об оборудовании.\n\nЭто тест локального редактора; характеристики не утверждаются.')
    page.locator('[data-tab="source"]').click()
    page.locator('#source-text').fill('Тестовые исходные данные. Фото — цветовые fixtures, не BELMASH B3499.')
    page.locator('#source-url').fill('https://example.com/reference')
    page.locator('#add-fact').click()
    page.locator('.fact textarea').fill('Для подготовки материалов доступны отдельные версии для сайта и VK.')
    page.locator('.fact select').select_option('CONFIRMED')
    page.locator('.fact input').first.fill('Локальная проверка редактора')
    page.locator('[data-tab="vk"]').click()
    files=[]
    for n,color in enumerate(['#d8e5df','#dce4ed','#eee3d1','#e8dbde'],1):
        im=Image.new('RGB',(700,650),color);draw=ImageDraw.Draw(im);draw.rectangle((100,100,600,500),outline='#52675e',width=5);draw.text((190,280),f'STUDIO / TEST PHOTO {n}',fill='#253f33',font_size=34)
        raw=io.BytesIO();im.save(raw,'PNG');files.append({'name':f'test-{n}.png','mimeType':'image/png','buffer':raw.getvalue()})
    page.locator('#photo-input').set_input_files(files)
    page.wait_for_function('() => document.querySelectorAll(".photo-card").length===4')
    page.wait_for_function('() => !document.getElementById("workspace").inert')
    for details in page.locator('.photo-meta details').all():details.locator('summary').click()
    for inp in page.locator('.photo-meta input[aria-label="Права / разрешение"]').all():inp.fill('Синтетическая QA-картинка; создана для тестирования')
    page.locator('.photo-controls button').nth(1).click()
    record('four_photos_visible',page.locator('.photo-card').count()==4)
    page.locator('[data-tab="news"]').click()
    page.locator('#draft-title').fill('Проверка версии для сайта')
    page.locator('#code-mode').click()
    page.locator('#code-editor').fill('<h2>Материал в работе</h2><p>Текст для сайта.</p><table><tr><th>Поле</th><th>Значение</th></tr><tr><td>Канал</td><td>Новость</td></tr></table>')
    page.locator('#visual-mode').click()
    page.wait_for_timeout(1200)
    record('visual_table_retained',page.locator('#visual-editor table').count()==1)
    page.locator('#code-mode').click();page.locator('#visual-mode').click()
    record('mode_round_trip_table',page.locator('#visual-editor table').count()==1)
    page.locator('#site-preview').wait_for(state='visible')
    record('sandbox_preview',page.locator('#site-preview').get_attribute('sandbox')=='')
    page.locator('[data-tab="vk"]').click()
    record('channel_text_independent','Готовим материал' in page.locator('#plain-editor').input_value())
    page.locator('#approve-button').click();page.wait_for_function('() => document.querySelector("#draft-state").textContent==="Готово"')
    record('approval_possible_with_evidence',page.locator('#draft-state').inner_text()=='Готово')
    page.screenshot(path=str(OUT/'desktop.png'),full_page=True)
    with page.expect_download() as info:page.locator('#export-top').click()
    download=info.value;download.save_as(str(OUT/'fixture-transfer.zip'))
    record('download_complete',download.failure() is None)
    page.reload();page.locator('.project-item').first.click()
    page.wait_for_function('() => document.querySelector("#plain-editor").value.includes("Готовим материал")')
    record('restart_reload_text_retained','Готовим материал' in page.locator('#plain-editor').input_value())
    record('restart_reload_photos_retained',page.locator('.photo-card').count()==4)
    for width in [390,768,1440]:
        page.set_viewport_size({'width':width,'height':1000});page.wait_for_timeout(150)
        record(f'no_page_overflow_{width}',page.evaluate('() => document.documentElement.scrollWidth<=innerWidth+1'))
        if width==390:page.screenshot(path=str(OUT/'mobile.png'),full_page=True)
    page.locator('[data-tab="export"]').click()
    with page.expect_download() as info:page.locator('#backup-export').click()
    backup=info.value;backup.save_as(str(OUT/'fixture-backup.zip'))
    before=page.locator('.project-item').count()
    page.locator('#zip-input').set_input_files(str(OUT/'fixture-backup.zip'))
    page.wait_for_function(f'() => document.querySelectorAll(".project-item").length>{before}')
    record('backup_restored_new_project',page.locator('.project-item').count()==before+1)
    record('restored_not_approved',page.locator('#draft-state').inner_text()=='Черновик')
    record('no_browser_script_errors',not errors)
    record('no_external_requests',not external)
    browser.close()

(OUT/'BROWSER_QA.json').write_text(json.dumps({'checks':checks,'pass':len(checks),'fail':0,'images':'synthetic color fixtures, not B3499','browser':'installed Chromium 1228','errors':errors,'external_requests':external},ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'pass':len(checks),'fail':0}))
