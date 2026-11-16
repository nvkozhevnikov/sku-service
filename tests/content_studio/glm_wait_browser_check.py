import json
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT.parents[1] / 'reports/CONTENT_STUDIO_GLM_WAIT_20261111'
checks = []


def check(name, value):
    checks.append({'name': name, 'pass': bool(value)})
    assert value, name


with sync_playwright() as pw:
    browser = pw.chromium.launch(executable_path='C:/Users/man503/AppData/Local/ms-playwright/chromium-1228/chrome-win64/chrome.exe', headless=True)
    context = browser.new_context(viewport={'width': 960, 'height': 900})
    page = context.new_page()
    external, errors = [], []
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.on('request', lambda r: external.append(r.url) if not r.url.startswith(('http://127.0.0.1:58207/', 'data:', 'about:')) else None)
    page.goto('http://127.0.0.1:58207/')
    page.locator('#login-code').fill('qa-browser-local')
    page.locator('#login-submit').click()
    page.locator('#ai-settings').click()
    page.locator('#ai-add-profile').click()
    page.locator('#ai-preset').select_option('glm_coding')
    check('coding_preset_correct_endpoint', page.locator('#ai-base-url').input_value() == 'https://api.z.ai/api/coding/paas/v4')
    check('coding_help_matches_selection', page.locator('#ai-provider-help-title').inner_text() == 'GLM Coding Plan'
        and 'Standard API Key' not in page.locator('#ai-provider-help-text').inner_text())
    page.locator('#ai-model').fill('glm-5.3')
    page.locator('#ai-key').fill('QA_LOCAL_FAKE_WAIT_KEY_123456789')
    page.locator('#ai-profile-form details summary').click()
    check('default_wait_and_auto_mode', page.locator('#ai-timeout').input_value() == '300'
          and page.locator('#ai-reasoning').input_value() == 'auto')
    page.locator('#ai-timeout').fill('420')
    page.locator('#ai-reasoning').select_option('low')
    page.locator('#ai-save').click()
    page.locator('#ai-status').get_by_text('Сохранено на этом компьютере.').wait_for()
    page.locator('#ai-profile-cancel').click()
    page.locator('#ai-profile-list .ai-card').get_by_role('button', name='Настроить').click()
    check('saved_settings_restored', page.locator('#ai-timeout').input_value() == '420'
          and page.locator('#ai-reasoning').input_value() == 'low')
    for width in (390, 960):
        page.set_viewport_size({'width': width, 'height': 900})
        check('settings_fit_' + str(width), page.locator('#ai-dialog').evaluate('(e) => e.scrollWidth <= e.clientWidth + 1'))
        page.locator('#ai-timeout').scroll_into_view_if_needed()
        page.screenshot(path=str(OUT / f'settings-{width}.png'))
    page.locator('#ai-close').click()
    page.locator('#welcome-new').click()
    source = 'QA:timeout\n\nНастольный токарный станок. Потребляемая мощность — 600 Вт.'
    page.locator('#compose-source').fill(source)
    page.locator('#compose-engine').select_option('ai')
    page.locator('[data-compose-channel="article"]').check()
    check('route_wait_visible', '420 с.' in page.locator('#compose-ai-target').inner_text())
    page.locator('#compose-ai-ack').check()
    page.locator('#compose-submit').click()
    page.locator('#compose-status').get_by_text('Ожидаем ответ ИИ', exact=False).wait_for()
    check('pending_message_matches_ai_mode', 'шаблонам' not in page.locator('#compose-status').inner_text())
    page.locator('#compose-ai-error').wait_for(state='visible')
    message = page.locator('#compose-api-error-text').inner_text()
    check('timeout_explained', '420 с' in message and 'сетевого ожидания' in message and 'HTTP None' not in message)
    check('source_and_channels_preserved', page.locator('#compose-source').input_value() == source
          and page.locator('[data-compose-channel]:checked').count() == 3)
    check('controls_enabled_after_error', page.locator('#compose-submit').is_enabled()
          and page.locator('#compose-close').is_enabled())
    page.wait_for_timeout(1100)
    check('pending_timer_stopped', 'Ожидаем ответ ИИ' not in page.locator('#compose-status').inner_text()
          and page.locator('#compose-ai-error').is_visible())
    for width in (390, 960):
        page.set_viewport_size({'width': width, 'height': 900})
        page.locator('#compose-ai-error').scroll_into_view_if_needed()
        check('timeout_card_fits_' + str(width), page.locator('#compose-ai-error').evaluate('(e) => e.scrollWidth <= e.clientWidth + 1'))
        page.screenshot(path=str(OUT / f'timeout-{width}.png'))
    check('no_external_requests', not external)
    check('no_script_errors', not errors)
    browser.close()

(OUT / 'BROWSER_QA.json').write_text(json.dumps({'pass': len(checks), 'fail': 0, 'checks': checks,
    'scope': 'isolated fake AI/VK; simulated timeout; no real model calls'}, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({'pass': len(checks), 'fail': 0}))
