#!/usr/bin/env python3
"""Capture required read-only Stage 6E XML/progress evidence from a preview clone."""
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urljoin

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
SHOTS = ROOT / "reports" / "screenshots"
BASE = os.environ.get("PREVIEW_BASE_URL", "http://127.0.0.1:58097")
USER = os.environ.get("PREVIEW_ADMIN_USER", "qa_stage6e")
PASSWORD = os.environ.get("PREVIEW_ADMIN_PASSWORD")
RUN_ID = os.environ.get("PREVIEW_RUNNING_RUN_ID", "6")
MODE = os.environ.get("QA_SCREENSHOT_MODE", "all")


def main() -> None:
    if not PASSWORD:
        raise SystemExit("PREVIEW_ADMIN_PASSWORD is required")
    SHOTS.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as engine:
        browser = None
        for channel in ("msedge", "chrome"):
            try:
                browser = engine.chromium.launch(channel=channel, headless=True)
                break
            except Exception:
                continue
        if browser is None:
            raise SystemExit("no local Edge/Chrome")
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.goto(urljoin(BASE, "/login"))
        page.fill("input[name=username]", USER)
        page.fill("input[name=password]", PASSWORD)
        page.click("button")
        page.wait_for_url("**/**")

        if MODE != "progress":
            page.goto(urljoin(BASE, "/exports/xml?mode=short&action=preview"))
            page.wait_for_selector("text=Выбрано в XML")
            page.screenshot(path=str(SHOTS / "xml_short_export.png"), full_page=True)

            page.goto(urljoin(BASE, "/exports/xml?mode=full&action=preview"))
            page.wait_for_selector("text=Без Sterbrust ID")
            page.screenshot(path=str(SHOTS / "xml_full_export.png"), full_page=True)

        page.goto(urljoin(BASE, f"/runs/{RUN_ID}"))
        page.wait_for_selector("#crawl-progress")
        page.screenshot(path=str(SHOTS / "crawl_running_progress.png"), full_page=True)
        page.locator("#crawl-progress").screenshot(path=str(SHOTS / "crawl_running_eta.png"))
        browser.close()
    print("XML_PROGRESS_SCREENSHOTS_PASS")


if __name__ == "__main__":
    main()
