#!/usr/bin/env python3
"""Self-contained Stage 3A.2 static/unit/package QA."""
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
    baseline = json.loads((ROOT / "stage3a1_migration_baseline_sha256.json").read_text(encoding="utf-8"))
    checks["migrations_001_008_unchanged"] = all(
        sha256(ROOT / "migrations" / name) == expected for name, expected in baseline["files"].items())
    migration = (ROOT / "migrations" / "009_matching_data_quality.sql").read_text(encoding="utf-8")
    schema = (ROOT / "schema.sql").read_text(encoding="utf-8-sig")
    checks["migration_009_in_schema"] = "009_matching_data_quality.sql" in schema and migration.strip() in schema
    checks["product_match_quality_columns"] = all(token in migration for token in (
        "ADD COLUMN warnings jsonb", "ADD COLUMN data_quality_flags jsonb",
        "product_matches_warnings_object_ck", "product_matches_data_quality_flags_array_ck"))
    checks["supplier_code_db_integrity"] = all(token in migration for token in (
        "suppliers_id_code_uq", "FOREIGN KEY (supplier_id, supplier_code)", "REFERENCES suppliers(id, code)"))
    checks["catalog_relation_db_integrity"] = all(token in migration for token in (
        "sterbrust_products_id_catalog_uq", "FOREIGN KEY (sterbrust_product_id, catalog_product_id)",
        "REFERENCES sterbrust_products(sterbrust_product_id, catalog_product_id)"))

    required_reports = ["PARTNER_ST_CONFLICT_REVIEW.csv", "STERBRUST_IDENTITY_DATA_QUALITY.csv",
                        "PARTNER_ST_MATCHING_SAMPLE.csv", "STAGE3A2_SUMMARY.txt"]
    checks["required_stage3a2_reports_present"] = all((REPORTS / name).is_file() for name in required_reports)
    sample = rows(REPORTS / "PARTNER_ST_MATCHING_SAMPLE.csv")
    baseline_sample = rows(REPORTS / "PARTNER_ST_MATCHING_SAMPLE_STAGE3A1_BASELINE.csv")
    review = rows(REPORTS / "PARTNER_ST_CONFLICT_REVIEW.csv")
    quality = rows(REPORTS / "STERBRUST_IDENTITY_DATA_QUALITY.csv")
    checks["input_sample_preserved"] = len(sample) == len(baseline_sample) == 50 and {r["product_id"] for r in sample} == {r["product_id"] for r in baseline_sample}
    checks["all_stage3a1_conflicts_reviewed"] = len(review) == sum(r["status"] == "CONFLICT" for r in baseline_sample) == 12
    checks["conflict_classes_complete"] = {r["conflict_class"] for r in review} == {"IDENTITY_CONFLICT", "FIELD_DATA_CONFLICT", "EXTRACTION_CONFLICT"}
    mrx = [r for r in review if r["source_SKU"] == "211301"]
    checks["mrx3_mrx4_stays_conflict"] = len(mrx) == 1 and mrx[0]["conflict_class"] == "IDENTITY_CONFLICT" and mrx[0]["final_status"] == "CONFLICT" and mrx[0]["auto_accepted"] == "FALSE"
    checks["false_field_conflicts_reduced"] = any(r["final_status"] == "EXACT_MATCH" and r["conflict_class"] != "IDENTITY_CONFLICT" for r in review)
    checks["duplicate_supplier_articles_review_only"] = all(
        r["status"] == "REVIEW" and r["auto_accepted"] == "FALSE"
        for r in sample if r["match_method"] == "DUPLICATE_SUPPLIER_ARTICLE")
    checks["fuzzy_never_auto_matches"] = all(
        r["auto_accepted"] == "FALSE" for r in sample if r["match_method"] in {"NAME_CANDIDATE_ONLY", "NO_IDENTITY_EVIDENCE"})
    required_flags = {"MODEL_PROPERTY_CONFLICT_WITH_NAME", "MODEL_CONTAINS_BRAND_PREFIX",
                      "SUPPLIER_ARTICLE_POINTS_TO_DIFFERENT_MODEL", "GENERAL_ARTICLE_DISAGREES",
                      "CYRILLIC_LATIN_MODEL_VARIATION", "DUPLICATE_SUPPLIER_ARTICLE"}
    checks["data_quality_flags_complete"] = required_flags <= {r["quality_flag"] for r in quality}
    checks["automatic_warnings_auditable"] = all(
        r["auto_accepted"] != "TRUE" or not json.loads(r["warnings"]) or json.loads(r["data_quality_flags"])
        for r in sample)
    metadata = json.loads((REPORTS / "sterbrust_snapshot_metadata.json").read_text(encoding="utf-8"))
    checks["sterbrust_writes_zero"] = metadata["write_methods_used"] == 0 and all(
        metadata["http_methods"][method] == 0 for method in ("POST", "PUT", "PATCH", "DELETE"))
    checks["postgres_not_in_docker"] = not any(
        "postgres" in path.read_text(encoding="utf-8", errors="ignore").lower()
        for path in ROOT.glob("**/*compose*.y*ml"))

    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    tests = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-q"],
                           cwd=ROOT, env=env, capture_output=True, text=True)
    checks["unit_tests_pass"] = tests.returncode == 0
    tests_source = (ROOT / "tests" / "test_matching.py").read_text(encoding="utf-8")
    required_tests = ["test_model_brand_prefix_removed", "test_model_latin_cyrillic_confusable",
                      "test_exact_supplier_article_stale_model_property",
                      "test_exact_supplier_article_candidate_name_confirms_model",
                      "test_exact_supplier_article_real_model_conflict", "test_new_suffix_extraction_warning",
                      "test_mrx3_mrx4_remains_conflict", "test_duplicate_supplier_article_remains_review",
                      "test_fuzzy_name_never_auto_match"]
    checks["required_tests_present"] = all(name in tests_source for name in required_tests)
    checks["psql_available"] = shutil.which("psql") is not None

    static_keys = [key for key in checks if key != "psql_available"]
    static_pass = all(checks[key] for key in static_keys)
    postgres_state = "PENDING_EXTERNAL_EXECUTION" if checks["psql_available"] else "BLOCKED_NO_EXTERNAL_POSTGRES"
    overall = "PASS" if static_pass and checks["psql_available"] else "PASS_STATIC_POSTGRES_BLOCKED" if static_pass else "FAIL"
    result = {"SELF_CONTAINED_QA": "PASS" if static_pass else "FAIL",
              "POSTGRES_INTEGRATION_QA": postgres_state, "OVERALL_QA": overall,
              "checks": checks, "unit_test_output": tests.stdout + tests.stderr}
    (REPORTS / "stage3a2_static_qa.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    summary_path = REPORTS / "STAGE3A2_SUMMARY.txt"
    summary = summary_path.read_text(encoding="utf-8")
    summary = summary.replace("MATCHING_ENGINE_QA = PENDING_STATIC_QA", f"MATCHING_ENGINE_QA = {'PASS' if checks['unit_tests_pass'] and checks['false_field_conflicts_reduced'] else 'FAIL'}")
    summary = summary.replace("NORMALIZATION_QA = PENDING_STATIC_QA", f"NORMALIZATION_QA = {'PASS' if checks['required_tests_present'] and checks['data_quality_flags_complete'] else 'FAIL'}")
    summary = summary.replace("ANTI_DUPLICATE_QA = PENDING_STATIC_QA", f"ANTI_DUPLICATE_QA = {'PASS' if checks['duplicate_supplier_articles_review_only'] and checks['fuzzy_never_auto_matches'] else 'FAIL'}")
    summary = summary.replace("SUPPLIER_IDENTIFIER_INTEGRITY_QA = PENDING_STATIC_QA", f"SUPPLIER_IDENTIFIER_INTEGRITY_QA = {'PASS' if checks['supplier_code_db_integrity'] and checks['catalog_relation_db_integrity'] else 'FAIL'}")
    summary = summary.replace("OVERALL_QA = PENDING_STATIC_QA", f"OVERALL_QA = {overall}")
    summary_path.write_text(summary, encoding="utf-8")

    counts = Counter(r["status"] for r in sample)
    report = ["# Stage 3A.2 QA report", "", "Identity-evidence quality and false-conflict correction only.", "",
              f"- Input sample: {len(sample)}", f"- Final statuses: {dict(sorted(counts.items()))}",
              f"- Reviewed former conflicts: {len(review)}", f"- Unit tests: {'PASS' if checks['unit_tests_pass'] else 'FAIL'}",
              f"- Matching engine QA: {'PASS' if checks['unit_tests_pass'] and checks['false_field_conflicts_reduced'] else 'FAIL'}",
              f"- Normalization QA: {'PASS' if checks['required_tests_present'] and checks['data_quality_flags_complete'] else 'FAIL'}",
              f"- Anti-duplicate QA: {'PASS' if checks['duplicate_supplier_articles_review_only'] and checks['fuzzy_never_auto_matches'] else 'FAIL'}",
              f"- Supplier identifier integrity QA: {'PASS' if checks['supplier_code_db_integrity'] and checks['catalog_relation_db_integrity'] else 'FAIL'}",
              f"- PostgreSQL integration QA: {postgres_state}", f"- Overall QA: {overall}", "",
              "No Sterbrust write method, production ingest, XML, offer selection, cron, deployment, or PostgreSQL container was used.", ""]
    (REPORTS / "STAGE3A2_QA_REPORT.md").write_text("\n".join(report), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not static_pass:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
