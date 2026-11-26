import json
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT.parents[1]/'reports/CONTENT_STUDIO_REASONING_20261111'
checks=[]
SOURCE=(ROOT/'tests/fixtures/belmash-b3499-source.html').read_text(encoding='utf-8')


def check(name,value):
    checks.append({'name':name,'pass':bool(value)})
    assert value,name


with sync_playwright() as pw:
    browser=pw.chromium.launch(executable_path='C:/Users/man503/AppData/Local/ms-playwright/chromium-1228/chrome-win64/chrome.exe',headless=True)
    ctx=browser.new_context(viewport={'width':1280,'height':1000});page=ctx.new_page();errors=[];external=[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    page.on('request',lambda r:external.append(r.url) if not r.url.startswith(('http://127.0.0.1:58207/','data:','about:')) else None)
    page.goto('http://127.0.0.1:58207/');page.locator('#login-code').fill('qa-browser-local');page.locator('#login-submit').click()
    page.locator('#ai-settings').click();page.locator('#ai-add-profile').click();page.locator('#ai-preset').select_option('glm_coding')
    page.locator('#ai-model').fill('glm-5.3');page.locator('#ai-key').fill('QA_FAKE_STREAM_KEY_NEVER_EXTERNAL_1234');page.locator('#ai-save').click()
    page.locator('#ai-status').get_by_text('Сохранено на этом компьютере.').wait_for()
    page.locator('#ai-tab-rules').click();page.locator('#ai-rules-import').click()
    page.wait_for_function("() => document.querySelectorAll('#ai-rules-list .editorial-rule').length===12")
    check('news_pack_imported',page.locator('#ai-rules-count').inner_text().startswith('Включено 12 из 12'))
    page.locator('#ai-rule-add').click();page.locator('#ai-rule-title').fill('Моё правило');page.locator('#ai-rule-text').fill('Пиши понятно.');page.locator('#ai-rule-save').click()
    page.locator('#ai-rule-form').wait_for(state='hidden');rule=page.locator('#ai-rules-list .editorial-rule').filter(has_text='Моё правило')
    check('custom_rule_added',rule.count()==1)
    rule.get_by_role('button',name='Изменить',exact=True).click();page.locator('#ai-rule-text').fill('Пиши понятно. Не выдумывай факты.');page.locator('#ai-rule-save').click();page.locator('#ai-rule-form').wait_for(state='hidden')
    rule.locator('summary').click();check('custom_rule_updated','Не выдумывай факты' in rule.inner_text())
    rule.get_by_role('checkbox').uncheck();page.locator('#ai-rules-status').get_by_text('Сохранено локально.').wait_for()
    check('rule_can_be_disabled',not rule.get_by_role('checkbox').is_checked())
    rule.get_by_role('button',name='Выше: Моё правило').click();page.wait_for_timeout(100)
    check('rule_order_updated','Моё правило' in page.locator('#ai-rules-list .editorial-rule').nth(11).inner_text())
    rule.get_by_role('button',name='Удалить',exact=True).click();page.wait_for_timeout(100)
    page.locator('#ai-rules-trash-title').click();deleted=page.locator('#ai-rules-trash-list .editorial-rule').filter(has_text='Моё правило')
    check('rule_moves_to_trash',deleted.count()==1)
    deleted.get_by_role('button',name='Восстановить',exact=True).click();page.wait_for_timeout(100)
    check('rule_restored',page.locator('#ai-rules-list .editorial-rule').filter(has_text='Моё правило').count()==1)
    page.locator('#ai-rules-list .editorial-rule').filter(has_text='Моё правило').get_by_role('button',name='Удалить',exact=True).click();page.wait_for_timeout(100)
    page.once('dialog',lambda d:d.accept());page.locator('#ai-rules-trash-list .editorial-rule').get_by_role('button',name='Удалить навсегда').click();page.wait_for_timeout(100)
    check('explicit_permanent_delete',page.locator('#ai-rules-trash-list .editorial-rule').count()==0)
    for width in (320,390,960,1920):
        page.set_viewport_size({'width':width,'height':900});check('rules_fit_'+str(width),page.locator('#ai-dialog').evaluate('(e)=>e.scrollWidth<=e.clientWidth+1'))
        if width==960:page.screenshot(path=str(OUT/'rules-news-960.png'))
    page.locator('#ai-rules-channel').select_option('vk');check('vk_independent_initially_empty',page.locator('#ai-rules-list .editorial-rule').count()==0)
    page.locator('#ai-rules-import').click();page.wait_for_function("() => document.querySelectorAll('#ai-rules-list .editorial-rule').length===8")
    check('vk_rules_imported',page.locator('#ai-rules-count').inner_text().startswith('Включено 8 из 8'))
    page.screenshot(path=str(OUT/'rules-vk-960.png'));page.locator('#ai-close').click();page.set_viewport_size({'width':1280,'height':1000})
    page.locator('#welcome-new').click();page.locator('#compose-source').fill(SOURCE);page.wait_for_function("() => document.querySelector('#compose-tags').value.includes('#BELMASH')")
    page.get_by_role('button',name='Удалить #BELMASH',exact=True).click()
    check('tag_cross_removes_selection','#BELMASH' not in page.locator('#compose-tags').input_value().split())
    page.locator('#compose-title').fill('BELMASH — проверка удаления');page.wait_for_timeout(650)
    check('removed_tag_not_readded_by_autodetection',page.get_by_role('button',name='Удалить #BELMASH',exact=True).count()==0)
    page.locator('#compose-use-detected').click();page.get_by_role('button',name='Удалить #BELMASH',exact=True).wait_for()
    check('explicit_use_detected_restores_tag','#BELMASH' in page.locator('#compose-tags').input_value().split())
    page.screenshot(path=str(OUT/'tag-crosses.png'))
    page.locator('#compose-news-mode').select_option('rewrite');page.locator('#compose-engine').select_option('ai');page.locator('[data-compose-channel="article"]').check()
    check('route_notes_on_separate_lines',page.locator('#compose-ai-target small').first.evaluate("e=>getComputedStyle(e).display==='block'"))
    page.locator('#compose-ai-ack').check();page.locator('#compose-submit').click()
    page.locator('#compose-reasoning-text').get_by_text('Учебный пример:',exact=False).wait_for()
    check('thoughts_arrive_before_result',page.locator('#compose-dialog').is_visible() and page.locator('#compose-submit').is_disabled())
    page.locator('#compose-reasoning summary').click();check('live_panel_can_collapse',not page.locator('#compose-reasoning').evaluate('(e)=>e.open'))
    page.locator('#compose-reasoning summary').click();check('thoughts_are_plain_text',page.locator('#compose-reasoning-text script').count()==0)
    page.screenshot(path=str(OUT/'live-reasoning.png'))
    page.locator('#compose-dialog').wait_for(state='hidden')
    page.locator('#detect-editor-tags').click();page.locator('#editor-tag-candidates').get_by_role('button',name='Удалить #BELMASH',exact=True).click()
    check('editor_tag_cross_updates_footer','#BELMASH' not in page.locator('#publication-tags').input_value().split() and '#BELMASH' not in page.locator('#plain-editor').input_value().split())
    page.locator('#show-ai-reasoning').click();page.locator('#reasoning-dialog').wait_for(state='visible')
    check('last_trace_available_after_success','Учебный пример:' in page.locator('#reasoning-history-text').inner_text())
    check('trace_context_separates_routes','Запрос 3/3' in page.locator('#reasoning-history-text').inner_text())
    for width in (390,960):
        page.set_viewport_size({'width':width,'height':900});check('trace_dialog_fits_'+str(width),page.locator('#reasoning-dialog').evaluate('(e)=>e.scrollWidth<=e.clientWidth+1'))
        page.screenshot(path=str(OUT/f'last-reasoning-{width}.png'))
    page.locator('#reasoning-close').click()
    page.locator('[data-tab="news"]').click();page.locator('#news-review-button').click();page.locator('#news-review-dialog').wait_for(state='visible')
    check('news_review_is_not_claimed_fact_check','Факты требуют ручного подтверждения' in page.locator('#news-review-content').inner_text())
    page.screenshot(path=str(OUT/'news-review.png'));page.locator('#news-review-close').click()
    page.locator('#home-button').click();page.locator('#welcome-new').click();page.locator('#compose-source').fill('QA:fail\n\nСинтетический исходник.');page.locator('#compose-engine').select_option('ai');page.locator('#compose-ai-ack').check();page.locator('#compose-submit').click()
    page.locator('#compose-ai-error').wait_for(state='visible');check('partial_thoughts_preserved_on_error','Учебный пример:' in page.locator('#compose-reasoning-text').inner_text())
    for width,height in ((320,640),(390,844),(960,540),(1920,1080)):
        page.set_viewport_size({'width':width,'height':height})
        check('live_trace_no_footer_overlap_'+str(width),page.locator('#compose-reasoning').evaluate('''e=>{
          const a=e.getBoundingClientRect(), b=document.querySelector('#compose-submit').getBoundingClientRect();
          return a.bottom<=b.top && a.left>=0 && a.right<=innerWidth;
        }'''))
    page.set_viewport_size({'width':960,'height':900})
    page.locator('#compose-close').click();page.locator('#welcome-new').click();page.locator('#compose-source').fill('QA:none\n\nСинтетический исходник.');page.locator('[data-compose-channel="news"]').uncheck();page.locator('#compose-engine').select_option('ai');page.locator('#compose-ai-ack').check();page.locator('#compose-submit').click();page.locator('#compose-dialog').wait_for(state='hidden')
    page.locator('#show-ai-reasoning').click();check('no_fake_thoughts_when_provider_has_none','не передал' in page.locator('#reasoning-history-text').inner_text());page.locator('#reasoning-close').click()
    page.locator('#home-button').click();page.locator('#welcome-new').click();page.locator('#compose-source').fill('Синтетический исходник.');page.locator('[data-compose-channel="news"]').uncheck();page.locator('#compose-engine').select_option('ai');page.locator('#compose-show-reasoning').uncheck();page.locator('#compose-ai-ack').check();page.locator('#compose-submit').click();page.locator('#compose-dialog').wait_for(state='hidden')
    check('display_can_be_disabled',not page.locator('#show-ai-reasoning').is_visible())
    check('no_script_errors',not errors);check('no_external_browser_requests',not external)
    browser.close()
(OUT/'BROWSER_QA.json').write_text(json.dumps({'pass':len(checks),'fail':0,'checks':checks,'scope':'isolated fake AI/VK; no live generation'},ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'pass':len(checks),'fail':0}))
