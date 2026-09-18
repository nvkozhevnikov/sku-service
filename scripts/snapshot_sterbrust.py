#!/usr/bin/env python3
"""Full Sterbrust iblock-48 snapshot using allowlisted HTTP GET requests only."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ALLOWED_METHODS = {"catalog.product.list", "catalog.section.list", "catalog.productProperty.list"}
IBLOCK_ID = "48"
BRANDS_IBLOCK_ID = "45"
PAGE_SIZE = 50
ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"
REST_DIR = REPORTS / "rest"
EMPTY_VALUES = (None, "", [], {})


def read_env(path: Path | None) -> dict[str, str]:
    result: dict[str, str] = {}
    if not path or not path.exists():
        return result
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            result[key.strip()] = value.strip().strip('"').strip("'")
    return result


class GetOnlyClient:
    def __init__(self, webhook: str) -> None:
        if not webhook:
            raise RuntimeError("BITRIX_WEBHOOK_URL is not configured")
        self.__webhook = webhook.rstrip("/") + "/"
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self._lock = threading.Lock()
        self.request_count = 0
        self.methods: dict[str, int] = {}
        self.retries = 0

    def call(self, method: str, params: list[tuple[str, str]] | dict[str, Any]) -> dict[str, Any]:
        if method not in ALLOWED_METHODS or any(x in method.lower() for x in ("add", "update", "delete")):
            raise RuntimeError(f"Blocked non-read-only method: {method}")
        query = urllib.parse.urlencode(params, doseq=True)
        request = urllib.request.Request(self.__webhook + method + "/?" + query, method="GET", headers={"Accept": "application/json"})
        last: Exception | None = None
        for attempt in range(6):
            try:
                with self._lock:
                    self.request_count += 1
                    self.methods[method] = self.methods.get(method, 0) + 1
                raw = self._opener.open(request, timeout=60).read()
                payload = json.loads(raw.decode("utf-8-sig"))
                error = payload.get("error")
                if error in (None, ""):
                    return payload
                if str(error) not in {"QUERY_LIMIT_EXCEEDED", "OPERATION_TIME_LIMIT"}:
                    raise RuntimeError(f"REST {method} failed: {error}; endpoint token hidden")
                last = RuntimeError(str(error))
            except (OSError, TimeoutError, json.JSONDecodeError) as exc:
                last = exc
            if attempt < 5:
                with self._lock:
                    self.retries += 1
                time.sleep(min(0.75 * (2**attempt), 12.0))
        raise RuntimeError(f"REST {method} exhausted retries; token hidden; cause={type(last).__name__}")


def extract(payload: dict[str, Any], result_key: str) -> list[dict[str, Any]]:
    result = payload.get("result", {})
    values = result.get(result_key, []) if isinstance(result, dict) else result
    if not isinstance(values, list):
        raise RuntimeError(f"Unexpected REST response for {result_key}")
    return [x for x in values if isinstance(x, dict)]


def select_params(iblock_id: str, fields: Iterable[str], start: int) -> list[tuple[str, str]]:
    return [("filter[iblockId]", iblock_id), *(("select[]", f) for f in fields), ("start", str(start))]


def paginate_small(client: GetOnlyClient, method: str, key: str, iblock_id: str, fields: list[str]) -> tuple[list[dict], int]:
    first = client.call(method, select_params(iblock_id, fields, 0))
    rows = extract(first, key)
    total = int(first.get("total") or len(rows))
    next_value = first.get("next")
    starts = list(range(int(next_value or PAGE_SIZE), total, int(next_value or PAGE_SIZE))) if total > len(rows) else []
    def fetch(start: int) -> list[dict]:
        return extract(client.call(method, select_params(iblock_id, fields, start)), key)
    with ThreadPoolExecutor(max_workers=4) as pool:
        for page in pool.map(fetch, starts):
            rows.extend(page)
    if len(rows) != total:
        raise RuntimeError(f"Pagination mismatch for {method}: {len(rows)} != {total}")
    return rows, total


def section_paths(sections: list[dict]) -> tuple[dict[str, str], dict[str, str]]:
    by_id = {str(row.get("id")): row for row in sections if row.get("id") not in EMPTY_VALUES}
    paths: dict[str, str] = {}
    codes: dict[str, str] = {}
    for section_id in by_id:
        names, seen, current = [], set(), section_id
        while current and current in by_id and current not in seen:
            seen.add(current); row = by_id[current]
            if row.get("name"): names.append(str(row["name"]))
            current = str(row.get("iblockSectionId") or "")
        paths[section_id] = " / ".join(reversed(names))
        codes[section_id] = str(by_id[section_id].get("code") or "")
    return paths, codes


def flat(value: Any, prefer_enum: bool = True) -> str:
    if value in EMPTY_VALUES:
        return ""
    if isinstance(value, list):
        return " | ".join(x for x in (flat(v, prefer_enum) for v in value) if x)
    if isinstance(value, dict):
        if prefer_enum and value.get("valueEnum") not in EMPTY_VALUES:
            return flat(value.get("valueEnum"), False)
        return flat(value.get("value"), False)
    return str(value)


def property_unit(name: str) -> str:
    match = re.search(r",\s*([^,]{1,24})\s*$", name)
    suffix = match.group(1).strip() if match else ""
    return suffix if suffix and re.fullmatch(r"[%°a-zа-яё0-9³²/·.* -]+", suffix, re.I) else ""


def nonempty_raw(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if value not in EMPTY_VALUES}


def seed_rows(path: Path, result_key: str, pages_key: str = "pages") -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows: list[dict] = []
    for page in payload.get(pages_key, []):
        rows.extend(extract(page, result_key))
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--metadata-seed", type=Path, help="Prior GET-only raw metadata directory; products are always fetched live")
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()
    config = read_env(args.env_file)
    webhook = os.environ.get("BITRIX_WEBHOOK_URL") or config.get("BITRIX_WEBHOOK_URL", "")
    client = GetOnlyClient(webhook)
    REPORTS.mkdir(exist_ok=True); REST_DIR.mkdir(exist_ok=True)
    captured_at = datetime.now(timezone.utc).isoformat()

    if args.metadata_seed:
        properties = seed_rows(args.metadata_seed / "STERBRUST_PROPERTIES_RAW.json", "productProperties")
        sections = seed_rows(args.metadata_seed / "STERBRUST_SECTIONS_RAW.json", "sections")
        brands = seed_rows(args.metadata_seed / "STERBRUST_PROPERTIES_RAW.json", "products", "brand_pages")
        property_total, section_total, brand_total = len(properties), len(sections), len(brands)
        metadata_source = "GET-only metadata seed 2026-08-28; live product response revalidates property keys"
    else:
        properties, property_total = paginate_small(client, "catalog.productProperty.list", "productProperties", IBLOCK_ID, ["*"])
        sections, section_total = paginate_small(client, "catalog.section.list", "sections", IBLOCK_ID,
                                                  ["id", "iblockId", "name", "code", "active", "iblockSectionId", "depthLevel", "sort"])
        brands, brand_total = paginate_small(client, "catalog.product.list", "products", BRANDS_IBLOCK_ID,
                                              ["id", "iblockId", "name", "code", "active"])
        metadata_source = "live GET"
    property_map = {str(p.get("id")): p for p in properties if p.get("id") not in EMPTY_VALUES}
    brand_map = {str(p.get("id")): str(p.get("name") or "") for p in brands if p.get("id") not in EMPTY_VALUES}
    paths, section_codes = section_paths(sections)

    # Bitrix wildcard omits some hidden identity properties, so they are explicit.
    product_fields = ["id", "iblockId", "*", "property1090", "property1272", "property1273", "property1131", "property1140", "property1156"]
    first = client.call("catalog.product.list", select_params(IBLOCK_ID, product_fields, 0))
    first_rows = extract(first, "products")
    api_total = int(first.get("total") or len(first_rows))
    step = int(first.get("next") or PAGE_SIZE)
    if step <= 0:
        raise RuntimeError("Invalid REST pagination step")
    starts = list(range(step, api_total, step))

    raw_path = REST_DIR / "STERBRUST_PRODUCTS_RAW.jsonl"
    registry_path = REPORTS / "STERBRUST_REGISTRY.jsonl"
    csv_path = REPORTS / "STERBRUST_REGISTRY.csv"
    csv_fields = ["sterbrust_product_id", "name", "article_raw", "supplier_article_raw", "normalized_supplier_article",
                  "brand_raw", "brand_id", "manufacturer_raw", "manufacturer_id", "model_raw", "category_id",
                  "category_name", "category_path", "product_url", "active", "properties_count", "snapshot_hash"]
    ids: list[str] = []
    active_count = inactive_count = 0
    coverage = {"supplier_article": 0, "brand": 0, "manufacturer": 0, "model": 0, "article": 0}

    def fetch_start(start: int) -> list[dict]:
        return extract(client.call("catalog.product.list", select_params(IBLOCK_ID, product_fields, start)), "products")

    def normalize_article(value: str) -> str:
        return "".join(re.findall(r"[a-zа-я0-9]+", value.casefold().replace("ё", "е")))

    with raw_path.open("w", encoding="utf-8") as raw_out, registry_path.open("w", encoding="utf-8") as registry_out, csv_path.open("w", encoding="utf-8-sig", newline="") as csv_out:
        writer = csv.DictWriter(csv_out, fieldnames=csv_fields, extrasaction="ignore"); writer.writeheader()

        def consume(rows: list[dict]) -> None:
            nonlocal active_count, inactive_count
            for product in rows:
                cleaned = nonempty_raw(product)
                product_id = str(product.get("id") or "")
                ids.append(product_id)
                active = str(product.get("active") or "").upper()
                active_count += active == "Y"; inactive_count += active == "N"
                section_id = str(product.get("iblockSectionId") or "")
                category_path = paths.get(section_id, "")
                category_name = category_path.split(" / ")[-1] if category_path else ""
                supplier_article = flat(product.get("property1273"))
                article = flat(product.get("property1090")) or flat(product.get("property1272"))
                brand_id = flat(product.get("property1140"), False)
                manufacturer = flat(product.get("property1131"))
                manufacturer_id = flat(product.get("property1131"), False)
                brand = brand_map.get(brand_id, "") or manufacturer
                model = flat(product.get("property1156"))
                coverage["supplier_article"] += bool(supplier_article); coverage["article"] += bool(article)
                coverage["brand"] += bool(brand); coverage["manufacturer"] += bool(manufacturer); coverage["model"] += bool(model)
                props = []
                for key, value in cleaned.items():
                    match = re.fullmatch(r"property(\d+)", key)
                    if not match:
                        continue
                    meta = property_map.get(match.group(1), {})
                    props.append({"property_id": match.group(1), "property_code": meta.get("code") or "",
                                  "property_name": meta.get("name") or "", "property_type": meta.get("propertyType") or "",
                                  "value": value, "value_flat": flat(value), "unit": property_unit(str(meta.get("name") or ""))})
                code = str(product.get("code") or "")
                section_code = section_codes.get(section_id, "")
                url = f"https://sterbrust.com/catalog/{section_code}/{code}/" if section_code and code else ""
                raw_json = json.dumps(cleaned, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                registry = {"sterbrust_product_id": product_id, "name": product.get("name") or "",
                            "article_raw": article, "supplier_article_raw": supplier_article,
                            "normalized_supplier_article": normalize_article(supplier_article), "brand_raw": brand,
                            "brand_id": brand_id, "manufacturer_raw": manufacturer, "manufacturer_id": manufacturer_id,
                            "model_raw": model, "category_id": section_id, "category_name": category_name,
                            "category_path": category_path, "category_ids": product.get("iblockSection") or [],
                            "product_url": url, "active": active, "properties": props, "raw_data": cleaned,
                            "snapshot_hash": hashlib.sha256(raw_json.encode()).hexdigest(), "captured_at": captured_at}
                raw_out.write(raw_json + "\n")
                registry_out.write(json.dumps(registry, ensure_ascii=False, separators=(",", ":")) + "\n")
                writer.writerow({**registry, "properties_count": len(props)})

        consume(first_rows)
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            for page_rows in pool.map(fetch_start, starts):
                consume(page_rows)

    unique_ids = len(set(ids)); duplicates = len(ids) - unique_ids
    if len(ids) != api_total:
        raise RuntimeError(f"Product pagination mismatch: {len(ids)} != {api_total}")
    if duplicates or any(not value for value in ids):
        raise RuntimeError(f"Product identity failure: duplicates={duplicates}; empty={sum(not x for x in ids)}")
    (REST_DIR / "STERBRUST_PROPERTIES_RAW.json").write_text(json.dumps(properties, ensure_ascii=False, indent=2), encoding="utf-8")
    (REST_DIR / "STERBRUST_SECTIONS_RAW.json").write_text(json.dumps(sections, ensure_ascii=False, indent=2), encoding="utf-8")
    (REST_DIR / "STERBRUST_BRANDS_RAW.json").write_text(json.dumps(brands, ensure_ascii=False, indent=2), encoding="utf-8")
    metadata = {"captured_at": captured_at, "source": "Sterbrust Bitrix REST", "iblock_id": 48,
                "http_methods": {"GET": client.request_count, "POST": 0, "PUT": 0, "PATCH": 0, "DELETE": 0},
                "rest_methods": client.methods, "retries": client.retries, "pagination": {"parameter": "start", "page_size": step,
                "api_reported_total": api_total, "pages_expected": (api_total + step - 1) // step},
                "products_total": len(ids), "unique_product_ids": unique_ids, "duplicate_product_ids": duplicates,
                "products_active": active_count, "products_inactive": inactive_count, "properties_total": property_total,
                "sections_total": section_total, "brands_total": brand_total, "metadata_source": metadata_source, "coverage": coverage,
                "write_methods_used": 0, "api_pagination_qa": "PASS", "snapshot_qa": "PASS"}
    (REPORTS / "sterbrust_snapshot_metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(metadata, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
