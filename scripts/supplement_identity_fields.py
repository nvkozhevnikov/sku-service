#!/usr/bin/env python3
"""One compact GET-only pass for Bitrix identity properties omitted by select=* on the completed live snapshot."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from snapshot_sterbrust import GetOnlyClient, IBLOCK_ID, REPORTS, REST_DIR, extract, flat, read_env, select_params

FIELDS = ["id", "iblockId", "property1272", "property1273"]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()
    config = read_env(args.env_file)
    client = GetOnlyClient(os.environ.get("BITRIX_WEBHOOK_URL") or config.get("BITRIX_WEBHOOK_URL", ""))
    first = client.call("catalog.product.list", select_params(IBLOCK_ID, FIELDS, 0))
    rows = extract(first, "products")
    total = int(first.get("total") or len(rows)); step = int(first.get("next") or 50)

    def fetch(start: int) -> list[dict]:
        return extract(client.call("catalog.product.list", select_params(IBLOCK_ID, FIELDS, start)), "products")

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for page in pool.map(fetch, range(step, total, step)):
            rows.extend(page)
    by_id = {str(row.get("id") or ""): row for row in rows}
    if len(rows) != total or len(by_id) != total or "" in by_id:
        raise RuntimeError(f"Identity pagination failure rows={len(rows)} unique={len(by_id)} total={total}")
    supplement_path = REST_DIR / "STERBRUST_IDENTITY_SUPPLEMENT.jsonl"
    with supplement_path.open("w", encoding="utf-8") as handle:
        for product_id in sorted(by_id, key=int):
            handle.write(json.dumps(by_id[product_id], ensure_ascii=False, separators=(",", ":")) + "\n")

    properties = json.loads((REST_DIR / "STERBRUST_PROPERTIES_RAW.json").read_text(encoding="utf-8"))
    pmap = {str(p.get("id")): p for p in properties}
    registry = REPORTS / "STERBRUST_REGISTRY.jsonl"; registry_tmp = registry.with_suffix(".jsonl.tmp")
    csv_path = REPORTS / "STERBRUST_REGISTRY.csv"; csv_tmp = csv_path.with_suffix(".csv.tmp")
    csv_fields = ["sterbrust_product_id", "name", "article_raw", "supplier_article_raw", "normalized_supplier_article",
                  "brand_raw", "brand_id", "manufacturer_raw", "manufacturer_id", "model_raw", "category_id",
                  "category_name", "category_path", "product_url", "active", "properties_count", "snapshot_hash"]
    supplier_present = article_present = 0
    with registry.open(encoding="utf-8") as source, registry_tmp.open("w", encoding="utf-8") as target, csv_tmp.open("w", encoding="utf-8-sig", newline="") as csv_handle:
        writer = csv.DictWriter(csv_handle, fieldnames=csv_fields, extrasaction="ignore"); writer.writeheader()
        for line in source:
            item = json.loads(line); extra = by_id[item["sterbrust_product_id"]]
            raw = item["raw_data"]
            for pid in ("1272", "1273"):
                key = "property" + pid; value = extra.get(key)
                if value not in (None, "", [], {}):
                    raw[key] = value
                    if not any(str(p.get("property_id")) == pid for p in item["properties"]):
                        meta = pmap.get(pid, {})
                        item["properties"].append({"property_id": pid, "property_code": meta.get("code") or "",
                            "property_name": meta.get("name") or "", "property_type": meta.get("propertyType") or "",
                            "value": value, "value_flat": flat(value), "unit": ""})
            cml2_article = flat(extra.get("property1272")); supplier_article = flat(extra.get("property1273"))
            item["article_raw"] = item.get("article_raw") or cml2_article
            item["supplier_article_raw"] = supplier_article
            item["normalized_supplier_article"] = "".join(ch for ch in supplier_article.casefold().replace("ё", "е") if ch.isalnum())
            item["snapshot_hash"] = hashlib.sha256(json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            supplier_present += bool(supplier_article); article_present += bool(item["article_raw"])
            target.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n")
            writer.writerow({**item, "properties_count": len(item["properties"])})
    registry_tmp.replace(registry); csv_tmp.replace(csv_path)

    raw_path = REST_DIR / "STERBRUST_PRODUCTS_RAW.jsonl"; raw_tmp = raw_path.with_suffix(".jsonl.tmp")
    with raw_path.open(encoding="utf-8") as source, raw_tmp.open("w", encoding="utf-8") as target:
        for line in source:
            item = json.loads(line); extra = by_id[str(item["id"])]
            for key in ("property1272", "property1273"):
                if extra.get(key) not in (None, "", [], {}): item[key] = extra[key]
            target.write(json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
    raw_tmp.replace(raw_path)

    meta_path = REPORTS / "sterbrust_snapshot_metadata.json"; meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["http_methods"]["GET"] += client.request_count
    meta["rest_methods"]["catalog.product.list"] += client.request_count
    meta["identity_supplement"] = {"fields": ["property1272", "property1273"], "api_reported_total": total,
                                   "unique_product_ids": len(by_id), "pages": (total + step - 1) // step,
                                   "requests": client.request_count, "retries": client.retries, "qa": "PASS"}
    meta["coverage"]["supplier_article"] = supplier_present; meta["coverage"]["article"] = article_present
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(meta["identity_supplement"] | {"supplier_article_present": supplier_present, "article_present": article_present}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
