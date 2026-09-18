#!/usr/bin/env python3
"""Generate Stage 3A.3 persistent-link and typed-property QA artifacts."""
from __future__ import annotations

import csv
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sterbrust_matching.matching import Product, match_product
from sterbrust_matching.property_normalization import compare_property


REPORTS = ROOT / "reports"
AUTO_STATUSES = {"EXACT_MATCH", "HIGH_CONFIDENCE_MATCH"}


def read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def source_from_row(row: dict) -> Product:
    return Product(
        key=row["product_id"], name=row["name"], brand=row["brand"], model=row["model"],
        supplier_article=row["SKU"], supplier_code="partner_st",
    )


def candidate_from_row(row: dict) -> Product:
    evidence = json.loads(row["evidence"])
    selected = evidence["selected_candidate"]
    return Product(
        key=selected["sterbrust_product_id"], name=selected["name"], brand=selected["brand"],
        model=selected["model_property"], manufacturer_article=selected["general_article"],
        category=selected["category_path"],
        supplier_identifiers={"partner_st": tuple(selected["partner_articles"])},
    )


def property_cases() -> list[dict]:
    definitions = [
        ("CASE 1", "voltage", "380 В", "380V", "compatible", "PROPERTY_UNIT_VARIATION"),
        ("CASE 2", "power", "7,5 кВт", "7.5 kW", "compatible", "PROPERTY_UNIT_VARIATION"),
        ("CASE 3", "power", "7.5 kW", "7500 W", "compatible", "PROPERTY_UNIT_VARIATION"),
        ("CASE 4", "length", "2500 mm", "2.5 m", "compatible", "PROPERTY_UNIT_VARIATION"),
        ("CASE 5", "length", "2500 mm", "3200 mm", "incompatible", "PROPERTY_IDENTITY_CONFLICT"),
        ("CASE 6", "weight", "820 kg", "821 kg", "compatible", "PROPERTY_WITHIN_TOLERANCE"),
        ("CASE 7", "weight", "820 kg", "1200 kg", "compatible", "PROPERTY_DATA_QUALITY_WARNING"),
    ]
    rows: list[dict] = []
    for case_id, property_name, source_raw, candidate_raw, expected_compatibility, expected_classification in definitions:
        result = compare_property(source_raw, candidate_raw, property_name)
        actual_compatibility = "compatible" if result.compatible else "incompatible"
        rows.append({
            "case_id": case_id, "property_name": property_name,
            "source_raw_value": source_raw, "candidate_raw_value": candidate_raw,
            "source_numeric_value": result.source.evidence()["numeric_value"],
            "candidate_numeric_value": result.candidate.evidence()["numeric_value"],
            "canonical_unit": result.source.canonical_unit or result.candidate.canonical_unit or "",
            "source_normalized_text": result.source.normalized_text or "",
            "candidate_normalized_text": result.candidate.normalized_text or "",
            "source_normalization_rule": result.source.normalization_rule,
            "candidate_normalization_rule": result.candidate.normalization_rule,
            "comparison_method": result.comparison_method,
            "unit_conversion": str(result.evidence()["unit_conversion"]).upper(),
            "tolerance": result.tolerance, "property_importance": result.property_importance,
            "classification": result.classification,
            "compatible": str(result.compatible).upper(),
            "expected_classification": expected_classification,
            "test_result": "PASS" if actual_compatibility == expected_compatibility and result.classification == expected_classification else "FAIL",
        })
    return rows


def main() -> None:
    sample = read_csv(REPORTS / "PARTNER_ST_MATCHING_SAMPLE.csv")
    if len(sample) != 50:
        raise RuntimeError("Stage 3A.3 requires the approved 50-row Stage 3A.2 sample")
    counts = Counter(row["status"] for row in sample)
    run2_rows: list[dict] = []
    validated = false_conflicts = 0
    for row in sample:
        output = {
            "source_external_id": row["product_id"], "SKU": row["SKU"],
            "RUN1_STATUS": row["status"], "RUN1_METHOD": row["match_method"],
            "RUN1_WARNINGS": row["warnings"], "RUN2_STATUS": "", "RUN2_METHOD": "",
            "RUN2_WARNINGS": "{}", "mapping_changed": "FALSE", "conflict_class": "", "reason": "",
        }
        if row["status"] in AUTO_STATUSES and row["auto_accepted"] == "TRUE":
            result = match_product(source_from_row(row), [], existing_link=candidate_from_row(row))
            output.update({
                "RUN2_STATUS": result.status, "RUN2_METHOD": result.method,
                "RUN2_WARNINGS": json.dumps(result.warnings, ensure_ascii=False, sort_keys=True),
                "mapping_changed": str(result.status not in AUTO_STATUSES).upper(),
                "conflict_class": str(result.evidence.get("conflict_class", "")),
                "reason": "Persisted mapping revalidated with the same unified identity-quality classifier.",
            })
            if result.status in AUTO_STATUSES and result.candidate_key == row["catalog_sterbrust_product_id"]:
                validated += 1
            else:
                false_conflicts += 1
        else:
            output["reason"] = "RUN1 decision was not auto-accepted; no persisted mapping simulated."
        run2_rows.append(output)

    property_rows = property_cases()
    # Deliberate regression fixture: changed title/model/article and loss of the old
    # supplier-specific article must stop an existing mapping.
    changed = Product("source", "Partner MR-X4", "Partner", "MR-X4", "221495", supplier_code="partner_st")
    old = Product("catalog", "Partner MR-X3", "Partner", "MR-X3", manufacturer_article="211301",
                  supplier_identifiers={"partner_st": ("211301",)})
    real_conflict = match_product(changed, [], existing_link=old)
    real_identity_conflicts = int(
        real_conflict.status == "CONFLICT" and real_conflict.evidence.get("conflict_class") == "IDENTITY_CONFLICT"
    )

    write_csv(REPORTS / "PARTNER_ST_MATCHING_RUN2_SIMULATION.csv", [
        "source_external_id", "SKU", "RUN1_STATUS", "RUN1_METHOD", "RUN1_WARNINGS",
        "RUN2_STATUS", "RUN2_METHOD", "RUN2_WARNINGS", "mapping_changed", "conflict_class", "reason",
    ], run2_rows)
    write_csv(REPORTS / "PROPERTY_IDENTITY_NORMALIZATION_TESTS.csv", [
        "case_id", "property_name", "source_raw_value", "candidate_raw_value", "source_numeric_value",
        "candidate_numeric_value", "canonical_unit", "source_normalized_text", "candidate_normalized_text",
        "source_normalization_rule", "candidate_normalization_rule", "comparison_method", "unit_conversion",
        "tolerance", "property_importance", "classification", "compatible", "expected_classification", "test_result",
    ], property_rows)

    property_qa = "PASS" if all(row["test_result"] == "PASS" for row in property_rows) else "FAIL"
    stability_qa = "PASS" if false_conflicts == 0 and validated == sum(counts[s] for s in AUTO_STATUSES) else "FAIL"
    summary = [
        "PROJECT = UNIVERSAL_SUPPLIER", "SUPPLIER = partner_st", "",
        f"RUN1_SAMPLE_TOTAL = {len(sample)}",
        *(f"RUN1_{status} = {counts[status]}" for status in ("EXACT_MATCH", "HIGH_CONFIDENCE_MATCH", "REVIEW", "NEW_CANDIDATE", "CONFLICT")),
        "", f"RUN2_EXISTING_LINK_VALIDATED = {validated}",
        f"RUN2_FALSE_EXISTING_LINK_CONFLICTS = {false_conflicts}",
        f"RUN2_REAL_IDENTITY_CONFLICTS = {real_identity_conflicts}", "",
        f"PERSISTENT_MAPPING_STABILITY_QA = {stability_qa}",
        f"PROPERTY_NORMALIZATION_QA = {property_qa}",
        "MATCHING_ENGINE_QA = PENDING_STATIC_QA", "ANTI_DUPLICATE_QA = PENDING_STATIC_QA",
        "CROSS_SUPPLIER_ISOLATION_QA = PENDING_STATIC_QA",
        "SUPPLIER_IDENTIFIER_INTEGRITY_QA = PENDING_STATIC_QA",
        "POSTGRES_INTEGRATION_QA = BLOCKED_NO_EXTERNAL_POSTGRES", "",
        "STERBRUST_WRITE_METHODS_USED = 0", "", "POSTGRES_EXTERNAL = YES",
        "POSTGRES_IN_DOCKER = NO", "", "OVERALL_QA = PENDING_STATIC_QA",
    ]
    (REPORTS / "STAGE3A3_SUMMARY.txt").write_text("\n".join(summary) + "\n", encoding="utf-8")
    print("\n".join(summary))


if __name__ == "__main__":
    main()
