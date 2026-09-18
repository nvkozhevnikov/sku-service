#!/usr/bin/env python3
"""Stage 3B live-crawl/static QA; never substitutes for PostgreSQL integration."""
from __future__ import annotations

import csv
import hashlib
import json
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


def csv_rows(name: str) -> list[dict]:
    with (REPORTS / name).open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def live_diff_evidence_valid(diff: list[dict], postgres: dict, run1_meta: dict, run2_meta: dict) -> bool:
    """Accept stable runs or exact, persisted live changes; never hide drift."""
    if (len({row["external_id"] for row in diff}) != len(diff)
            or sum(row["run1_present"] == "TRUE" for row in diff) != run1_meta["fetched_count"]
            or sum(row["run2_present"] == "TRUE" for row in diff) != run2_meta["fetched_count"]):
        return False
    allowed_states = {"UNCHANGED", "CHANGED", "NEW_IN_RUN2", "MISSING_IN_RUN2"}
    if any(row["state"] not in allowed_states for row in diff):
        return False
    actual = {
        "new": sorted(row["external_id"] for row in diff if row["state"] == "NEW_IN_RUN2"),
        "missing": sorted(row["external_id"] for row in diff if row["state"] == "MISSING_IN_RUN2"),
        "changed": sorted(row["external_id"] for row in diff if row["state"] == "CHANGED"),
    }
    if not any(actual.values()):
        return all(row["state"] == "UNCHANGED" for row in diff)
    reported = postgres.get("source_diff", {})
    expected_history = sum(
        len([group for group in row.get("changed_groups", "").split("|") if group])
        for row in diff if row["state"] == "CHANGED"
    )
    return (
        postgres.get("state") == "PASS"
        and postgres.get("idempotency_qa") == "PASS_WITH_RECORDED_LIVE_SOURCE_CHANGES"
        and all(sorted(map(str, reported.get(key, []))) == actual[key] for key in actual)
        and postgres.get("change_history_run2_delta") == expected_history
    )


def integration_evidence_valid(postgres: dict) -> tuple[bool, bool]:
    """Return (recorded_pass, valid) without consulting DB credentials."""
    recorded_pass = postgres.get("state") == "PASS"
    post_counts = postgres.get("post_close_db_counts")
    required_count_keys = {"crawl_runs", "crawl_run_items", "source_products", "offers",
                           "entity_change_history", "product_matches"}
    valid_post_counts = (isinstance(post_counts, dict)
                         and required_count_keys.issubset(post_counts)
                         and all(isinstance(post_counts[key], int) and post_counts[key] >= 0
                                 for key in required_count_keys))
    post_assertions = postgres.get("post_close_assertions")
    valid_post_assertions = (isinstance(post_assertions, dict) and bool(post_assertions)
                             and all(isinstance(value, int) and value == 0
                                     for value in post_assertions.values()))
    pass_evidence = all((
        postgres.get("select_1") is True,
        postgres.get("runtime_role_superuser") is False,
        postgres.get("postgres_external") is True,
        postgres.get("postgres_in_docker") is False,
        postgres.get("migrations_001_010") == "PASS",
        postgres.get("migration_011_optional_sterbrust_url") == "PASS",
        postgres.get("integrity_tests") == "PASS",
        postgres.get("connection_autocommit") is True,
        postgres.get("credentials_recorded") is False,
        bool(postgres.get("server_version")),
        bool(postgres.get("integration_timestamp")),
        isinstance(postgres.get("db_counts_after_run2"), dict),
        postgres.get("post_close_verification") == "PASS",
        postgres.get("post_close_select_1") == "PASS",
        valid_post_counts,
        valid_post_assertions,
        postgres.get("post_close_crawl_runs") == 2,
        (isinstance(postgres.get("post_close_current_matches"), int)
         and postgres["post_close_current_matches"] > 0),
        isinstance(postgres.get("post_close_partner_source_products"), int)
        and postgres["post_close_partner_source_products"] > 0,
        isinstance(postgres.get("post_close_partner_default_offers"), int)
        and postgres["post_close_partner_default_offers"] > 0,
        isinstance(postgres.get("post_close_run2_crawl_run_items"), int)
        and postgres["post_close_run2_crawl_run_items"] > 0,
        isinstance(postgres.get("post_close_accepted_catalog_links"), int)
        and postgres["post_close_accepted_catalog_links"] > 0,
        postgres.get("post_close_pp800f_persistent_mapping_stable") == "PASS",
        postgres.get("post_close_pp800f_run1_accepted_match_history") == "PASS",
        postgres.get("post_close_mrx3_mrx4_conflict_preserved") == "PASS",
    ))
    blocked_evidence = (postgres.get("state") == "BLOCKED_NO_EXTERNAL_POSTGRES"
                        and postgres.get("integrity_tests") == "NOT_EXECUTED"
                        and postgres.get("postgres_external") is False
                        and postgres.get("connection_autocommit") is True
                        and postgres.get("post_close_verification") == "NOT_EXECUTED"
                        and postgres.get("credentials_recorded") is False)
    return recorded_pass, (pass_evidence if recorded_pass else blocked_evidence)


def main() -> None:
    checks: dict[str, bool] = {}
    baseline = json.loads((ROOT / "stage3a3_migration_baseline_sha256.json").read_text(encoding="utf-8"))
    checks["migrations_001_010_immutable"] = all(
        sha256(ROOT / "migrations" / name) == expected for name, expected in baseline["files"].items()
    )
    migration_011 = (ROOT / "migrations" / "011_sterbrust_optional_product_url.sql")
    checks["migration_011_optional_sterbrust_url"] = (
        migration_011.is_file()
        and "ALTER COLUMN product_url DROP NOT NULL" in migration_011.read_text(encoding="utf-8")
        and "product_url IS NULL OR product_url ~ '^https?://'" in migration_011.read_text(encoding="utf-8")
    )
    required = [
        "STAGE3B_QA_REPORT.md", "STAGE3B_SUMMARY.txt", "STAGE3B_POSTGRES_INTEGRATION_QA.json",
        "PARTNER_ST_FULL_CRAWL_RUN1.csv", "PARTNER_ST_FULL_CRAWL_RUN2.csv",
        "PARTNER_ST_FULL_MATCHING_RUN1.csv", "PARTNER_ST_FULL_MATCHING_RUN2.csv",
        "PARTNER_ST_RUN1_RUN2_DIFF.csv", "PARTNER_ST_INGEST_COUNTS.csv",
        "PARTNER_ST_MATCH_STATUS_COUNTS.csv", "PARTNER_ST_CONFLICTS.csv", "PARTNER_ST_REVIEW.csv",
        "PARTNER_ST_NEW_CANDIDATES.csv", "POSTGRES_INTEGRITY_RESULTS.txt",
    ]
    checks["required_reports_present"] = all((REPORTS / name).is_file() for name in required)
    run1_meta = json.loads((REPORTS / "partner_st_run1_metadata.json").read_text(encoding="utf-8"))
    run2_meta = json.loads((REPORTS / "partner_st_run2_metadata.json").read_text(encoding="utf-8"))
    checks["live_dynamic_crawls_complete"] = all(
        meta["transport"] == "direct_http" and meta["discovered_count"] == meta["fetched_count"]
        and meta["http_200"] == meta["fetched_count"] and meta["errors"] == 0
        and meta["unique_product_ids"] == meta["fetched_count"]
        and meta["duplicate_product_ids"] == 0 and not meta["anomalous_discovery"]
        for meta in (run1_meta, run2_meta)
    )
    checks["no_playwright_proxy"] = all(not meta["playwright_used"] and not meta["proxy_used"] for meta in (run1_meta, run2_meta))
    run1_crawl, run2_crawl = csv_rows("PARTNER_ST_FULL_CRAWL_RUN1.csv"), csv_rows("PARTNER_ST_FULL_CRAWL_RUN2.csv")
    checks["crawl_csv_identity_unique"] = all(
        len(rows) == len({row["external_id"] for row in rows}) == meta["fetched_count"]
        for rows, meta in ((run1_crawl, run1_meta), (run2_crawl, run2_meta))
    )
    postgres = json.loads((REPORTS / "STAGE3B_POSTGRES_INTEGRATION_QA.json").read_text(encoding="utf-8"))
    diff = csv_rows("PARTNER_ST_RUN1_RUN2_DIFF.csv")
    checks["live_run1_run2_diff_reconciled"] = live_diff_evidence_valid(diff, postgres, run1_meta, run2_meta)
    matching1, matching2 = csv_rows("PARTNER_ST_FULL_MATCHING_RUN1.csv"), csv_rows("PARTNER_ST_FULL_MATCHING_RUN2.csv")
    checks["full_matching_complete"] = len(matching1) == run1_meta["fetched_count"] and len(matching2) == run2_meta["fetched_count"]
    pp1 = next((row for row in matching1 if row["source_external_id"] == "297"), None)
    pp2 = next((row for row in matching2 if row["source_external_id"] == "297"), None)
    mrx = next((row for row in matching1 if row["SKU"] == "211301"), None)
    checks["pp800_stable"] = bool(pp1 and pp2 and pp1["status"] == "EXACT_MATCH" and pp1["match_method"] == "SUPPLIER_ARTICLE_EXACT" and pp2["status"] == "EXACT_MATCH" and pp2["match_method"] == "EXISTING_LINK")
    checks["mrx_conflict_preserved"] = bool(mrx and mrx["status"] == "CONFLICT" and mrx["auto_accepted"] == "FALSE")
    checks["fuzzy_auto_matches_zero"] = not any(row["auto_accepted"] == "TRUE" and row["match_method"] in {"NAME_CANDIDATE_ONLY", "NO_IDENTITY_EVIDENCE"} for row in matching1 + matching2)
    checks["cross_supplier_article_auto_matches_zero"] = not any(row["match_method"] == "SUPPLIER_ARTICLE_BRAND" for row in matching1 + matching2)
    checks["false_existing_link_conflicts_zero"] = not any(
        a["auto_accepted"] == "TRUE" and b["status"] == "CONFLICT"
        and b.get("conflict_class") != "IDENTITY_CONFLICT"
        for a, b in zip(matching1, matching2))
    ingest = {row["metric"]: row for row in csv_rows("PARTNER_ST_INGEST_COUNTS.csv")}
    checks["structured_live_data_extracted"] = all(int(ingest[key]["RUN1"]) > 0 for key in (
        "source_cards", "default_offers", "category_path_nodes", "properties", "images", "documents", "option_groups", "option_values", "navigation_values"
    ))
    import os
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    tests = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-q"],
                           cwd=ROOT, env=env, capture_output=True, text=True)
    checks["unit_tests_pass"] = tests.returncode == 0
    checks["stage3b_tests_present"] = (ROOT / "tests" / "test_stage3b.py").is_file()
    checks["postgres_application_path_present"] = all((ROOT / path).is_file() for path in (
        "universal_supplier/postgres.py", "scripts/run_stage3b_postgres_integration.py",
        "tests/postgres_stage3b_integrity_tests.sql",
    ))
    checks["postgres_not_in_docker"] = not any(
        "postgres" in path.read_text(encoding="utf-8", errors="ignore").lower()
        for path in ROOT.glob("**/*compose*.y*ml")
    )
    checks["sterbrust_writes_zero"] = run1_meta["sterbrust_write_methods_used"] == run2_meta["sterbrust_write_methods_used"] == 0
    checks["xml_not_generated"] = not any(path.suffix.lower() in {".xml", ".yml"} for path in REPORTS.iterdir() if path.is_file())
    forbidden_names = {".env", "id_rsa", "id_ed25519"}
    checks["security_file_names_clean"] = not any(
        path.name.lower() in forbidden_names or path.suffix.lower() in {".pem", ".key", ".dump"}
        for path in ROOT.rglob("*") if path.is_file()
    )
    # Review of a completed package must not require credentials again. The
    # integration runner is the only component that reads DB_*.
    recorded_pass, valid_evidence = integration_evidence_valid(postgres)
    checks["postgres_gate_truthful"] = valid_evidence
    checks["integration_evidence_self_contained"] = valid_evidence
    summary = (REPORTS / "STAGE3B_SUMMARY.txt").read_text(encoding="utf-8")
    checks["stage_not_falsely_closed"] = (("STAGE3B = CLOSED" in summary) if recorded_pass
                                            else ("STAGE3B = INTEGRATION_BLOCKED" in summary))

    static_pass = all(checks.values())
    result = {
        "STAGE3B_STATIC_QA": "PASS" if static_pass else "FAIL",
        "POSTGRES_INTEGRATION_QA": postgres["state"],
        "STAGE3B": "CLOSED" if static_pass and postgres["state"] == "PASS" else "INTEGRATION_BLOCKED",
        "checks": checks, "unit_test_output": tests.stdout + tests.stderr,
        "matching_run1": dict(sorted(Counter(row["status"] for row in matching1).items())),
    }
    (REPORTS / "stage3b_static_qa.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not static_pass:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
