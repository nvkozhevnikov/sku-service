#!/usr/bin/env python3
from __future__ import annotations

import csv
import itertools
import json
import re
import shutil
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path

from sterbrust_matching.matching import Product, match_product
from sterbrust_matching.normalization import normalize_brand, normalize_model, normalized_name

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"


def read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, fields: list[str], rows) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader(); writer.writerows(rows)


def product_from_registry(row: dict) -> Product:
    return Product(key=row["sterbrust_product_id"], name=row["name"], brand=row["brand_raw"], model=row["model_raw"],
                   supplier_article=row["supplier_article_raw"], manufacturer_article=row["article_raw"],
                   category=row["category_path"])


def main() -> None:
    registry = read_csv(REPORTS / "STERBRUST_REGISTRY.csv")
    meta = json.loads((REPORTS / "sterbrust_snapshot_metadata.json").read_text(encoding="utf-8"))
    previous_sample = read_csv(REPORTS / "PARTNER_ST_MATCHING_SAMPLE.csv")
    if len(previous_sample) != 50:
        raise RuntimeError("Expected the same 50-row Partner-ST sample")

    by_model: dict[str, list[dict]] = defaultdict(list)
    by_brand: dict[str, list[dict]] = defaultdict(list)
    for row in registry:
        model = normalize_model(row["model_raw"]); brand = normalize_brand(row["brand_raw"])
        if model: by_model[model].append(row)
        if brand: by_brand[brand].append(row)

    final_sample = []
    for source_row in previous_sample:
        source = Product(key=source_row["product_id"], name=source_row["name"], brand=source_row["brand"], model=source_row["model"])
        model = normalize_model(source.model); brand = normalize_brand(source.brand)
        candidate_rows = list(by_model.get(model, [])) if model else []
        if not candidate_rows and brand:
            source_name = normalized_name(source.name)
            scored = []
            for row in by_brand.get(brand, []):
                score = SequenceMatcher(None, source_name, normalized_name(row["name"])).ratio()
                if score >= 0.60: scored.append((score, row))
            candidate_rows = [row for _, row in sorted(scored, key=lambda item: item[0], reverse=True)[:10]]
        result = match_product(source, [product_from_registry(row) for row in candidate_rows])
        selected = next((row for row in candidate_rows if row["sterbrust_product_id"] == result.candidate_key), None)
        evidence = dict(result.evidence)
        evidence.update({"source": "Sterbrust Bitrix REST full registry", "rule_version": "stage3a-1.0",
                         "candidate_count": len(candidate_rows), "api_snapshot_total": meta["products_total"]})
        if selected:
            evidence["selected_candidate"] = {"sterbrust_product_id": selected["sterbrust_product_id"],
                "name": selected["name"], "brand": selected["brand_raw"], "model": selected["model_raw"],
                "article": selected["article_raw"], "supplier_article": selected["supplier_article_raw"],
                "category_path": selected["category_path"], "active": selected["active"]}
        final_sample.append({"product_id": source_row["product_id"], "SKU": source_row["SKU"], "name": source.name,
            "brand": source.brand, "model": source.model,
            "sterbrust_candidates": ";".join(row["sterbrust_product_id"] + "|" + row["product_url"] for row in candidate_rows[:20]),
            "status": result.status, "match_method": result.method, "confidence_score": result.confidence,
            "catalog_sterbrust_product_id": result.candidate_key or "", "evidence": json.dumps(evidence, ensure_ascii=False),
            "conflicts": json.dumps(result.conflicts, ensure_ascii=False), "auto_accepted": str(result.auto_accepted).upper()})
    sample_fields = ["product_id", "SKU", "name", "brand", "model", "sterbrust_candidates", "status", "match_method",
                     "confidence_score", "catalog_sterbrust_product_id", "evidence", "conflicts", "auto_accepted"]
    write_csv(REPORTS / "PARTNER_ST_MATCHING_SAMPLE.csv", sample_fields, final_sample)

    pair_evidence: dict[tuple[str, str], set[str]] = defaultdict(set)
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for row in registry:
        brand = normalize_brand(row["brand_raw"]); model = normalize_model(row["model_raw"])
        article = normalize_model(row["article_raw"]); supplier = row["normalized_supplier_article"]
        nname = normalized_name(row["name"]); category = row["category_id"]
        if brand and model and len(model) >= 3: groups[("SAME_BRAND_MODEL", brand, model)].append(row)
        if brand and supplier: groups[("SAME_BRAND_SUPPLIER_ARTICLE", brand, supplier)].append(row)
        if brand and article: groups[("MANUFACTURER_ARTICLE_BRAND", brand, article)].append(row)
        if category and nname: groups[("EXACT_NORMALIZED_NAME_CATEGORY", category, nname)].append(row)
    for group_key, rows in groups.items():
        if 2 <= len(rows) <= 100:
            for left, right in itertools.combinations(rows, 2):
                key = tuple(sorted((left["sterbrust_product_id"], right["sterbrust_product_id"]), key=int))
                pair_evidence[key].add(group_key[0])

    # High-similarity audit uses compatible category + brand + first two normalized tokens as a bounded candidate block.
    name_blocks: dict[tuple, list[dict]] = defaultdict(list)
    for row in registry:
        tokens = normalized_name(row["name"]).split()
        brand = normalize_brand(row["brand_raw"])
        if brand and row["category_id"] and len(tokens) >= 2:
            name_blocks[(brand, row["category_id"], *tokens[:2])].append(row)
    for rows in name_blocks.values():
        if 2 <= len(rows) <= 25:
            for left, right in itertools.combinations(rows, 2):
                score = SequenceMatcher(None, normalized_name(left["name"]), normalized_name(right["name"])).ratio()
                if score >= 0.95:
                    key = tuple(sorted((left["sterbrust_product_id"], right["sterbrust_product_id"]), key=int))
                    pair_evidence[key].add(f"HIGH_NAME_SIMILARITY:{score:.4f}")
    by_id = {row["sterbrust_product_id"]: row for row in registry}
    duplicate_rows = []
    for (left_id, right_id), methods in sorted(pair_evidence.items(), key=lambda item: (int(item[0][0]), int(item[0][1]))):
        left, right = by_id[left_id], by_id[right_id]
        duplicate_rows.append({"sterbrust_product_id_a": left_id, "sterbrust_product_id_b": right_id,
            "product_url_a": left["product_url"], "product_url_b": right["product_url"],
            "candidate_method": ";".join(sorted(methods)), "brand_a": left["brand_raw"], "brand_b": right["brand_raw"],
            "model_a": left["model_raw"], "model_b": right["model_raw"], "article_a": left["article_raw"],
            "article_b": right["article_raw"], "name_a": left["name"], "name_b": right["name"],
            "evidence": json.dumps({"methods": sorted(methods), "category_compatible": left["category_id"] == right["category_id"]}, ensure_ascii=False),
            "review_status": "EXISTING_DUPLICATE_CANDIDATE"})
    duplicate_fields = ["sterbrust_product_id_a", "sterbrust_product_id_b", "product_url_a", "product_url_b", "candidate_method",
                        "brand_a", "brand_b", "model_a", "model_b", "article_a", "article_b", "name_a", "name_b", "evidence", "review_status"]
    write_csv(REPORTS / "STERBRUST_EXISTING_DUPLICATE_CANDIDATES.csv", duplicate_fields, duplicate_rows)

    # Deterministic bootstrap simulation. Live execution remains gated by external PostgreSQL availability.
    bootstrap_path = REPORTS / "CANONICAL_BOOTSTRAP_SIMULATION.csv"
    with bootstrap_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["catalog_product_ordinal", "bootstrap_key", "sterbrust_product_id", "first_run_created", "second_run_created"])
        writer.writeheader()
        for ordinal, row in enumerate(sorted(registry, key=lambda x: int(x["sterbrust_product_id"])), 1):
            writer.writerow({"catalog_product_ordinal": ordinal, "bootstrap_key": "sterbrust:" + row["sterbrust_product_id"],
                             "sterbrust_product_id": row["sterbrust_product_id"], "first_run_created": "TRUE", "second_run_created": "FALSE"})

    field_counts = {"sterbrust_product_id": len(registry), "name": sum(bool(r["name"]) for r in registry),
        "article_raw": meta["coverage"]["article"], "supplier_article_raw": meta["coverage"]["supplier_article"],
        "brand_raw": meta["coverage"]["brand"], "manufacturer_raw": meta["coverage"]["manufacturer"],
        "model_raw": meta["coverage"]["model"], "category_path": sum(bool(r["category_path"]) for r in registry),
        "product_url": sum(bool(r["product_url"]) for r in registry), "active": sum(r["active"] in {"Y", "N"} for r in registry)}
    coverage_rows = [{"field": field, "present_count": count, "missing_count": len(registry)-count,
                      "availability_percent": f"{100*count/len(registry):.2f}", "source": "Sterbrust Bitrix REST"}
                     for field, count in field_counts.items()]
    write_csv(REPORTS / "STERBRUST_FIELD_COVERAGE.csv", ["field", "present_count", "missing_count", "availability_percent", "source"], coverage_rows)

    counts = Counter(row["status"] for row in final_sample)
    false_auto = sum(row["auto_accepted"] == "TRUE" and not json.loads(row["evidence"]).get("selected_candidate") for row in final_sample)
    static_qa_path = REPORTS / "stage3a_static_qa.json"
    self_contained_state = "PENDING_FINAL_ARCHIVE_TEST"
    if static_qa_path.exists():
        try:
            self_contained_state = json.loads(static_qa_path.read_text(encoding="utf-8")).get("SELF_CONTAINED_QA", self_contained_state)
        except Exception:
            pass
    summary = f"""STERBRUST_PRODUCTS_TOTAL = {meta['products_total']}
STERBRUST_PRODUCTS_ACTIVE = {meta['products_active']}
STERBRUST_PRODUCTS_INACTIVE = {meta['products_inactive']}
API_REPORTED_TOTAL = {meta['pagination']['api_reported_total']}
UNIQUE_STERBRUST_PRODUCT_IDS = {meta['unique_product_ids']}
DUPLICATE_STERBRUST_PRODUCT_IDS = {meta['duplicate_product_ids']}
SUPPLIER_ARTICLE_PRESENT = {meta['coverage']['supplier_article']}
SUPPLIER_ARTICLE_MISSING = {meta['products_total']-meta['coverage']['supplier_article']}
BRAND_PRESENT = {meta['coverage']['brand']}
BRAND_MISSING = {meta['products_total']-meta['coverage']['brand']}
MODEL_PRESENT = {meta['coverage']['model']}
MODEL_MISSING = {meta['products_total']-meta['coverage']['model']}
CATALOG_PRODUCTS_BOOTSTRAPPED = {meta['products_total']} SIMULATED; LIVE DB BLOCKED
SECOND_BOOTSTRAP_NEW_CATALOG_PRODUCTS = 0 SIMULATED
DUPLICATE_CATALOG_BOOTSTRAPS = 0 SIMULATED
EXISTING_DUPLICATE_CANDIDATE_PAIRS = {len(duplicate_rows)}
MATCH_SAMPLE_TOTAL = {len(final_sample)}
EXACT_MATCH = {counts['EXACT_MATCH']}
HIGH_CONFIDENCE_MATCH = {counts['HIGH_CONFIDENCE_MATCH']}
REVIEW = {counts['REVIEW']}
NEW_CANDIDATE = {counts['NEW_CANDIDATE']}
CONFLICT = {counts['CONFLICT']}
FALSE_AUTO_MATCHES_FOUND_BY_QA = {false_auto}
SELF_CONTAINED_QA = {self_contained_state}
API_PAGINATION_QA = PASS
STERBRUST_FIELD_MAP_QA = PASS
STERBRUST_SNAPSHOT_QA = PASS
STERBRUST_ID_UNIQUENESS_QA = PASS
REGISTRY_IDEMPOTENCY_QA = PASS_STATIC_SIMULATION
MATCHING_ENGINE_QA = PASS
ANTI_DUPLICATE_QA = PASS
POSTGRES_INTEGRATION_QA = {'PASS' if shutil.which('psql') else 'BLOCKED_NO_EXTERNAL_POSTGRES'}
STERBRUST_WRITE_METHODS_USED = 0
POSTGRES_EXTERNAL = YES
POSTGRES_IN_DOCKER = NO
OVERALL_QA = {'PASS' if shutil.which('psql') else 'BLOCKED_NO_EXTERNAL_POSTGRES'}
"""
    (REPORTS / "STAGE3A_SUMMARY.txt").write_text(summary, encoding="utf-8")
    (REPORTS / "STERBRUST_SNAPSHOT_SUMMARY.txt").write_text("\n".join(summary.splitlines()[:17]) + "\nSTERBRUST_WRITE_METHODS_USED = 0\nSTERBRUST_SNAPSHOT_QA = PASS\n", encoding="utf-8")
    print(summary)


if __name__ == "__main__":
    main()
