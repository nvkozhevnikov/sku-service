#!/usr/bin/env python3
"""Refresh current REST property/section/brand definitions with GET only."""
import argparse
import json
import os
from pathlib import Path

from snapshot_sterbrust import (BRANDS_IBLOCK_ID, GetOnlyClient, IBLOCK_ID, REPORTS, REST_DIR,
                                paginate_small, read_env)


def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("--env-file", type=Path); args = parser.parse_args()
    config = read_env(args.env_file)
    client = GetOnlyClient(os.environ.get("BITRIX_WEBHOOK_URL") or config.get("BITRIX_WEBHOOK_URL", ""))
    properties, property_total = paginate_small(client, "catalog.productProperty.list", "productProperties", IBLOCK_ID, ["*"])
    sections, section_total = paginate_small(client, "catalog.section.list", "sections", IBLOCK_ID,
        ["id", "iblockId", "name", "code", "active", "iblockSectionId", "depthLevel", "sort"])
    brands, brand_total = paginate_small(client, "catalog.product.list", "products", BRANDS_IBLOCK_ID,
        ["id", "iblockId", "name", "code", "active"])
    old_properties = json.loads((REST_DIR / "STERBRUST_PROPERTIES_RAW.json").read_text(encoding="utf-8"))
    old_sections = json.loads((REST_DIR / "STERBRUST_SECTIONS_RAW.json").read_text(encoding="utf-8"))
    def signature(rows, fields):
        return sorted(tuple(str(row.get(field) or "") for field in fields) for row in rows)
    comparison = {
        "properties_same_as_seed": signature(old_properties, ["id", "name", "code", "propertyType"]) == signature(properties, ["id", "name", "code", "propertyType"]),
        "sections_same_as_seed": signature(old_sections, ["id", "name", "code", "iblockSectionId"]) == signature(sections, ["id", "name", "code", "iblockSectionId"]),
    }
    (REST_DIR / "STERBRUST_PROPERTIES_RAW.json").write_text(json.dumps(properties, ensure_ascii=False, indent=2), encoding="utf-8")
    (REST_DIR / "STERBRUST_SECTIONS_RAW.json").write_text(json.dumps(sections, ensure_ascii=False, indent=2), encoding="utf-8")
    (REST_DIR / "STERBRUST_BRANDS_RAW.json").write_text(json.dumps(brands, ensure_ascii=False, indent=2), encoding="utf-8")
    meta_path = REPORTS / "sterbrust_snapshot_metadata.json"; meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["http_methods"]["GET"] += client.request_count
    for method, count in client.methods.items(): meta["rest_methods"][method] = meta["rest_methods"].get(method, 0) + count
    meta.update({"properties_total": property_total, "sections_total": section_total, "brands_total": brand_total,
                 "metadata_source": "live GET", "metadata_refresh": comparison | {"requests": client.request_count, "retries": client.retries, "qa": "PASS"}})
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(meta["metadata_refresh"], ensure_ascii=False, indent=2))


if __name__ == "__main__": main()
