"""UX workflows with synthetic fixtures against the local mock-only QA server."""
from pathlib import Path
import io,json
from PIL import Image,ImageDraw,ImageFont
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT.parents[1]/'reports/CONTENT_STUDIO_WORKFLOW_20261106/regression-ux';OUT.mkdir(exist_ok=True,parents=True)
checks=[]
def ck(name,value):checks.append({'name':name,'pass':bool(value)});assert value,name

with sync_playwright() as pw:
    browser=pw.chromium.launch(executable_path='C:/Users/man503/AppData/Local/ms-playwright/chromium-1228/chrome-win64/chrome.exe',headless=True)
    ctx=browser.new_context(viewport={'width':1600,'height':1050},accept_downloads=True);page=ctx.new_page();errors=[];external=[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    page.on('request',lambda r:external.append(r.url) if not r.url.startswith(('http://127.0.0.1:58207/','data:','about:')) else None)
    page.goto('http://127.0.0.1:58207/');page.locator('#login-code').fill('qa-browser-local');page.locator('#login-submit').click()
    page.locator('.dashboard-stats').wait_for(state='visible')
    rows=ctx.request.get('http://127.0.0.1:58207/api/projects').json()
    ck('dashboard_real_counts',int(page.locator('#stat-projects').inner_text())==len(rows))
    page.locator('[data-start-channel="article"]').click();page.locator('#compose-dialog').wait_for(state='visible');ck('quick_start_selects_composer_channel',page.locator('[data-compose-channel="article"]').is_checked() and not page.locator('[data-compose-channel="vk"]').is_checked());page.locator('#compose-close').click();page.locator('#blank-project').click();page.locator('[data-tab="article"]').click();page.locator('#visual-editor').wait_for(state='visible')
    ck('quick_start_opens_requested_channel',page.locator('[data-tab="article"]').get_attribute('aria-selected')=='true')
    page.locator('#project-title').fill('Подготовка мастерской к сезону · демо')
    page.locator('[data-tab="source"]').click()
    raw='<h2>Порядок начинается с деталей</h2><p>Подготовьте рабочее место, составьте список задач и проверьте документацию оборудования.</p><script>window.uxUnsafe=true</script>'
    page.locator('#source-text').fill(raw);page.locator('#use-source').click();page.locator('#source-dialog').wait_for(state='visible')
    page.locator('#source-destination').select_option('news')
    ck('source_import_preview_is_sanitized','<script>' not in page.locator('#source-import-preview').inner_text())
    page.locator('#source-import-apply').click();page.locator('#visual-editor h2').wait_for()
    ck('source_import_target_and_draft',page.locator('[data-tab="news"]').get_attribute('aria-selected')=='true' and page.locator('#draft-state').inner_text()=='Черновик')
    ck('source_script_not_executed',page.evaluate('() => window.uxUnsafe===undefined'))
    page.locator('#draft-title').fill('Подготовка мастерской к сезону')
    page.locator('#announcement').fill('Рабочее пространство, полезные привычки и внимательность к деталям.')
    page.locator('[data-tab="source"]').click();page.locator('#add-fact').click();fact=page.locator('.fact textarea')
    fact.fill('Первое изменение факта');page.wait_for_timeout(1200)
    fact.fill('Демонстрационный материал для проверки интерфейса.');page.wait_for_timeout(1200)
    page.locator('.fact select').select_option('CONFIRMED');page.locator('.fact input').first.fill('Локальный UX-сценарий, не коммерческая публикация')
    page.locator('#save-button').click()
    records=ctx.request.get('http://127.0.0.1:58207/api/projects').json();pid=next(r['id'] for r in records if r['title']=='Подготовка мастерской к сезону · демо')
    saved=ctx.request.get(f'http://127.0.0.1:58207/api/projects/{pid}').json()
    ck('repeat_fact_edit_after_autosave_persists',saved['facts'][0]['text']=='Демонстрационный материал для проверки интерфейса.')
    ck('source_original_retained',saved['source']['text']==raw)
    page.locator('[data-tab="news"]').click()
    files=[]
    for n,color in enumerate(['#dce4cf','#c8d9d1','#e9dfc6'],1):
        image=Image.new('RGB',(1000,800),color);draw=ImageDraw.Draw(image)
        font=ImageFont.truetype('C:/Windows/Fonts/segoeui.ttf',43);small=ImageFont.truetype('C:/Windows/Fonts/segoeui.ttf',21)
        draw.rounded_rectangle((160,140,840,625),radius=8,outline='#748771',width=2)
        for x in range(220,830,95):draw.line((x,210,x,540),fill='#b0bea0',width=1)
        for y in range(230,540,75):draw.line((220,y,790,y),fill='#b0bea0',width=1)
        draw.text((80,65),'STUDIO / VISUAL STUDY',fill='#63775b',font=small)
        draw.text((210,290),f'WORKSHOP   /   0{n}',fill='#314c35',font=font)
        draw.text((80,700),'DEMONSTRATION ASSET · NOT A PRODUCT PHOTO',fill='#75866a',font=small)
        buffer=io.BytesIO();image.save(buffer,'PNG');files.append({'name':f'workshop-demo-0{n}.png','mimeType':'image/png','buffer':buffer.getvalue()})
    page.locator('#photo-input').set_input_files(files);page.wait_for_function('() => document.querySelectorAll(".photo-card").length===3&&!document.getElementById("workspace").inert')
    page.locator('#bulk-photo-details summary').click();page.locator('#bulk-origin').fill('Синтетические UX-иллюстрации');page.locator('#bulk-rights').fill('Созданы только для проверки интерфейса');page.locator('#apply-photo-metadata').click()
    page.locator('.photo-card').nth(2).locator('.first-photo').click()
    ck('make_first_photo',page.locator('.photo-filename').first.inner_text()=='workshop-demo-03.png')
    alt=page.locator('.photo-meta input[aria-label="Подпись / alt"]').first
    alt.fill('Первая подпись');page.wait_for_timeout(1200);alt.fill('Подпись после автосохранения');page.wait_for_timeout(1200)
    page.keyboard.press('Control+s');page.wait_for_function('() => document.querySelector("#save-status").textContent==="Сохранено"')
    saved=ctx.request.get(f'http://127.0.0.1:58207/api/projects/{pid}').json();aid=saved['drafts']['news']['media'][0]
    ck('repeat_photo_edit_after_autosave_persists',next(a for a in saved['media'] if a['id']==aid)['alt']=='Подпись после автосохранения')
    ck('bulk_rights_persist',all(a['rights']=='Созданы только для проверки интерфейса' for a in saved['media']))
    ck('readiness_matches_server_conditions',page.locator('#readiness-score').inner_text()=='3 / 3' and page.locator('#approve-button').is_enabled())
    page.locator('#focus-button').click();ck('focus_mode_hides_sidebar',not page.locator('.sidebar').is_visible())
    page.keyboard.press('Escape');ck('escape_restores_sidebar',page.locator('.sidebar').is_visible())
    page.keyboard.press('Control+k');ck('search_shortcut',page.locator('#search').evaluate('(el) => document.activeElement===el'))
    page.locator('#search').fill('не существующий материал');ck('search_empty_feedback','ничего нет' in page.locator('#project-list').inner_text());page.locator('#search').fill('')
    page.locator('#approve-button').click();page.wait_for_function('() => document.querySelector("#draft-state").textContent==="Готово"')
    page.locator('#project-filter').select_option('ready');page.wait_for_function('(id) => [...document.querySelectorAll(".project-item")].some(x=>x.dataset.projectId===id)',arg=pid);ck('ready_filter_includes_material',page.locator('.project-item[data-project-id="'+pid+'"]').count()==1)
    page.locator('#project-filter').select_option('all')
    page.locator('[data-tab="news"]').focus();page.keyboard.press('ArrowRight');page.wait_for_function('() => document.querySelector("[data-tab=article]").getAttribute("aria-selected")==="true"')
    ck('keyboard_tabs_next',page.locator('[data-tab="article"]').get_attribute('aria-selected')=='true')
    page.keyboard.press('ArrowLeft');page.wait_for_function('() => document.querySelector("[data-tab=news]").getAttribute("aria-selected")==="true"')
    ck('keyboard_tabs_previous',page.locator('[data-tab="news"]').get_attribute('aria-selected')=='true')
    bounds=page.locator('.project-item').evaluate_all('(items) => items.map(el=>({top:el.getBoundingClientRect().top,bottom:el.getBoundingClientRect().bottom}))')
    ck('long_sidebar_items_do_not_overlap',all(bounds[i]['bottom']<=bounds[i+1]['top']+1 for i in range(len(bounds)-1)))

    page.wait_for_timeout(200)
    ck('preview_has_readable_local_styles',page.frame_locator('#site-preview').locator('body').evaluate('(el) => getComputedStyle(el).fontFamily.includes("Segoe UI")'))
    ck('preview_includes_selected_photos',page.frame_locator('#site-preview').locator('img').count()==3)
    for width in [1600,1440,1280,1024,768,650,390,360]:
        page.set_viewport_size({'width':width,'height':1050});page.evaluate('() => window.scrollTo(0,0)');page.wait_for_timeout(150)
        ck('no_overflow_'+str(width),page.evaluate('() => document.documentElement.scrollWidth<=innerWidth+1'))
        if width==1440:
            page.screenshot(path=str(OUT/'editor-desktop.png'),full_page=True)
            page.screenshot(path=str(OUT/'editor-overview.png'))
        if width==390:page.screenshot(path=str(OUT/'editor-mobile.png'),full_page=True)
    page.set_viewport_size({'width':1600,'height':1050});page.locator('#duplicate-project').click();page.wait_for_function('() => document.querySelector("#project-title").value.startsWith("Копия")')
    ck('duplicate_is_new_draft',page.locator('#draft-state').inner_text()=='Черновик')
    copy_title=page.locator('#project-title').input_value()
    records=ctx.request.get('http://127.0.0.1:58207/api/projects').json();copy_id=next(r['id'] for r in records if r['title']==copy_title)
    server=ctx.request.get(f'http://127.0.0.1:58207/api/projects/{copy_id}').json();version=server['revision'];server['title']='Актуальная редакция на другом экране'
    token=ctx.request.get('http://127.0.0.1:58207/api/session').json()['csrf']
    changed=ctx.request.put(f'http://127.0.0.1:58207/api/projects/{copy_id}',data={'project':server,'expected_revision':version},headers={'X-CSRF-Token':token});assert changed.ok
    page.locator('#project-title').fill('Моя несохранённая правка');page.keyboard.press('Control+s');page.locator('#conflict-recovery').wait_for(state='visible')
    ck('conflict_offers_recovery',page.locator('#download-unsaved').is_visible())
    with page.expect_download() as info:page.locator('#download-unsaved').click()
    download=info.value;download.save_as(str(OUT/'unsaved-fixture.json'))
    ck('unsaved_snapshot_preserves_edits','Моя несохранённая правка' in (OUT/'unsaved-fixture.json').read_text(encoding='utf-8'))
    page.once('dialog',lambda d:d.accept());page.locator('#reload-current').click();page.wait_for_function('() => document.querySelector("#project-title").value==="Актуальная редакция на другом экране"')
    ck('conflict_can_open_current_after_confirmation',not page.locator('#conflict-recovery').is_visible())

    page.locator('#back-home').click();page.locator('#welcome').wait_for(state='visible');page.screenshot(path=str(OUT/'dashboard-desktop.png'),full_page=True)
    ck('dashboard_recent_cards',page.locator('.recent-card').count()>0)
    ck('no_script_errors',not errors);ck('no_external_requests',not external)
    browser.close()
(OUT/'BROWSER_QA.json').write_text(json.dumps({'pass':len(checks),'fail':0,'checks':checks,'scope':'synthetic UX data, mock-only VK, no external writes','errors':errors,'external_requests':external},ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'pass':len(checks),'fail':0}))
