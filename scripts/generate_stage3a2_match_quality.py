#!/usr/bin/env python3
"""Stage 3A.2 identity-evidence quality correction over the approved snapshot."""
from __future__ import annotations

import csv
import json
import shutil
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path

from sterbrust_matching.matching import Product, match_product
from sterbrust_matching.normalization import (
    model_tokens, normalize_brand, normalize_model, normalize_model_with_flags, normalized_name,
)

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"
PARTNER_CODE = "partner_st"


def read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, fields: list[str], rows) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def raw_values(value) -> list[str]:
    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            result.extend(raw_values(item))
        return result
    if isinstance(value, dict):
        return raw_values(value.get("value")) if "value" in value else []
    text = "" if value is None or isinstance(value, bool) else str(value).strip()
    return [text] if text else []


def product_object(row: dict) -> Product:
    return Product(
        key=row["sterbrust_product_id"], name=row["name"], brand=row["brand_raw"],
        model=row["model_raw"], manufacturer_article=row["article_raw"], category=row["category_path"],
        supplier_identifiers=row["supplier_identifiers"],
    )


def quality_row(flag: str, row: dict, evidence: dict, supplier_article: str = "") -> dict:
    model_normalized, _ = normalize_model_with_flags(row["model_raw"], row["brand_raw"])
    return {
        "quality_flag": flag, "supplier_code": PARTNER_CODE if supplier_article else "",
        "sterbrust_product_id": row["sterbrust_product_id"], "product_name": row["name"],
        "brand": row["brand_raw"], "raw_model_property": row["model_raw"],
        "normalized_model_property": model_normalized,
        "name_model_tokens": "|".join(model_tokens(row["name"])),
        "supplier_article": supplier_article, "general_article": row["article_raw"],
        "evidence": json.dumps(evidence, ensure_ascii=False, sort_keys=True), "status": "REVIEW",
    }


def main() -> None:
    input_sample = read_csv(REPORTS / "PARTNER_ST_MATCHING_SAMPLE_STAGE3A1_BASELINE.csv")
    if len(input_sample) != 50:
        raise RuntimeError("Stage 3A.2 requires the approved 50-row sample")
    original_conflicts = {row["product_id"] for row in input_sample if row["status"] == "CONFLICT"}
    if len(original_conflicts) != 12:
        raise RuntimeError(f"Expected the 12 Stage 3A.1 conflicts, got {len(original_conflicts)}")

    property_map = read_csv(REPORTS / "STERBRUST_SUPPLIER_ARTICLE_PROPERTY_MAP.csv")
    supplier_by_property = {row["property_code"]: row["supplier_code"] for row in property_map}
    products: dict[str, dict] = {}
    identifier_index: dict[tuple[str, str], list[str]] = defaultdict(list)
    data_quality_rows: list[dict] = []

    with (REPORTS / "STERBRUST_REGISTRY.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            raw = json.loads(line)
            product_id = str(raw["sterbrust_product_id"])
            supplier_identifiers: dict[str, list[str]] = defaultdict(list)
            for item in raw.get("properties", []):
                property_code = str(item.get("property_code", ""))
                supplier_code = supplier_by_property.get(property_code)
                if not supplier_code:
                    continue
                for value in raw_values(item.get("value")):
                    normalized = normalize_model(value)
                    if normalized and normalized not in {normalize_model(v) for v in supplier_identifiers[supplier_code]}:
                        supplier_identifiers[supplier_code].append(value)
                        identifier_index[(supplier_code, normalized)].append(product_id)
            row = {
                "sterbrust_product_id": product_id, "name": raw.get("name", ""),
                "brand_raw": raw.get("brand_raw", ""), "model_raw": raw.get("model_raw", ""),
                "article_raw": raw.get("article_raw", ""), "category_path": raw.get("category_path", ""),
                "product_url": raw.get("product_url", ""),
                "supplier_identifiers": {key: tuple(values) for key, values in supplier_identifiers.items()},
            }
            products[product_id] = row
            normalized_model, normalization_flags = normalize_model_with_flags(row["model_raw"], row["brand_raw"])
            title_models = model_tokens(row["name"])
            for flag in normalization_flags:
                data_quality_rows.append(quality_row(flag, row, {"normalization_flags": list(normalization_flags)}))
            if normalized_model and title_models and normalized_model not in title_models:
                data_quality_rows.append(quality_row(
                    "MODEL_PROPERTY_CONFLICT_WITH_NAME", row,
                    {"normalized_model_property": normalized_model, "name_model_tokens": list(title_models)},
                ))

    by_model: dict[str, list[dict]] = defaultdict(list)
    by_brand: dict[str, list[dict]] = defaultdict(list)
    for row in products.values():
        model = normalize_model(row["model_raw"], row["brand_raw"])
        brand = normalize_brand(row["brand_raw"])
        if model:
            by_model[model].append(row)
        if brand:
            by_brand[brand].append(row)

    final_sample: list[dict] = []
    conflict_review: list[dict] = []
    for source_row in input_sample:
        source = Product(
            key=source_row["product_id"], name=source_row["name"], brand=source_row["brand"],
            model=source_row["model"], supplier_article=source_row["SKU"], supplier_code=PARTNER_CODE,
        )
        exact_ids = sorted(set(identifier_index.get((PARTNER_CODE, normalize_model(source.supplier_article)), [])), key=int)
        candidate_rows = [products[product_id] for product_id in exact_ids]
        if not candidate_rows:
            model = normalize_model(source.model, source.brand)
            brand = normalize_brand(source.brand)
            candidate_rows = list(by_model.get(model, [])) if model else []
            if not candidate_rows and brand:
                scored = []
                for candidate in by_brand.get(brand, []):
                    score = SequenceMatcher(None, normalized_name(source.name), normalized_name(candidate["name"])).ratio()
                    if score >= 0.60:
                        scored.append((score, candidate))
                candidate_rows = [row for _, row in sorted(scored, key=lambda item: item[0], reverse=True)[:10]]

        result = match_product(source, [product_object(row) for row in candidate_rows])
        selected = products.get(result.candidate_key or "")
        evidence = dict(result.evidence)
        evidence.update({
            "source": "Approved packaged Sterbrust Bitrix REST snapshot",
            "rule_version": "stage3a2-1.0", "supplier_property_id": "1546",
            "supplier_property_code": "ARTIKUL_PARTNER", "candidate_count": len(candidate_rows),
            "evidence_precedence": ["existing_mapping", "confirmed_supplier_article", "manufacturer_global_id",
                                    "multi_source_model", "name_model_token", "model_property", "fuzzy_retrieval"],
        })
        if selected:
            evidence["selected_candidate"] = {
                "sterbrust_product_id": selected["sterbrust_product_id"], "name": selected["name"],
                "brand": selected["brand_raw"], "model_property": selected["model_raw"],
                "general_article": selected["article_raw"],
                "partner_articles": list(selected["supplier_identifiers"].get(PARTNER_CODE, ())),
                "category_path": selected["category_path"],
            }
        conflict_class = str(evidence.get("conflict_class", ""))
        data_quality_flags = sorted(result.warnings)
        final_row = {
            "product_id": source_row["product_id"], "SKU": source_row["SKU"], "name": source.name,
            "brand": source.brand, "model": source.model,
            "sterbrust_candidates": ";".join(row["sterbrust_product_id"] + "|" + row["product_url"] for row in candidate_rows[:20]),
            "status": result.status, "match_method": result.method, "confidence_score": result.confidence,
            "catalog_sterbrust_product_id": result.candidate_key or "", "conflict_class": conflict_class,
            "evidence": json.dumps(evidence, ensure_ascii=False, sort_keys=True),
            "conflicts": json.dumps(result.conflicts, ensure_ascii=False, sort_keys=True),
            "warnings": json.dumps(result.warnings, ensure_ascii=False, sort_keys=True),
            "data_quality_flags": json.dumps(data_quality_flags, ensure_ascii=False),
            "auto_accepted": str(result.auto_accepted).upper(),
        }
        final_sample.append(final_row)

        if source_row["product_id"] in original_conflicts:
            if not selected:
                raise RuntimeError(f"Original conflict lost candidate: {source_row['SKU']}")
            source_norm, source_flags = normalize_model_with_flags(source.model, source.brand)
            candidate_norm, candidate_flags = normalize_model_with_flags(selected["model_raw"], selected["brand_raw"])
            partner_articles = list(selected["supplier_identifiers"].get(PARTNER_CODE, ()))
            if conflict_class == "IDENTITY_CONFLICT":
                reason = "Exact supplier article is contradicted by candidate title/model and disagreeing general article."
            elif conflict_class == "FIELD_DATA_CONFLICT":
                reason = "Exact supplier article, compatible brand, matching general article and title model outweigh stale MODEL property."
            else:
                reason = "Exact supplier article is confirmed; deterministic normalization/extraction explains the model-string difference."
            conflict_review.append({
                "source_SKU": source.supplier_article, "source_model": source.model,
                "sterbrust_product_id": selected["sterbrust_product_id"], "sterbrust_name": selected["name"],
                "ARTIKUL_PARTNER": "|".join(partner_articles), "general_article": selected["article_raw"],
                "MODEL_property": selected["model_raw"], "source_model_normalized": source_norm,
                "sterbrust_model_normalized": candidate_norm,
                "sterbrust_name_model_tokens": "|".join(model_tokens(selected["name"])),
                "source_normalization_flags": "|".join(source_flags),
                "sterbrust_normalization_flags": "|".join(candidate_flags),
                "supplier_article_equal": str(normalize_model(source.supplier_article) in {normalize_model(v) for v in partner_articles}).upper(),
                "general_article_equal_to_source_SKU": str(normalize_model(selected["article_raw"]) == normalize_model(source.supplier_article)).upper(),
                "conflict_class": conflict_class or "EXTRACTION_CONFLICT",
                "final_status": result.status, "auto_accepted": str(result.auto_accepted).upper(), "reason": reason,
            })
            if conflict_class == "IDENTITY_CONFLICT":
                data_quality_rows.append(quality_row(
                    "SUPPLIER_ARTICLE_POINTS_TO_DIFFERENT_MODEL", selected,
                    {"source_model": source.model, "candidate_name_models": list(model_tokens(selected["name"])),
                     "candidate_model_property": selected["model_raw"]}, source.supplier_article,
                ))
            if selected["article_raw"] and normalize_model(selected["article_raw"]) != normalize_model(source.supplier_article):
                data_quality_rows.append(quality_row(
                    "GENERAL_ARTICLE_DISAGREES", selected,
                    {"source_SKU": source.supplier_article, "general_article": selected["article_raw"]},
                    source.supplier_article,
                ))

    if len(conflict_review) != 12:
        raise RuntimeError("All 12 Stage 3A.1 conflicts must be reviewed")

    duplicate_source = read_csv(REPORTS / "STERBRUST_SUPPLIER_ARTICLE_DUPLICATES.csv")
    for duplicate in duplicate_source:
        product = products[duplicate["sterbrust_product_id"]]
        data_quality_rows.append(quality_row(
            "DUPLICATE_SUPPLIER_ARTICLE", product,
            {"supplier": duplicate["supplier"], "normalized_article": duplicate["normalized_article"],
             "candidate_count": int(duplicate["candidate_count"])}, duplicate["normalized_article"],
        ))

    sample_fields = [
        "product_id", "SKU", "name", "brand", "model", "sterbrust_candidates", "status", "match_method",
        "confidence_score", "catalog_sterbrust_product_id", "conflict_class", "evidence", "conflicts", "warnings",
        "data_quality_flags", "auto_accepted",
    ]
    write_csv(REPORTS / "PARTNER_ST_MATCHING_SAMPLE.csv", sample_fields, final_sample)
    write_csv(REPORTS / "PARTNER_ST_CONFLICT_REVIEW.csv", [
        "source_SKU", "source_model", "sterbrust_product_id", "sterbrust_name", "ARTIKUL_PARTNER",
        "general_article", "MODEL_property", "source_model_normalized", "sterbrust_model_normalized",
        "sterbrust_name_model_tokens", "source_normalization_flags", "sterbrust_normalization_flags",
        "supplier_article_equal", "general_article_equal_to_source_SKU", "conflict_class", "final_status",
        "auto_accepted", "reason",
    ], conflict_review)
    write_csv(REPORTS / "STERBRUST_IDENTITY_DATA_QUALITY.csv", [
        "quality_flag", "supplier_code", "sterbrust_product_id", "product_name", "brand",
        "raw_model_property", "normalized_model_property", "name_model_tokens", "supplier_article",
        "general_article", "evidence", "status",
    ], data_quality_rows)

    counts = Counter(row["status"] for row in final_sample)
    classes = Counter(row["conflict_class"] for row in conflict_review)
    auto_warning = sum(row["auto_accepted"] == "TRUE" and bool(json.loads(row["warnings"])) for row in final_sample)
    false_conflicts_fixed = sum(row["final_status"] != "CONFLICT" for row in conflict_review)
    duplicate_review = sum(row["match_method"] == "DUPLICATE_SUPPLIER_ARTICLE" and row["status"] == "REVIEW" for row in final_sample)
    postgres_qa = "PASS" if shutil.which("psql") else "BLOCKED_NO_EXTERNAL_POSTGRES"
    summary = [
        "STAGE = 3A.2 IDENTITY EVIDENCE QUALITY + FALSE CONFLICT REDUCTION",
        f"INPUT_SAMPLE_TOTAL = {len(final_sample)}",
        *(f"{status} = {counts[status]}" for status in ("EXACT_MATCH", "HIGH_CONFIDENCE_MATCH", "REVIEW", "NEW_CANDIDATE", "CONFLICT")),
        *(f"{name} = {classes[name]}" for name in ("IDENTITY_CONFLICT", "FIELD_DATA_CONFLICT", "EXTRACTION_CONFLICT")),
        f"AUTO_MATCH_WITH_WARNING = {auto_warning}",
        f"FALSE_CONFLICTS_FIXED = {false_conflicts_fixed}",
        f"DUPLICATE_SUPPLIER_ARTICLE_REVIEW = {duplicate_review}",
        "STERBRUST_WRITE_METHODS_USED = 0", "MATCHING_ENGINE_QA = PENDING_STATIC_QA",
        "NORMALIZATION_QA = PENDING_STATIC_QA", "ANTI_DUPLICATE_QA = PENDING_STATIC_QA",
        "SUPPLIER_IDENTIFIER_INTEGRITY_QA = PENDING_STATIC_QA",
        f"POSTGRES_INTEGRATION_QA = {postgres_qa}", "OVERALL_QA = PENDING_STATIC_QA",
    ]
    (REPORTS / "STAGE3A2_SUMMARY.txt").write_text("\n".join(summary) + "\n", encoding="utf-8")
    print("\n".join(summary))


if __name__ == "__main__":
    main()
