"""Layout regression on isolated 58207; fake AI/VK, no external requests."""
import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT.parents[1] / 'reports/CONTENT_STUDIO_RESPONSIVE_20261111'
parser = argparse.ArgumentParser()
parser.add_argument('--baseline', action='store_true')
args = parser.parse_args()
checks = []
external = []
errors = []
sizes = [(320, 640), (390, 844), (640, 720), (768, 800), (960, 540),
         (960, 800), (1280, 800), (1440, 900), (1920, 1080), (2560, 1440)]


def check(name, value, detail=None):
    checks.append({'name': name, 'pass': bool(value), 'detail': detail})


def layout(page, selector, name):
    result = page.locator(selector).evaluate('''root => {
      const box = root.getBoundingClientRect();
      const controls = [...root.querySelectorAll('input,select,textarea,button')]
        .filter(e => e.getClientRects().length);
      const checkboxes = controls.filter(e => e.type === 'checkbox').map(e => {
        const r = e.getBoundingClientRect(), label = e.closest('label').getBoundingClientRect();
        return {id:e.id, width:r.width, height:r.height, labelHeight:label.height};
      });
      const clipped = controls.filter(e => {
        const r = e.getBoundingClientRect();
        return r.left < box.left-1 || r.right > box.right+1;
      }).map(e=>e.id);
      return {width:box.width, height:box.height, scrollWidth:root.scrollWidth,
        clientWidth:root.clientWidth, clipped, checkboxes,
        inViewport:box.left>=0 && box.right<=innerWidth+1 && box.top>=-1 && box.bottom<=innerHeight+1};
    }''')
    check(name + '_inside_viewport', result['inViewport'], result)
    check(name + '_no_horizontal_scroll', result['scrollWidth'] <= result['clientWidth'] + 1)
    check(name + '_controls_fit', not result['clipped'], result['clipped'])
    check(name + '_checkboxes_compact', all(12 <= x['width'] <= 24 and 12 <= x['height'] <= 24
          and x['labelHeight'] < 170 for x in result['checkboxes']), result['checkboxes'])


with sync_playwright() as pw:
    browser = pw.chromium.launch(
        executable_path='C:/Users/man503/AppData/Local/ms-playwright/chromium-1228/chrome-win64/chrome.exe',
        headless=True)
    context = browser.new_context(viewport={'width': 1280, 'height': 900})
    page = context.new_page()
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.on('request', lambda r: external.append(r.url)
            if not r.url.startswith(('http://127.0.0.1:58207/', 'data:', 'about:')) else None)
    page.goto('http://127.0.0.1:58207/')
    page.locator('#login-code').fill('qa-browser-local')
    page.locator('#login-submit').click()
    page.locator('#ai-settings').click()
    page.locator('#ai-add-profile').click()
    page.locator('#ai-preset').select_option('glm')
    page.locator('#ai-name').fill('GLM для новости, статьи и поста VK — проверка длинного названия')
    page.locator('#ai-model').fill('glm-5.3')
    page.locator('#ai-key').fill('QA_FAKE_RESPONSIVE_NEVER_EXTERNAL_123456789')
    page.locator('#ai-save').click()
    page.locator('#ai-status').get_by_text('Сохранено на этом компьютере.').wait_for()
    for w, h in sizes:
        page.set_viewport_size({'width': w, 'height': h})
        layout(page, '#ai-dialog', f'profile_{w}x{h}')
        page.locator('#ai-enabled').uncheck()
        page.locator('#ai-enabled').check()
        check(f'profile_checkbox_click_{w}x{h}', page.locator('#ai-enabled').is_checked())
        page.locator('#ai-profile-form details summary').click()
        layout(page, '#ai-dialog', f'profile_advanced_{w}x{h}')
        page.locator('#ai-profile-form details summary').click()
        if w in (390, 960, 1920) and h != 540:
            page.locator('#ai-enabled').scroll_into_view_if_needed()
            page.screenshot(path=str(OUT / f'{"before" if args.baseline else "after"}-profile-{w}.png'))
    page.set_viewport_size({'width': 1280, 'height': 900})
    page.locator('#ai-profile-cancel').click()
    page.locator('#ai-tab-tasks').click()
    page.locator('#ai-task-list .ai-card').filter(has_text='Статья для сайта').get_by_role(
        'button', name='Настроить задачу').click()
    page.locator('#ai-task-instructions').fill('Пиши по исходнику. Подписывай каждую характеристику.')
    for w, h in sizes:
        page.set_viewport_size({'width': w, 'height': h})
        layout(page, '#ai-dialog', f'task_{w}x{h}')
    page.locator('#ai-task-cancel').click()
    page.locator('#ai-tab-check').click()
    page.locator('#ai-run-check').click()
    page.locator('#ai-check-result').wait_for(state='visible')
    for w, h in sizes:
        page.set_viewport_size({'width': w, 'height': h})
        layout(page, '#ai-dialog', f'offline_{w}x{h}')
    page.locator('#ai-close').click()
    page.locator('#welcome-new').click()
    source = 'BELMASH B3499: настольный токарный станок. Потребляемая мощность — 600 Вт.'
    page.locator('#compose-source').fill(source)
    page.locator('#compose-engine').select_option('ai')
    for w, h in sizes:
        page.set_viewport_size({'width': w, 'height': h})
        layout(page, '#compose-dialog', f'composer_{w}x{h}')
        page.locator('#compose-reviewed').check()
        page.locator('#compose-reviewed').uncheck()
        check(f'composer_checkbox_click_{w}x{h}', not page.locator('#compose-reviewed').is_checked())
        if w in (390, 960, 1920) and h != 540:
            page.locator('#compose-engine').scroll_into_view_if_needed()
            page.screenshot(path=str(OUT / f'{"before" if args.baseline else "after"}-compose-{w}.png'))
    check('source_preserved_through_resize', page.locator('#compose-source').input_value() == source)
    check('no_external_requests', not external)
    check('no_javascript_errors', not errors, errors)
    browser.close()

result = {'pass': sum(x['pass'] for x in checks), 'fail': sum(not x['pass'] for x in checks),
          'scope': 'isolated fake AI/VK; ten viewports; no paid calls or publications', 'checks': checks}
OUT.mkdir(parents=True, exist_ok=True)
(OUT / ('BASELINE_QA.json' if args.baseline else 'BROWSER_QA.json')).write_text(
    json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({'pass': result['pass'], 'fail': result['fail']}))
if not args.baseline:
    assert not result['fail'], [x['name'] for x in checks if not x['pass']]
