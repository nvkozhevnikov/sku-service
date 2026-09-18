#!/usr/bin/env python3
from pathlib import Path
from urllib.parse import urljoin

BASE = "http://127.0.0.1:58093"
ROOT = Path(__file__).resolve().parents[1]
SHOTS = ROOT / "reports" / "screenshots"
SHOTS.mkdir(parents=True, exist_ok=True)

from playwright.sync_api import sync_playwright

pages = [
    ("/login", "stage6e1_login.png"),
    ("/data-quality", "stage6e1_data_quality.png"),
    ("/properties", "stage6e1_properties.png"),
    ("/rules", "stage6e1_rules.png"),
    ("/products/partner_st/QA-0", "stage6e1_product_detail.png"),
    ("/products/partner_st/QA-0/reparse", "stage6e1_reparse.png"),
    ("/sterbrust-preview", "stage6e1_sterbrust_preview.png"),
    ("/products", "stage6e1_products_export.png"),
]

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
    page.fill("input[name=username]", "qa_admin")
    page.fill("input[name=password]", "Stage6e1-QA-password")
    page.click("button")
    page.wait_for_timeout(800)
    for path, name in pages:
        page.goto(urljoin(BASE, path))
        page.wait_for_timeout(400)
        page.screenshot(path=str(SHOTS / name), full_page=True)
        print("WROTE", name)
    browser.close()
print("SCREENSHOTS_PASS")
