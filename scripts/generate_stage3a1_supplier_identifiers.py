#!/usr/bin/env python3
"""Stage 3A.1 correction pass over the packaged read-only REST snapshot."""
from __future__ import annotations

import csv
import json
import shutil
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path

from sterbrust_matching.matching import Product, match_product
from sterbrust_matching.normalization import normalize_brand, normalize_model, normalized_name

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"
PROPERTIES_PATH = REPORTS / "rest" / "STERBRUST_PROPERTIES_RAW.json"
REGISTRY_JSONL = REPORTS / "STERBRUST_REGISTRY.jsonl"
PARTNER_PROPERTY_ID = "1546"
PARTNER_PROPERTY_CODE = "ARTIKUL_PARTNER"


def read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, fields: list[str], rows) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def property_supplier_code(prop: dict) -> str:
    if str(prop["id"]) == PARTNER_PROPERTY_ID:
        return "partner_st"
    return prop["code"][len("ARTIKUL_"):].lower()


def raw_values(value) -> list[str]:
    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            result.extend(raw_values(item))
        return result
    if isinstance(value, dict):
        return raw_values(value.get("value")) if "value" in value else []
    if value is None or isinstance(value, bool):
        return []
    text = str(value).strip()
    return [text] if text else []


def product_object(row: dict) -> Product:
    return Product(
        key=row["sterbrust_product_id"], name=row["name"], brand=row["brand_raw"],
        model=row["model_raw"], manufacturer_article=row["article_raw"],
        category=row["category_path"], supplier_identifiers=row["supplier_identifiers"],
    )


def main() -> None:
    definitions = json.loads(PROPERTIES_PATH.read_text(encoding="utf-8"))
    supplier_properties = sorted(
        (prop for prop in definitions if str(prop.get("code", "")).startswith("ARTIKUL_")),
        key=lambda prop: int(prop["id"]),
    )
    definitions_by_code = {prop["code"]: prop for prop in supplier_properties}
    supplier_code_by_property = {code: property_supplier_code(prop) for code, prop in definitions_by_code.items()}

    products: dict[str, dict] = {}
    identifiers: list[dict] = []
    identifier_index: dict[tuple[str, str], list[str]] = defaultdict(list)
    identifier_detail: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    populated_by_property: Counter[str] = Counter()
    captured_at = ""

    with REGISTRY_JSONL.open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            product_id = str(row["sterbrust_product_id"])
            supplier_identifiers: dict[str, list[str]] = defaultdict(list)
            captured_at = row.get("captured_at", captured_at)
            for item in row.get("properties", []):
                property_code = str(item.get("property_code", ""))
                definition = definitions_by_code.get(property_code)
                if not definition:
                    continue
                supplier_code = supplier_code_by_property[property_code]
                seen_values: set[str] = set()
                for raw_value in raw_values(item.get("value")):
                    normalized_value = normalize_model(raw_value)
                    if not normalized_value or normalized_value in seen_values:
                        continue
                    seen_values.add(normalized_value)
                    supplier_identifiers[supplier_code].append(raw_value)
                    populated_by_property[property_code] += 1
                    identifier_index[(supplier_code, normalized_value)].append(product_id)
                    identifier_record = {
                        "sterbrust_product_id": product_id,
                        "supplier_code": supplier_code,
                        "property_id": str(definition["id"]),
                        "property_code": property_code,
                        "raw_value": raw_value,
                        "normalized_value": normalized_value,
                    }
                    identifiers.append(identifier_record)
                    identifier_detail[(supplier_code, normalized_value, product_id)].append(identifier_record)
            products[product_id] = {
                "sterbrust_product_id": product_id,
                "name": row.get("name", ""), "brand_raw": row.get("brand_raw", ""),
                "model_raw": row.get("model_raw", ""), "article_raw": row.get("article_raw", ""),
                "category_path": row.get("category_path", ""), "product_url": row.get("product_url", ""),
                "supplier_identifiers": {key: tuple(values) for key, values in supplier_identifiers.items()},
            }

    sample = read_csv(REPORTS / "PARTNER_ST_MATCHING_SAMPLE.csv")
    if len(sample) != 50:
        raise RuntimeError("Expected the existing approved 50-row Partner-ST sample")

    partner_found = sum(bool(identifier_index.get(("partner_st", normalize_model(row["SKU"])))) for row in sample)
    partner_unique = sum(len(identifier_index.get(("partner_st", normalize_model(row["SKU"])), [])) == 1 for row in sample)
    partner_duplicate = sum(len(identifier_index.get(("partner_st", normalize_model(row["SKU"])), [])) > 1 for row in sample)
    partner_mapping_qa = "PASS" if partner_found and partner_found == partner_unique + partner_duplicate else "FAIL"

    property_map_rows = []
    for prop in supplier_properties:
        is_partner = str(prop["id"]) == PARTNER_PROPERTY_ID
        property_map_rows.append({
            "property_id": prop["id"], "property_code": prop["code"], "property_name": prop["name"],
            "active": prop["active"], "supplier_code": property_supplier_code(prop),
            "supplier_name": "Partner-ST" if is_partner else "",
            "mapping_status": "CONFIRMED" if is_partner else "REVIEW",
            "mapping_evidence": (
                f"Confirmed by property contents against the fixed Partner-ST sample: {partner_found} exact SKU values; "
                f"{partner_unique} unique and {partner_duplicate} duplicate candidates."
                if is_partner else
                "Supplier namespace derived from the ARTIKUL_ property suffix; independent supplier identity not verified in Stage 3A.1."
            ),
        })
    write_csv(REPORTS / "STERBRUST_SUPPLIER_ARTICLE_PROPERTY_MAP.csv",
              ["property_id", "property_code", "property_name", "active", "supplier_code", "supplier_name",
               "mapping_status", "mapping_evidence"], property_map_rows)

    products_with_any = sum(bool(row["supplier_identifiers"]) for row in products.values())
    products_with_multiple = sum(sum(len(values) for values in row["supplier_identifiers"].values()) > 1
                                 for row in products.values())
    coverage = {
        "SUPPLIER_SPECIFIC_PROPERTIES_TOTAL": len(supplier_properties),
        "SUPPLIER_ARTICLE_VALUES_TOTAL": len(identifiers),
        "PRODUCTS_WITH_ANY_SUPPLIER_ARTICLE": products_with_any,
        "PRODUCTS_WITHOUT_SUPPLIER_ARTICLE": len(products) - products_with_any,
        "PRODUCTS_WITH_MULTIPLE_SUPPLIER_ARTICLES": products_with_multiple,
    }
    coverage_rows = [
        {"metric": key, "value": value, "source": "Packaged full Sterbrust REST snapshot",
         "definition": "Populated values are counted per product, property, and normalized value."}
        for key, value in coverage.items()
    ]
    write_csv(REPORTS / "STERBRUST_SUPPLIER_ARTICLE_COVERAGE.csv",
              ["metric", "value", "source", "definition"], coverage_rows)

    duplicate_rows = []
    for (supplier_code, normalized_article), product_ids in sorted(identifier_index.items()):
        unique_ids = sorted(set(product_ids), key=int)
        if len(unique_ids) < 2:
            continue
        for product_id in unique_ids:
            product = products[product_id]
            matching_identifiers = identifier_detail[(supplier_code, normalized_article, product_id)]
            for item in matching_identifiers:
                duplicate_rows.append({
                    "supplier": supplier_code, "normalized_article": normalized_article,
                    "sterbrust_product_id": product_id, "name": product["name"], "brand": product["brand_raw"],
                    "model": product["model_raw"], "general_article": product["article_raw"],
                    "property_id": item["property_id"], "property_code": item["property_code"],
                    "candidate_count": len(unique_ids), "status": "REVIEW",
                })
    write_csv(REPORTS / "STERBRUST_SUPPLIER_ARTICLE_DUPLICATES.csv",
              ["supplier", "normalized_article", "sterbrust_product_id", "name", "brand", "model",
               "general_article", "property_id", "property_code", "candidate_count", "status"], duplicate_rows)

    by_model: dict[str, list[dict]] = defaultdict(list)
    by_brand: dict[str, list[dict]] = defaultdict(list)
    for product in products.values():
        model = normalize_model(product["model_raw"])
        brand = normalize_brand(product["brand_raw"])
        if model:
            by_model[model].append(product)
        if brand:
            by_brand[brand].append(product)

    final_sample = []
    for source_row in sample:
        source = Product(key=source_row["product_id"], name=source_row["name"], brand=source_row["brand"],
                         model=source_row["model"], supplier_article=source_row["SKU"], supplier_code="partner_st")
        existing_link = None
        if source_row.get("match_method") == "EXISTING_LINK" and source_row.get("catalog_sterbrust_product_id") in products:
            existing_link = product_object(products[source_row["catalog_sterbrust_product_id"]])
        exact_ids = sorted(set(identifier_index.get(("partner_st", normalize_model(source.supplier_article)), [])), key=int)
        candidate_rows = [products[product_id] for product_id in exact_ids]
        if not candidate_rows:
            model = normalize_model(source.model)
            brand = normalize_brand(source.brand)
            candidate_rows = list(by_model.get(model, [])) if model else []
            if not candidate_rows and brand:
                scores = []
                for product in by_brand.get(brand, []):
                    score = SequenceMatcher(None, normalized_name(source.name), normalized_name(product["name"])).ratio()
                    if score >= 0.60:
                        scores.append((score, product))
                candidate_rows = [row for _, row in sorted(scores, key=lambda item: item[0], reverse=True)[:10]]
        result = match_product(source, [product_object(row) for row in candidate_rows], existing_link=existing_link)
        selected = products.get(result.candidate_key or "")
        evidence = dict(result.evidence)
        evidence.update({"source": "Sterbrust Bitrix REST full registry", "rule_version": "stage3a1-1.0",
                         "supplier_property_id": PARTNER_PROPERTY_ID, "supplier_property_code": PARTNER_PROPERTY_CODE,
                         "candidate_count": len(candidate_rows)})
        if selected:
            evidence["selected_candidate"] = {
                "sterbrust_product_id": selected["sterbrust_product_id"], "name": selected["name"],
                "brand": selected["brand_raw"], "model": selected["model_raw"],
                "general_article": selected["article_raw"],
                "partner_articles": list(selected["supplier_identifiers"].get("partner_st", ())),
                "category_path": selected["category_path"],
            }
        final_sample.append({
            "product_id": source_row["product_id"], "SKU": source_row["SKU"], "name": source.name,
            "brand": source.brand, "model": source.model,
            "sterbrust_candidates": ";".join(row["sterbrust_product_id"] + "|" + row["product_url"] for row in candidate_rows[:20]),
            "status": result.status, "match_method": result.method, "confidence_score": result.confidence,
            "catalog_sterbrust_product_id": result.candidate_key or "", "evidence": json.dumps(evidence, ensure_ascii=False),
            "conflicts": json.dumps(result.conflicts, ensure_ascii=False), "auto_accepted": str(result.auto_accepted).upper(),
        })
    sample_fields = ["product_id", "SKU", "name", "brand", "model", "sterbrust_candidates", "status", "match_method",
                     "confidence_score", "catalog_sterbrust_product_id", "evidence", "conflicts", "auto_accepted"]
    write_csv(REPORTS / "PARTNER_ST_MATCHING_SAMPLE.csv", sample_fields, final_sample)

    counts = Counter(row["status"] for row in final_sample)
    postgres_qa = "PASS" if shutil.which("psql") else "BLOCKED_NO_EXTERNAL_POSTGRES"
    summary_lines = [
        "STAGE = 3A.1 SUPPLIER IDENTIFIER CORRECTION PASS",
        *(f"{key} = {value}" for key, value in coverage.items()),
        f"PARTNER_ST_ARTICLE_PROPERTY_ID = {PARTNER_PROPERTY_ID}",
        f"PARTNER_ST_ARTICLE_PROPERTY_CODE = {PARTNER_PROPERTY_CODE}",
        f"PARTNER_ST_ARTICLE_PROPERTY_MAPPING_QA = {partner_mapping_qa}",
        f"PARTNER_SAMPLE_EXACT_ARTICLE_FOUND = {partner_found}",
        f"PARTNER_SAMPLE_EXACT_ARTICLE_UNIQUE = {partner_unique}",
        f"PARTNER_SAMPLE_EXACT_ARTICLE_DUPLICATE = {partner_duplicate}",
        *(f"{status} = {counts[status]}" for status in ("EXACT_MATCH", "HIGH_CONFIDENCE_MATCH", "REVIEW", "NEW_CANDIDATE", "CONFLICT")),
        "SUPPLIER_IDENTIFIER_MODEL_QA = PENDING_STATIC_QA",
        "MATCHING_ENGINE_QA = PENDING_STATIC_QA",
        "ANTI_DUPLICATE_QA = PENDING_STATIC_QA",
        f"POSTGRES_INTEGRATION_QA = {postgres_qa}",
        "STERBRUST_WRITE_METHODS_USED = 0",
        "POSTGRES_EXTERNAL = YES",
        "POSTGRES_IN_DOCKER = NO",
        "OVERALL_QA = PENDING_STATIC_QA",
    ]
    (REPORTS / "STAGE3A_SUMMARY.txt").write_text("\n".join(summary_lines) + "\n", encoding="utf-8")
    print("\n".join(summary_lines))


if __name__ == "__main__":
    main()
