import io
import json
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT.parents[1] / 'reports/CONTENT_STUDIO_AI_ERRORS_20261111'
checks = []


def check(name, condition):
    checks.append({'name': name, 'pass': bool(condition)})
    assert condition, name


with sync_playwright() as pw:
    browser = pw.chromium.launch(executable_path='C:/Users/man503/AppData/Local/ms-playwright/chromium-1228/chrome-win64/chrome.exe', headless=True)
    context = browser.new_context(viewport={'width': 960, 'height': 800})
    page = context.new_page()
    errors, external = [], []
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.on('request', lambda r: external.append(r.url)
        if not r.url.startswith(('http://127.0.0.1:58207/', 'data:', 'about:')) else None)
    page.goto('http://127.0.0.1:58207/')
    page.locator('#login-code').fill('qa-browser-local')
    page.locator('#login-submit').click()
    page.locator('#ai-settings').click()
    page.locator('#ai-add-profile').click()
    page.locator('#ai-preset').select_option('glm')
    check('zai_key_type_guidance_visible', 'Standard API Key' in page.locator('#ai-provider-help').inner_text())
    page.locator('#ai-model').fill('glm-5.3')
    page.locator('#ai-key').fill('QA_FAKE_ERROR_KEY_NEVER_EXTERNAL_123456')
    page.locator('#ai-save').click()
    page.locator('#ai-status').get_by_text('Сохранено на этом компьютере.').wait_for()
    check('save_does_not_call_provider', context.request.get('http://127.0.0.1:58207/api/qa/transport-count').json()['calls'] == 0)
    page.screenshot(path=str(OUT / 'connection-help-960.png'))
    page.locator('#ai-close').click()
    page.locator('#welcome-new').click()
    source = 'QA:balance\n\nНастольный токарный станок. Потребляемая мощность 600 Вт.'
    page.locator('#compose-source').fill(source)
    page.locator('#compose-engine').select_option('ai')
    page.locator('[data-compose-channel="article"]').check()
    page.locator('#compose-ai-ack').check()
    picture = io.BytesIO()
    Image.new('RGB', (300, 200), '#e9eee5').save(picture, format='PNG')
    page.locator('#compose-file-input').set_input_files({'name': 'synthetic.png', 'mimeType': 'image/png', 'buffer': picture.getvalue()})
    page.locator('#compose-submit').click()
    page.locator('#compose-ai-error').wait_for(state='visible')
    text = page.locator('#compose-api-error-text').inner_text()
    check('business_code_and_balance_action_visible', '1113' in text and 'Standard API Key' in text)
    check('provider_message_hidden', 'PRIVATE_RESPONSE_MUST_STAY_HIDDEN' not in text)
    check('source_preserved_on_error', page.locator('#compose-source').input_value() == source)
    check('queued_photo_preserved_on_error', page.locator('.compose-photo-row').count() == 1)
    check('channels_preserved_on_error', page.locator('[data-compose-channel]:checked').count() == 3)
    check('no_project_saved_for_failed_generation', context.request.get('http://127.0.0.1:58207/api/projects').json() == [])
    page.screenshot(path=str(OUT / 'balance-example-960.png'))
    source = source.replace('QA:balance', 'QA:rate_limit')
    page.locator('#compose-source').fill(source)
    page.locator('#compose-submit').click()
    page.locator('#compose-api-error-text').get_by_text('1302', exact=False).wait_for()
    calls = context.request.get('http://127.0.0.1:58207/api/qa/transport-count').json()['calls']
    check('rate_limit_pause_explained', 'ручной попытки' in page.locator('#compose-api-error-text').inner_text())
    page.locator('#compose-submit').click()
    page.locator('#compose-submit').wait_for(state='visible')
    page.wait_for_function("() => !document.querySelector('#compose-submit').disabled")
    check('repeated_click_during_pause_does_not_call_provider', context.request.get('http://127.0.0.1:58207/api/qa/transport-count').json()['calls'] == calls)
    for width in (320, 390, 960, 1920):
        page.set_viewport_size({'width': width, 'height': 844 if width < 960 else 900})
        check('error_card_fits_' + str(width), page.locator('#compose-ai-error').evaluate('(e) => e.scrollWidth <= e.clientWidth + 1'))
        check('dialog_fits_' + str(width), page.locator('#compose-dialog').evaluate('(e) => e.getBoundingClientRect().right <= innerWidth && e.getBoundingClientRect().left >= 0'))
        if width in (390, 960):
            page.locator('#compose-ai-error').scroll_into_view_if_needed()
            page.screenshot(path=str(OUT / f'rate-limit-example-{width}.png'))
    page.locator('#compose-error-settings').click()
    page.locator('#ai-dialog').wait_for(state='visible')
    check('settings_open_without_losing_composer', page.locator('#compose-source').input_value() == source)
    page.locator('#ai-profile-list .ai-card').get_by_role('button', name='Настроить').click()
    page.locator('#ai-test-ack').check()
    page.locator('#ai-test').click()
    page.locator('#ai-status[data-error="true"]').wait_for(state='visible')
    check('test_uses_same_pause_without_request', '1302' in page.locator('#ai-status').inner_text()
        and context.request.get('http://127.0.0.1:58207/api/qa/transport-count').json()['calls'] == calls)
    page.locator('#ai-close').click()
    page.locator('#compose-engine').select_option('local')
    page.locator('#compose-submit').click()
    page.locator('#compose-dialog').wait_for(state='hidden')
    check('local_mode_works_while_provider_limited', len(context.request.get('http://127.0.0.1:58207/api/projects').json()) == 1
        and context.request.get('http://127.0.0.1:58207/api/qa/transport-count').json()['calls'] == calls)
    check('no_javascript_errors', not errors)
    check('no_external_browser_requests', not external)
    browser.close()

(OUT / 'BROWSER_QA.json').write_text(json.dumps({'pass': len(checks), 'fail': 0, 'checks': checks,
    'scope': 'isolated data, fake HTTP transport and credentials; no live GLM or VK'}, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({'pass': len(checks), 'fail': 0}))
