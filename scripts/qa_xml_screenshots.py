#!/usr/bin/env python3
from pathlib import Path
from urllib.parse import urljoin

import os
BASE = os.environ.get("PREVIEW_BASE_URL", "http://127.0.0.1:58095")
PASSWORD = os.environ.get("PREVIEW_ADMIN_PASSWORD")
if not PASSWORD:
    raise SystemExit("PREVIEW_ADMIN_PASSWORD is required")
ROOT = Path(__file__).resolve().parents[1]
SHOTS = ROOT / "reports" / "screenshots"
SHOTS.mkdir(parents=True, exist_ok=True)

from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = None
    for channel in ("msedge", "chrome"):
        try:
            browser = p.chromium.launch(channel=channel)
            break
        except Exception:
            continue
    if browser is None:
        raise SystemExit("no local Edge/Chrome")
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.goto(urljoin(BASE, "/login"))
    page.fill("input[name=username]", "preview")
    page.fill("input[name=password]", PASSWORD)
    page.click("button")
    page.wait_for_timeout(800)
    page.goto(urljoin(BASE, "/exports/xml"))
    page.wait_for_timeout(400)
    page.screenshot(path=str(SHOTS / "xml_export_form.png"), full_page=True)
    page.goto(urljoin(BASE, "/exports/xml?mode=canonical&action=preview"))
    page.wait_for_timeout(600)
    page.screenshot(path=str(SHOTS / "xml_preview.png"), full_page=True)
    browser.close()
print("XML_SCREENSHOTS_PASS")
