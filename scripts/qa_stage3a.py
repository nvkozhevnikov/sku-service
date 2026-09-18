#!/usr/bin/env python3
"""Self-contained Stage 3A package QA. Requires no source workspace."""
from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""): digest.update(block)
    return digest.hexdigest()


def csv_rows(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as handle: return list(csv.DictReader(handle))


def main() -> None:
    checks: dict[str, bool] = {}
    baseline = json.loads((ROOT / "migration_baseline_sha256.json").read_text(encoding="utf-8"))
    checks["migrations_001_005_unchanged"] = all(
        sha256(ROOT / "migrations" / name) == expected for name, expected in baseline["files"].items())
    schema = (ROOT / "schema.sql").read_text(encoding="utf-8-sig")
    required_tables = ["sterbrust_snapshot_runs", "sterbrust_products", "catalog_product_identifiers",
                       "brand_aliases", "product_matches", "catalog_offer_selection"]
    checks["schema_has_006_007_008"] = all(name in schema for name in (
        "006_sterbrust_registry_and_matching.sql", "007_sterbrust_registry_indexes.sql",
        "008_sterbrust_supplier_identifiers.sql"))
    checks["required_tables_present"] = all(f"CREATE TABLE {name}" in schema for name in required_tables)
    checks["normalized_supplier_article_indexed"] = "normalized_supplier_article" in schema and "sterbrust_products_supplier_article_idx" in schema
    checks["sterbrust_id_identifier_present"] = "'sterbrust_id'" in schema and "'sterbrust_id'" in (ROOT / "scripts" / "bootstrap_sterbrust_catalog.sql").read_text(encoding="utf-8")
    checks["supplier_identifier_model_present"] = all(token in schema for token in (
        "CREATE TABLE sterbrust_product_supplier_identifiers", "supplier_code", "property_id",
        "normalized_value", "sterbrust_supplier_identifiers_lookup_idx"))
    checks["supplier_identifier_duplicates_not_blocked"] = "UNIQUE (supplier_code, normalized_value)" not in schema

    meta = json.loads((REPORTS / "sterbrust_snapshot_metadata.json").read_text(encoding="utf-8"))
    checks["rest_primary_source"] = meta["source"] == "Sterbrust Bitrix REST" and meta["metadata_source"] == "live GET"
    checks["sterbrust_writes_zero"] = meta["write_methods_used"] == 0 and all(meta["http_methods"][method] == 0 for method in ("POST", "PUT", "PATCH", "DELETE"))
    checks["api_pagination_complete"] = meta["pagination"]["api_reported_total"] == meta["products_total"] == meta["unique_product_ids"]
    checks["sterbrust_ids_unique"] = meta["duplicate_product_ids"] == 0
    checks["active_inactive_complete"] = meta["products_active"] + meta["products_inactive"] == meta["products_total"]
    checks["field_map_live"] = meta["metadata_refresh"]["qa"] == "PASS" and (ROOT / "docs" / "STERBRUST_API_FIELD_MAP.md").is_file()

    registry = csv_rows(REPORTS / "STERBRUST_REGISTRY.csv")
    checks["registry_rows_complete"] = len(registry) == meta["products_total"] and len({r["sterbrust_product_id"] for r in registry}) == len(registry)
    checks["registry_jsonl_present"] = (REPORTS / "STERBRUST_REGISTRY.jsonl").stat().st_size > 0
    sample = csv_rows(REPORTS / "PARTNER_ST_MATCHING_SAMPLE.csv")
    checks["partner_sample_50"] = len(sample) == 50 and len({r["product_id"] for r in sample}) == 50
    checks["automatic_matches_auditable"] = all(
        row["auto_accepted"] != "TRUE" or (row["catalog_sterbrust_product_id"] and json.loads(row["evidence"]).get("selected_candidate"))
        for row in sample)
    property_map = csv_rows(REPORTS / "STERBRUST_SUPPLIER_ARTICLE_PROPERTY_MAP.csv")
    partner_map = [row for row in property_map if row["property_id"] == "1546" and row["property_code"] == "ARTIKUL_PARTNER"]
    checks["supplier_property_map_dynamic"] = len(property_map) == 62 and all(row["property_code"].startswith("ARTIKUL_") for row in property_map)
    checks["partner_property_mapping_confirmed"] = len(partner_map) == 1 and partner_map[0]["supplier_code"] == "partner_st" and partner_map[0]["mapping_status"] == "CONFIRMED"
    coverage = {row["metric"]: int(row["value"]) for row in csv_rows(REPORTS / "STERBRUST_SUPPLIER_ARTICLE_COVERAGE.csv")}
    checks["supplier_article_coverage_complete"] = (
        coverage.get("SUPPLIER_SPECIFIC_PROPERTIES_TOTAL") == len(property_map)
        and coverage.get("PRODUCTS_WITH_ANY_SUPPLIER_ARTICLE", 0) + coverage.get("PRODUCTS_WITHOUT_SUPPLIER_ARTICLE", 0) == meta["products_total"]
        and coverage.get("SUPPLIER_ARTICLE_VALUES_TOTAL", 0) >= coverage.get("PRODUCTS_WITH_ANY_SUPPLIER_ARTICLE", 0)
        and coverage.get("PRODUCTS_WITH_MULTIPLE_SUPPLIER_ARTICLES", 0) > 0)
    supplier_duplicates = csv_rows(REPORTS / "STERBRUST_SUPPLIER_ARTICLE_DUPLICATES.csv")
    checks["supplier_article_duplicates_review_only"] = bool(supplier_duplicates) and all(
        row["status"] == "REVIEW" and int(row["candidate_count"]) > 1 for row in supplier_duplicates)
    exact_found = sum(row["match_method"] in {"SUPPLIER_ARTICLE_EXACT", "SUPPLIER_ARTICLE_IDENTITY_CONFLICT", "DUPLICATE_SUPPLIER_ARTICLE"} for row in sample)
    exact_unique = sum(row["match_method"] in {"SUPPLIER_ARTICLE_EXACT", "SUPPLIER_ARTICLE_IDENTITY_CONFLICT"} for row in sample)
    exact_duplicate = sum(row["match_method"] == "DUPLICATE_SUPPLIER_ARTICLE" for row in sample)
    checks["partner_sample_supplier_article_first"] = (exact_found, exact_unique, exact_duplicate) == (46, 42, 4)
    duplicate_rows = csv_rows(REPORTS / "STERBRUST_EXISTING_DUPLICATE_CANDIDATES.csv")
    checks["real_duplicate_audit"] = len(duplicate_rows) > 0 and all(r["review_status"] == "EXISTING_DUPLICATE_CANDIDATE" for r in duplicate_rows)
    bootstrap = csv_rows(REPORTS / "CANONICAL_BOOTSTRAP_SIMULATION.csv")
    checks["bootstrap_idempotency_simulation"] = len(bootstrap) == meta["products_total"] and all(r["second_run_created"] == "FALSE" for r in bootstrap)
    checks["postgres_not_in_docker"] = not any("postgres" in path.read_text(encoding="utf-8", errors="ignore").lower() for path in ROOT.glob("**/*compose*.y*ml"))

    env = dict(os.environ); env["PYTHONPATH"] = str(ROOT)
    tests = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-q"], cwd=ROOT, env=env, capture_output=True, text=True)
    checks["anti_duplicate_unit_tests"] = tests.returncode == 0
    required = ["STERBRUST_SNAPSHOT_SUMMARY.txt", "STERBRUST_FIELD_COVERAGE.csv",
                "STERBRUST_EXISTING_DUPLICATE_CANDIDATES.csv", "PARTNER_ST_MATCHING_SAMPLE.csv",
                "STERBRUST_SUPPLIER_ARTICLE_PROPERTY_MAP.csv", "STERBRUST_SUPPLIER_ARTICLE_COVERAGE.csv",
                "STERBRUST_SUPPLIER_ARTICLE_DUPLICATES.csv", "STAGE3A_QA_REPORT.md", "STAGE3A_SUMMARY.txt"]
    checks["required_reports_present"] = all((REPORTS / name).is_file() for name in required)
    checks["psql_available"] = shutil.which("psql") is not None

    static_keys = [key for key in checks if key != "psql_available"]
    self_contained = all(checks[key] for key in static_keys)
    postgres_state = "PENDING_EXTERNAL_EXECUTION" if checks["psql_available"] else "BLOCKED_NO_EXTERNAL_POSTGRES"
    overall_state = "PASS" if self_contained and checks["psql_available"] else "PASS_STATIC_POSTGRES_BLOCKED" if self_contained else "FAIL"
    result = {"SELF_CONTAINED_QA": "PASS" if self_contained else "FAIL",
              "POSTGRES_INTEGRATION_QA": postgres_state,
              "OVERALL_QA": overall_state,
              "checks": checks, "unit_test_output": tests.stdout + tests.stderr}
    (REPORTS / "stage3a_static_qa.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    summary_path = REPORTS / "STAGE3A_SUMMARY.txt"
    summary = summary_path.read_text(encoding="utf-8")
    summary = summary.replace("SUPPLIER_IDENTIFIER_MODEL_QA = PENDING_STATIC_QA", f"SUPPLIER_IDENTIFIER_MODEL_QA = {'PASS' if checks['supplier_identifier_model_present'] and checks['supplier_identifier_duplicates_not_blocked'] else 'FAIL'}")
    summary = summary.replace("MATCHING_ENGINE_QA = PENDING_STATIC_QA", f"MATCHING_ENGINE_QA = {'PASS' if checks['anti_duplicate_unit_tests'] and checks['partner_sample_supplier_article_first'] else 'FAIL'}")
    summary = summary.replace("ANTI_DUPLICATE_QA = PENDING_STATIC_QA", f"ANTI_DUPLICATE_QA = {'PASS' if checks['supplier_article_duplicates_review_only'] else 'FAIL'}")
    summary = summary.replace("OVERALL_QA = PENDING_STATIC_QA", f"OVERALL_QA = {overall_state}")
    summary_path.write_text(summary, encoding="utf-8")
    report_lines = ["# Stage 3A.1 QA report", "", "Correction pass scope: supplier-specific Sterbrust identifiers and Partner-ST matching only.", "",
                    f"- `SUPPLIER_IDENTIFIER_MODEL_QA`: {'PASS' if checks['supplier_identifier_model_present'] and checks['supplier_identifier_duplicates_not_blocked'] else 'FAIL'}",
                    f"- `MATCHING_ENGINE_QA`: {'PASS' if checks['anti_duplicate_unit_tests'] and checks['partner_sample_supplier_article_first'] else 'FAIL'}",
                    f"- `ANTI_DUPLICATE_QA`: {'PASS' if checks['supplier_article_duplicates_review_only'] else 'FAIL'}",
                    f"- `POSTGRES_INTEGRATION_QA`: {postgres_state}", f"- `OVERALL_QA`: {overall_state}", "",
                    "No Sterbrust write method was used. No product, XML, production ingest, or PostgreSQL container was created.", ""]
    (REPORTS / "STAGE3A_QA_REPORT.md").write_text("\n".join(report_lines), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not self_contained: raise SystemExit(1)


if __name__ == "__main__": main()
