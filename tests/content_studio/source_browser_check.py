"""Pasted customer HTML through the real UI, separate QA database, no publications."""
from pathlib import Path
import json

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT.parents[1]/'reports/CONTENT_STUDIO_SOURCE_20261106'
checks = []


def ck(name, condition):
    checks.append({'name': name, 'pass': bool(condition)})
    assert condition, name


with sync_playwright() as pw:
    browser = pw.chromium.launch(executable_path='C:/Users/man503/AppData/Local/ms-playwright/chromium-1228/chrome-win64/chrome.exe', headless=True)
    ctx = browser.new_context(viewport={'width': 1440, 'height': 1050})
    page = ctx.new_page()
    errors, external = [], []
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.on('request', lambda r: external.append(r.url) if not r.url.startswith(('http://127.0.0.1:58207/', 'data:', 'about:')) else None)
    page.goto('http://127.0.0.1:58207/')
    page.locator('#login-code').fill('qa-browser-local')
    page.locator('#login-submit').click()
    page.locator('#welcome-new').click()
    page.locator('#compose-source').fill((ROOT/'tests/fixtures/belmash-b3499-source.html').read_text(encoding='utf-8'))
    page.locator('[data-compose-channel="article"]').check()
    page.locator('#compose-submit').click()
    page.locator('#compose-dialog').wait_for(state='hidden')
    page.locator('#plain-editor').wait_for(state='visible')
    text = page.locator('#plain-editor').input_value()
    ck('no_orphan_600w', 'Потребляемая мощность: 600 Вт' in text and not text.endswith('\n\n600 Вт'))
    ck('work_area_labels_intact', 'Диаметр над станиной: 180 мм' in text and 'Расстояние между центрами: 340 мм' in text)
    ck('cover_qualifier_intact', 'С установленной защитой ход каретки составляет 280 мм.' in text)
    ck('no_missing_comparison_reference', 'Ниже — подробное сравнение' not in text)
    ck('meaningful_title', page.locator('#project-title').input_value().startswith('BELMASH B3499'))
    ck('unreviewed_stays_unready', not page.locator('#approve-button').is_enabled())
    page.locator('#preview-desktop').click()
    page.screenshot(path=str(OUT/'belmash-desktop.png'), full_page=True)
    page.set_viewport_size({'width': 390, 'height': 1000})
    page.locator('#preview-mobile').click()
    page.screenshot(path=str(OUT/'belmash-mobile.png'), full_page=True)
    ck('mobile_no_overflow', page.evaluate('() => document.documentElement.scrollWidth <= innerWidth + 1'))
    ck('preview_matches_editor', page.locator('#vk-text').inner_text() == text)
    page.set_viewport_size({'width': 1440, 'height': 1050})
    page.locator('[data-tab="article"]').click()
    ck('article_keeps_real_table', page.locator('#visual-editor table').count() == 1)
    ck('article_keeps_distinct_models', 'ML-714VS' in page.locator('#visual-editor table').inner_text() and 'B3499' in page.locator('#visual-editor table').inner_text())
    ck('article_keeps_qualification', 'до 340 мм без кожуха' in page.locator('#visual-editor').inner_text())
    page.screenshot(path=str(OUT/'belmash-article.png'), full_page=True)
    ck('no_external_requests', not external)
    ck('no_script_errors', not errors)
    browser.close()
(OUT/'BROWSER_QA.json').write_text(json.dumps({'pass': len(checks), 'fail': 0, 'checks': checks, 'scope': 'isolated QA data; pasted HTML only; no VK posting'}, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({'pass': len(checks), 'fail': 0}))

