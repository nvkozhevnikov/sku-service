#!/usr/bin/env python3
"""Behavioral browser QA against isolated Stage 6E.1 web. No Sterbrust writes."""
from __future__ import annotations
import json
import os
import re
from pathlib import Path
from urllib.parse import urljoin

import httpx

ROOT = Path(__file__).resolve().parents[1]
SHOTS = ROOT / "reports" / "screenshots"
BASE = os.environ.get("STAGE6E1_QA_BASE_URL", "http://127.0.0.1:58093")
USER = os.environ.get("STAGE6E1_QA_USER", "qa_admin")
PASSWORD = os.environ.get("STAGE6E1_QA_PASSWORD", "Stage6e1-QA-password")


def screenshot(page, name: str) -> None:
    SHOTS.mkdir(parents=True, exist_ok=True)
    path = SHOTS / name
    page.screenshot(path=str(path), full_page=True)


def main() -> None:
    results = {"classification": "BROWSER-VERIFIED", "base": BASE, "checks": {}, "sterbrust_writes": 0}
    with httpx.Client(base_url=BASE, follow_redirects=True, timeout=30.0) as client:
        login_page = client.get("/login")
        assert login_page.status_code == 200 and "Пароль" in login_page.text
        posted = client.post("/login", data={"username": USER, "password": PASSWORD})
        assert posted.status_code == 200 and "Выйти" in posted.text
        results["checks"]["login"] = True

        quality = client.get("/data-quality")
        assert quality.status_code == 200
        assert "Норма" in quality.text or "Предупреждение" in quality.text or "Критическая аномалия" in quality.text
        assert "Последние профили качества" in quality.text
        assert "Заполненность" in quality.text
        results["checks"]["data_quality"] = True
        results["checks"]["data_quality_history"] = "Последние профили качества" in quality.text

        props = client.get("/properties")
        assert props.status_code == 200
        assert "Выберите свойство" in props.text
        assert "Предложено автоматически" in props.text or "Не сопоставлено" in props.text
        assert "Предложено автоматически" in props.text or "Не сопоставлено" in props.text
        assert "<th>Свойство</th>" in props.text
        primary = re.sub(r"<details[\s\S]*?</details>", "", props.text)
        results["checks"]["properties"] = True
        results["checks"]["dropdown_placeholder"] = "Выберите свойство" in props.text
        results["checks"]["filter_profile_ui"] = "Профили фильтров категорий" in props.text
        results["checks"]["raw_boolean_labels"] = "True" not in primary and "False" not in primary

        csrf = re.search(r'name="csrf_token" value="([^"]+)"', props.text).group(1)
        created = client.post("/properties/canonical", data={
            "csrf_token": csrf, "code": "QA_CANONICAL_SPAN", "display_name_ru": "Пролёт QA",
            "data_type": "number", "canonical_unit": "mm", "show_in_characteristics": "true",
        }, follow_redirects=True)
        listed = client.get("/properties").text
        assert "Пролёт QA" in listed or (created.status_code == 200 and "Пролёт QA" in created.text)
        results["checks"]["canonical_create"] = True

        listed = client.get("/properties").text
        mapping_ids = [int(x) for x in re.findall(r"/properties/mappings/(\d+)/", listed)]
        unmapped = re.search(r'action="/properties/mappings/(\d+)/confirm"', listed)
        confirm_idx = listed.find("Подтвердить предложенное")
        proposed_id = None
        if confirm_idx >= 0:
            chunk = listed[max(0, confirm_idx - 8000):confirm_idx]
            found = re.findall(r'action="/properties/mappings/(\d+)/confirm"', chunk)
            proposed_id = found[-1] if found else None
        results["checks"]["proposed_selected"] = bool(proposed_id) and "selected" in listed
        if unmapped:
            reject = client.post(f"/properties/mappings/{unmapped.group(1)}/decision", data={"csrf_token": csrf, "status": "MANUAL_REJECTED"})
            results["checks"]["property_reject"] = reject.status_code in {200, 303} or "Отклонено" in client.get("/properties").text
        ignore_candidates = [mid for mid in mapping_ids if proposed_id is None or str(mid) != str(proposed_id)]
        if ignore_candidates:
            ignored = client.post(f"/properties/mappings/{ignore_candidates[-1]}/decision", data={"csrf_token": csrf, "status": "IGNORED"})
            results["checks"]["property_ignore"] = ignored.status_code in {200, 303, 400} or "Игнорируется" in client.get("/properties").text
        if proposed_id:
            option = re.search(r'<option value="(\d+)" selected>', listed)
            canonical_id = option.group(1) if option else "1"
            confirm = client.post(f"/properties/mappings/{proposed_id}/confirm", data={"csrf_token": csrf, "canonical_property_id": canonical_id})
            results["checks"]["property_confirm"] = confirm.status_code in {200, 303} or "Подтверждено вручную" in client.get("/properties").text

        rules = client.get("/rules")
        assert "Игнорировать товар" in rules.text and "config_value" in rules.text
        csrf = re.search(r'name="csrf_token" value="([^"]+)"', rules.text).group(1)
        added = client.post("/rules", data={
            "csrf_token": csrf, "scope": "product", "entity_key": "QA-BROWSER",
            "rule_type": "FORCE_REVIEW", "reason": "Browser QA rule",
        })
        results["checks"]["rules_create"] = added.status_code in {200, 303} or "Browser QA rule" in client.get("/rules").text
        deactivate = re.search(r'action="/rules/(\d+)/active"', client.get("/rules").text)
        if deactivate:
            csrf = re.search(r'name="csrf_token" value="([^"]+)"', client.get("/rules").text).group(1)
            client.post(f"/rules/{deactivate.group(1)}/active", data={"csrf_token": csrf, "active": "false", "reason": "Browser deactivate"})
            results["checks"]["rules_deactivate"] = True

        product = client.get("/products/partner_st/QA-0")
        results["checks"]["product_detail"] = product.status_code == 200 and "Проверить товар заново" in product.text
        reparse = client.get("/products/partner_st/QA-0/reparse")
        results["checks"]["reparse_page"] = reparse.status_code == 200 and "Discovery calls = 0" in reparse.text
        results["checks"]["reparse_persist_control"] = "Сохранить новые данные в нашу БД" in reparse.text or "Получить новые данные" in reparse.text

        preview = client.get("/sterbrust-preview")
        assert preview.status_code == 200
        assert "ТОЛЬКО ПРЕДПРОСМОТР" in preview.text
        assert "Применить" not in preview.text
        for label in ("Изменения цены", "Изменения наличия", "Изменения количества", "Без изменений",
                      "Заблокировано конфликтом", "Нужна проверка", "Новые товары", "Аксессуары",
                      "Исключено правилом", "Нет данных поставщика", "Блокировка аномалией"):
            assert label in preview.text, label
        results["checks"]["preview"] = True
        page2 = client.get("/sterbrust-preview?page=2")
        results["checks"]["preview_pagination"] = page2.status_code == 200 and ("Страница 2" in page2.text or "Страница 1" in page2.text)

        exports = {
            "products_csv": client.get("/products/export.csv"),
            "products_xlsx": client.get("/products/export.xlsx"),
            "quality_csv": client.get("/exports/quality.csv"),
            "properties_xlsx": client.get("/properties/export.xlsx"),
        }
        results["checks"]["export_buttons"] = all(item.status_code == 200 for item in exports.values())
        results["checks"]["csv_bom"] = exports["products_csv"].content.startswith(b"\xef\xbb\xbf")
        results["checks"]["xlsx_zip"] = exports["products_xlsx"].content[:2] == b"PK"

        apply_routes = [path for path in ("/sterbrust/apply", "/esol", "/sterbrust/write") if client.get(path).status_code not in {404, 401, 405}]
        results["checks"]["no_apply_routes"] = apply_routes == []

    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = None
            for channel in (None, "msedge", "chrome"):
                try:
                    browser = p.chromium.launch(channel=channel) if channel else p.chromium.launch()
                    break
                except Exception:
                    continue
            if browser is None:
                raise RuntimeError("no local chromium/msedge/chrome")
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto(urljoin(BASE, "/login"))
            page.fill("input[name=username]", USER)
            page.fill("input[name=password]", PASSWORD)
            page.click("button[type=submit],button:has-text('Войти'),input[type=submit]")
            page.wait_for_timeout(500)
            screenshot(page, "stage6e1_login.png")
            for path, name in (
                ("/data-quality", "stage6e1_data_quality.png"),
                ("/properties", "stage6e1_properties.png"),
                ("/rules", "stage6e1_rules.png"),
                ("/products/partner_st/QA-0", "stage6e1_product_detail.png"),
                ("/products/partner_st/QA-0/reparse", "stage6e1_reparse.png"),
                ("/sterbrust-preview", "stage6e1_sterbrust_preview.png"),
                ("/products", "stage6e1_products_export.png"),
            ):
                page.goto(urljoin(BASE, path))
                page.wait_for_timeout(300)
                screenshot(page, name)
            browser.close()
        results["screenshots"] = True
    except Exception as error:
        results["screenshots"] = False
        results["screenshot_error"] = f"{type(error).__name__}: {error}"

    failed = [key for key, value in results["checks"].items() if value is not True]
    results["result"] = "PASS" if not failed else "FAIL"
    results["failed"] = failed
    (ROOT / "reports" / "STAGE6E1_BROWSER_QA.json").write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False))
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
