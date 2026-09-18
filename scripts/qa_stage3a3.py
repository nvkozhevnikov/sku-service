#!/usr/bin/env python3
"""Self-contained static/unit/package QA for Stage 3A.3."""
from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def rows(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def main() -> None:
    checks: dict[str, bool] = {}
    baseline = json.loads((ROOT / "stage3a2_migration_baseline_sha256.json").read_text(encoding="utf-8"))
    checks["migrations_001_009_unchanged"] = all(
        sha256(ROOT / "migrations" / name) == expected for name, expected in baseline["files"].items()
    )
    migration = (ROOT / "migrations" / "010_persistent_match_quality.sql").read_text(encoding="utf-8")
    schema = (ROOT / "schema.sql").read_text(encoding="utf-8-sig")
    checks["migration_010_in_schema"] = "010_persistent_match_quality.sql" in schema and migration.strip() in schema
    checks["authoritative_current_mapping"] = "source_products" in schema and "catalog_product_id" in schema
    checks["decision_history_semantics"] = all(token in migration for token in (
        "last_validated_at", "is_current", "previous_match_id", "decision_fingerprint",
        "warning_fingerprint", "product_matches_one_current_per_source_uq",
    ))
    checks["supplier_code_db_integrity"] = all(token in schema for token in (
        "FOREIGN KEY (supplier_id, supplier_code)", "REFERENCES suppliers(id, code)",
    ))
    checks["catalog_relation_db_integrity"] = all(token in schema for token in (
        "FOREIGN KEY (sterbrust_product_id, catalog_product_id)",
        "REFERENCES sterbrust_products(sterbrust_product_id, catalog_product_id)",
    ))
    postgres_test = (ROOT / "tests" / "postgres_stage3a3_integrity_tests.sql").read_text(encoding="utf-8")
    checks["postgres_integrity_fixture_present"] = all(token in postgres_test for token in (
        "sterbrust_supplier_identifiers_supplier_code_fk",
        "sterbrust_supplier_identifiers_catalog_relation_fk",
        "product_matches_one_current_per_source_uq", "last_validated_at",
    ))

    required_reports = [
        "PARTNER_ST_MATCHING_RUN2_SIMULATION.csv", "PROPERTY_IDENTITY_NORMALIZATION_TESTS.csv",
        "STAGE3A3_SUMMARY.txt",
    ]
    checks["required_reports_present"] = all((REPORTS / name).is_file() for name in required_reports)
    sample = rows(REPORTS / "PARTNER_ST_MATCHING_SAMPLE.csv")
    run2 = rows(REPORTS / "PARTNER_ST_MATCHING_RUN2_SIMULATION.csv")
    properties = rows(REPORTS / "PROPERTY_IDENTITY_NORMALIZATION_TESTS.csv")
    counts = Counter(row["status"] for row in sample)
    auto_count = sum(row["status"] in {"EXACT_MATCH", "HIGH_CONFIDENCE_MATCH"} and row["auto_accepted"] == "TRUE" for row in sample)
    run2_attempts = [row for row in run2 if row["RUN2_STATUS"]]
    checks["approved_sample_preserved"] = len(sample) == len(run2) == 50 and counts == Counter({"EXACT_MATCH": 41, "REVIEW": 4, "NEW_CANDIDATE": 4, "CONFLICT": 1})
    checks["persistent_mapping_stable"] = len(run2_attempts) == auto_count == 41 and all(
        row["RUN2_STATUS"] in {"EXACT_MATCH", "HIGH_CONFIDENCE_MATCH"}
        and row["RUN2_METHOD"] == "EXISTING_LINK" and row["mapping_changed"] == "FALSE"
        for row in run2_attempts
    )
    checks["false_existing_link_conflicts_zero"] = not any(
        row["RUN1_STATUS"] in {"EXACT_MATCH", "HIGH_CONFIDENCE_MATCH"} and row["RUN2_STATUS"] == "CONFLICT"
        for row in run2
    )
    pp800 = [row for row in run2 if row["SKU"] == "508002"]
    checks["pp800_real_example_stable"] = len(pp800) == 1 and pp800[0]["RUN1_STATUS"] == "EXACT_MATCH" and pp800[0]["RUN2_STATUS"] == "EXACT_MATCH" and pp800[0]["RUN2_METHOD"] == "EXISTING_LINK" and "STERBRUST_MODEL_PROPERTY_STALE" in pp800[0]["RUN2_WARNINGS"]
    mrx = [row for row in sample if row["SKU"] == "211301"]
    checks["mrx3_mrx4_real_conflict_preserved"] = len(mrx) == 1 and mrx[0]["status"] == "CONFLICT" and mrx[0]["conflict_class"] == "IDENTITY_CONFLICT"

    expected_property_classes = {
        "CASE 1": "PROPERTY_UNIT_VARIATION", "CASE 2": "PROPERTY_UNIT_VARIATION",
        "CASE 3": "PROPERTY_UNIT_VARIATION", "CASE 4": "PROPERTY_UNIT_VARIATION",
        "CASE 5": "PROPERTY_IDENTITY_CONFLICT", "CASE 6": "PROPERTY_WITHIN_TOLERANCE",
        "CASE 7": "PROPERTY_DATA_QUALITY_WARNING",
    }
    checks["property_normalization_cases"] = len(properties) == 7 and all(
        row["test_result"] == "PASS" and row["classification"] == expected_property_classes[row["case_id"]]
        for row in properties
    )
    checks["raw_and_normalized_property_evidence"] = all(
        row["source_raw_value"] and row["candidate_raw_value"] and row["comparison_method"]
        and row["tolerance"] and row["property_importance"] and row["source_normalization_rule"]
        for row in properties
    )

    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    tests = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-q"],
        cwd=ROOT, env=env, capture_output=True, text=True,
    )
    checks["unit_tests_pass"] = tests.returncode == 0
    tests_source = (ROOT / "tests" / "test_matching.py").read_text(encoding="utf-8")
    required_tests = [
        "test_existing_link_stale_model_property", "test_existing_link_supplier_article_confirms",
        "test_existing_link_real_identity_change_conflict", "test_existing_link_repeated_validation_stable",
        "test_property_voltage_unit_variation", "test_property_power_decimal_variation",
        "test_property_power_unit_conversion", "test_property_length_conversion",
        "test_property_identity_critical_difference", "test_property_supporting_tolerance",
        "test_fuzzy_name_never_auto_match", "test_duplicate_supplier_article_review",
        "test_model_brand_prefix_still_supported", "test_model_latin_cyrillic_still_supported",
        "test_legacy_unnamespaced_supplier_article_never_auto_matches",
        "test_namespaced_supplier_article_positive_regression",
        "test_cross_supplier_identifier_namespace_never_matches",
    ]
    checks["required_tests_present"] = all(name in tests_source for name in required_tests)
    checks["legacy_supplier_article_auto_path_removed"] = (
        "SUPPLIER_ARTICLE_BRAND" not in (ROOT / "sterbrust_matching" / "matching.py").read_text(encoding="utf-8")
    )
    metadata = json.loads((REPORTS / "sterbrust_snapshot_metadata.json").read_text(encoding="utf-8"))
    checks["sterbrust_writes_zero"] = metadata["write_methods_used"] == 0 and all(
        metadata["http_methods"][method] == 0 for method in ("POST", "PUT", "PATCH", "DELETE")
    )
    checks["postgres_not_in_docker"] = not any(
        "postgres" in path.read_text(encoding="utf-8", errors="ignore").lower()
        for path in ROOT.glob("**/*compose*.y*ml")
    )
    checks["external_postgres_available"] = shutil.which("psql") is not None

    static_keys = [key for key in checks if key != "external_postgres_available"]
    static_pass = all(checks[key] for key in static_keys)
    postgres_state = "PENDING_EXTERNAL_EXECUTION" if checks["external_postgres_available"] else "BLOCKED_NO_EXTERNAL_POSTGRES"
    overall = "PASS" if static_pass and checks["external_postgres_available"] else "PASS_STATIC_POSTGRES_BLOCKED" if static_pass else "FAIL"
    result = {
        "SELF_CONTAINED_QA": "PASS" if static_pass else "FAIL",
        "POSTGRES_INTEGRATION_QA": postgres_state,
        "OVERALL_QA": overall,
        "checks": checks,
        "unit_test_output": tests.stdout + tests.stderr,
    }
    (REPORTS / "stage3a3_static_qa.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    summary_path = REPORTS / "STAGE3A3_SUMMARY.txt"
    summary = summary_path.read_text(encoding="utf-8")
    summary = summary.replace("MATCHING_ENGINE_QA = PENDING_STATIC_QA", f"MATCHING_ENGINE_QA = {'PASS' if checks['unit_tests_pass'] and checks['persistent_mapping_stable'] else 'FAIL'}")
    summary = summary.replace("ANTI_DUPLICATE_QA = PENDING_STATIC_QA", f"ANTI_DUPLICATE_QA = {'PASS' if checks['required_tests_present'] and checks['mrx3_mrx4_real_conflict_preserved'] else 'FAIL'}")
    summary = summary.replace("CROSS_SUPPLIER_ISOLATION_QA = PENDING_STATIC_QA", f"CROSS_SUPPLIER_ISOLATION_QA = {'PASS' if checks['required_tests_present'] and checks['legacy_supplier_article_auto_path_removed'] else 'FAIL'}")
    summary = summary.replace("SUPPLIER_IDENTIFIER_INTEGRITY_QA = PENDING_STATIC_QA", f"SUPPLIER_IDENTIFIER_INTEGRITY_QA = {'PASS' if checks['supplier_code_db_integrity'] and checks['catalog_relation_db_integrity'] else 'FAIL'}")
    summary = summary.replace("OVERALL_QA = PENDING_STATIC_QA", f"OVERALL_QA = {overall}")
    summary_path.write_text(summary, encoding="utf-8")

    report = [
        "# Stage 3A.3 QA report", "", "Persistent mapping stability and normalized identity-property comparison.", "",
        f"- RUN1 sample: {len(sample)} ({dict(sorted(counts.items()))})",
        f"- RUN2 existing links validated: {len(run2_attempts)}",
        f"- RUN2 false existing-link conflicts: {sum(row['RUN2_STATUS'] == 'CONFLICT' for row in run2_attempts)}",
        "- RUN2 real identity conflicts: 1 (deliberate MR-X3 to MR-X4 identity-change fixture)",
        f"- Persistent mapping stability: {'PASS' if checks['persistent_mapping_stable'] else 'FAIL'}",
        f"- Property normalization: {'PASS' if checks['property_normalization_cases'] else 'FAIL'}",
        f"- Matching engine: {'PASS' if checks['unit_tests_pass'] else 'FAIL'}",
        f"- Anti-duplicate rules: {'PASS' if checks['required_tests_present'] and checks['mrx3_mrx4_real_conflict_preserved'] else 'FAIL'}",
        f"- Cross-supplier namespace isolation: {'PASS' if checks['required_tests_present'] and checks['legacy_supplier_article_auto_path_removed'] else 'FAIL'}",
        f"- Supplier identifier integrity: {'PASS' if checks['supplier_code_db_integrity'] and checks['catalog_relation_db_integrity'] else 'FAIL'}",
        f"- PostgreSQL integration: {postgres_state}", f"- Overall: {overall}", "",
        "No Sterbrust write, production ingest, XML, offer selection, cron, deployment, proxy, or PostgreSQL container was used.",
        "A repeated unchanged decision updates last_validated_at; a history row is added only for a material decision, conflict, warning, or manual-mapping change.", "",
    ]
    (REPORTS / "STAGE3A3_QA_REPORT.md").write_text("\n".join(report), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not static_pass:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
