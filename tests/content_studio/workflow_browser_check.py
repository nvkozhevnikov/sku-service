from pathlib import Path
import io,json,zipfile
from datetime import datetime,timedelta
from PIL import Image,ImageDraw
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT.parents[1]/'reports/CONTENT_STUDIO_WORKFLOW_20261106';OUT.mkdir(exist_ok=True)
checks=[]
def ck(name,value):checks.append({'name':name,'pass':bool(value)});assert value,name
source='Демонстрация подготовки публикаций\n\nРедактор собирает разные варианты из одного исходника.\n\nВыберите VK, новость или статью.\n\nЭто синтетический QA-материал, не для реальной публикации.'
with sync_playwright() as pw:
    browser=pw.chromium.launch(executable_path='C:/Users/man503/AppData/Local/ms-playwright/chromium-1228/chrome-win64/chrome.exe',headless=True)
    ctx=browser.new_context(viewport={'width':1440,'height':1050},accept_downloads=True);page=ctx.new_page();errors=[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto('http://127.0.0.1:58207/');page.locator('#login-code').fill('qa-browser-local');page.locator('#login-submit').click();page.locator('#welcome-new').click();page.locator('#compose-dialog').wait_for(state='visible')
    ck('primary_action_opens_source_workflow',page.locator('#compose-source').is_visible())
    ck('review_not_preselected',not page.locator('#compose-reviewed').is_checked())
    page.locator('#compose-source').fill(source);page.locator('#compose-title').fill('Один исходник — три версии');page.locator('[data-compose-channel="article"]').check();page.locator('#compose-reviewed').check();page.locator('#compose-photo-origin').fill('Синтетические QA-изображения');page.locator('#compose-photo-rights').fill('Созданы для тестирования интерфейса')
    files=[]
    for n,color in enumerate(['#d1dfce','#ede1c9'],1):
        im=Image.new('RGB',(700,500),color);draw=ImageDraw.Draw(im);draw.text((100,220),f'COMPOSE TEST PHOTO {n}',fill='#35503c',font_size=32);buf=io.BytesIO();im.save(buf,'PNG');files.append({'name':f'compose-{n}.png','mimeType':'image/png','buffer':buf.getvalue()})
    page.locator('#compose-file-input').set_input_files(files);ck('photos_queued_with_source',page.locator('.compose-photo-row').count()==2)
    page.screenshot(path=str(OUT/'composer-desktop.png'),full_page=True)
    page.set_viewport_size({'width':390,'height':1000});page.screenshot(path=str(OUT/'composer-mobile.png'),full_page=True);ck('composer_mobile_no_overflow',page.evaluate('() => document.documentElement.scrollWidth<=innerWidth+1'))
    page.set_viewport_size({'width':1440,'height':1050});page.locator('#compose-submit').click();page.locator('#compose-dialog').wait_for(state='hidden');page.locator('#plain-editor').wait_for(state='visible')
    records=ctx.request.get('http://127.0.0.1:58207/api/projects').json();pid=next(x['id'] for x in records if x['title']=='Один исходник — три версии');p=ctx.request.get('http://127.0.0.1:58207/api/projects/'+pid).json()
    ck('three_selected_variants_generated',all(d['body'] for d in p['drafts'].values()))
    ck('generated_variants_are_drafts',all(d['state']=='DRAFT' for d in p['drafts'].values()))
    ck('source_preserved',p['source']['text']==source)
    ck('photos_shared_but_orders_separate',all(len(d['media'])==2 for d in p['drafts'].values()))
    ck('vk_is_plain_text_and_article_structured','<p>' not in p['drafts']['vk']['body'] and '<h2>' in p['drafts']['article']['body'])
    mobile_width=page.locator('.vk-preview').bounding_box()['width'];page.locator('#preview-desktop').click();wide_width=page.locator('.vk-preview').bounding_box()['width'];ck('pc_preview_actually_wider',wide_width>mobile_width)
    page.locator('#preview-mobile').click();page.locator('#approve-button').click();page.locator('#draft-state').get_by_text('Готово',exact=True).wait_for();page.locator('#vk-prepare').click();page.locator('#vk-confirm-dialog').wait_for(state='visible')
    ck('blocked_photo_defaults_to_explicit_fallback',page.locator('#vk-delivery-mode').input_value()=='publish_with_fallback')
    ck('schedule_warning_visible','иначе VK опубликует текст без них' in page.locator('#vk-delivery-explanation').inner_text())
    ck('nothing_sent_before_confirmation',not page.locator('#vk-confirm-send').is_enabled())
    page.locator('#vk-schedule-time').fill((datetime.now()+timedelta(days=2)).strftime('%Y-%m-%dT%H:%M'))
    ck('time_change_invalidates_confirmation',page.locator('#vk-ack').is_disabled() and not page.locator('#vk-confirm-send').is_enabled())
    page.locator('#vk-reprepare').click();page.wait_for_function('() => !document.querySelector("#vk-ack").disabled');page.locator('#vk-ack').check()
    page.screenshot(path=str(OUT/'schedule-confirm-mock.png'),full_page=True)
    page.locator('#vk-confirm-send').click();page.locator('#vk-attempt-status').get_by_text('Текст в отложке VK. Добавьте фотографии.',exact=True).wait_for()
    ck('scheduled_success_only_after_mock_receipt',True)
    with page.expect_download() as info:page.locator('#vk-download-photos').click()
    info.value.save_as(str(OUT/'formatted-photos-mock.zip'))
    with zipfile.ZipFile(OUT/'formatted-photos-mock.zip') as z:
        ck('formatted_ordered_jpg_package',Image.open(io.BytesIO(z.read('01.jpg'))).size==(1200,1200) and '02.jpg' in z.namelist())
        ck('package_contains_exact_post_text',z.read('post.txt').decode()==p['drafts']['vk']['body'])
    page.screenshot(path=str(OUT/'schedule-result-mock.png'),full_page=True)
    page.locator('#vk-confirm-close').click();page.locator('#home-button').click();page.locator('#welcome-new').click();page.locator('#compose-source').fill(source)
    page.locator('[data-compose-channel="news"]').uncheck();page.locator('[data-compose-channel="article"]').uncheck();page.locator('#compose-title').fill('Только VK — QA');page.locator('#compose-submit').click();page.locator('#compose-dialog').wait_for(state='hidden')
    p=ctx.request.get('http://127.0.0.1:58207/api/projects/'+next(x['id'] for x in ctx.request.get('http://127.0.0.1:58207/api/projects').json() if x['title']=='Только VK — QA')).json()
    ck('unchecked_channels_stay_empty',not p['drafts']['news']['body'] and not p['drafts']['article']['body'])
    ck('no_script_errors',not errors)
    browser.close()
(OUT/'BROWSER_QA.json').write_text(json.dumps({'pass':len(checks),'fail':0,'checks':checks,'scope':'synthetic source/photos, MOCK VK scheduled receipt only'},ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'pass':len(checks),'fail':0}))
