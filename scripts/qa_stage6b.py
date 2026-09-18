from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"

REQUIRED_REPORTS = (
    "STAGE6B_SUMMARY.txt", "STAGE6B_QA_REPORT.md", "PRODUCT_KIND_TAXONOMY.md",
    "MODEL_ROLE_RULES.md", "CATEGORY_IDENTITY_PROFILES.md", "OPTIMUM_IDENTITY_AUDIT.csv",
    "PARTNER_ST_IDENTITY_REGRESSION.csv", "CURRENT_AUTO_ACCEPT_AUDIT.csv",
    "NEW_AUTO_ACCEPTED_AFTER_STAGE6B.csv", "SAFE_NEW_PRODUCT_CANDIDATES_OPTIMUM.csv",
    "REVIEW_EXISTING_CANDIDATES_OPTIMUM.csv", "ACCESSORY_COMPATIBILITY_OPTIMUM.csv",
    "CONTENT_COMPLETENESS_OPTIMUM.csv", "MEDIA_COMPLETENESS_OPTIMUM.csv",
    "PRODUCT_RELATION_AUDIT_OPTIMUM.csv", "SAME_SUPPLIER_COLLAPSE_AUDIT.csv",
)


def rows(name: str) -> list[dict[str, str]]:
    with (REPORTS / name).open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def check(condition: bool, name: str, details: object = "") -> dict:
    return {"check": name, "result": "PASS" if condition else "FAIL", "details": details}


def main() -> None:
    results = []
    missing = [name for name in REQUIRED_REPORTS if not (REPORTS / name).is_file()]
    results.append(check(not missing, "REQUIRED_REPORTS_PRESENT", missing))

    optimum = rows("OPTIMUM_IDENTITY_AUDIT.csv")
    partner = rows("PARTNER_ST_IDENTITY_REGRESSION.csv")
    current_auto = rows("CURRENT_AUTO_ACCEPT_AUDIT.csv")
    new_auto = rows("NEW_AUTO_ACCEPTED_AFTER_STAGE6B.csv")
    collapse = rows("SAME_SUPPLIER_COLLAPSE_AUDIT.csv")
    results.extend([
        check(len(optimum) == 1351 and len({r["supplier_external_id"] for r in optimum}) == 1351, "OPTIMUM_FULL_IDENTITY_AUDIT", len(optimum)),
        check(len(partner) == 1222 and len({r["supplier_external_id"] for r in partner}) == 1222, "PARTNER_ST_FULL_REGRESSION", len(partner)),
        check(all(r.get("decision_reason") for r in optimum + partner), "ALL_DECISIONS_EXPLAINED"),
        check(len(current_auto) == 117 and all(r.get("audit_result") in {"PASS", "DEMOTED_TO_REVIEW"} for r in current_auto), "AUTO_ACCEPT_AUDIT_COVERAGE", "117/117"),
        check(len(new_auto) == 0, "NEW_AUTO_ACCEPTED_AUDIT_COVERAGE", "0/0; no new auto accepts"),
        check(len(collapse) == 0, "UNEXPLAINED_SAME_SUPPLIER_COLLAPSES", 0),
    ])

    optimum_by_id = {r["supplier_external_id"]: r for r in optimum}
    partner_by_id = {r["supplier_external_id"]: r for r in partner}
    results.extend([
        check(optimum_by_id["561"]["auto_accepted"] == "FALSE" and optimum_by_id["561"]["decision"] == "ACCESSORY_OR_COMPATIBILITY_ITEM", "OPTIMUM_561_JAW_NOT_VISE", optimum_by_id["561"]["decision_reason"]),
        check(optimum_by_id["782"]["auto_accepted"] == "FALSE", "OPTIMUM_782_TU2304V_NOT_TU2304", optimum_by_id["782"]["decision_reason"]),
        check(partner_by_id["297"]["decision"] == "EXACT_EXISTING", "PP800F", partner_by_id["297"]["decision"]),
        check(partner_by_id["305"]["decision"] == "CONFLICT", "MRX3_MRX4", partner_by_id["305"]["decision"]),
        check(partner_by_id["1655"]["auto_accepted"] == "FALSE", "LX20_PRO_NOT_LX20_NEW"),
        check(partner_by_id["1656"]["auto_accepted"] == "FALSE", "PP13D_NEW_NOT_PP13D"),
        check(partner_by_id["1835"]["decision"] == "ACCESSORY_OR_COMPATIBILITY_ITEM", "CONTROLLER_NOT_MANIPULATOR"),
        check(partner_by_id["1837"]["decision"] == "ACCESSORY_OR_COMPATIBILITY_ITEM", "HANDLE_NOT_MANIPULATOR"),
        check(any("LX20 NEW" in r["source_name"].upper() and r["decision"] == "EXACT_EXISTING" for r in partner), "LX20_NEW_CORRECT_MAPPING"),
    ])

    safe = rows("SAFE_NEW_PRODUCT_CANDIDATES_OPTIMUM.csv")
    content = rows("CONTENT_COMPLETENESS_OPTIMUM.csv")
    media = rows("MEDIA_COMPLETENESS_OPTIMUM.csv")
    relations = rows("PRODUCT_RELATION_AUDIT_OPTIMUM.csv")
    partner_relations = rows("PRODUCT_RELATION_AUDIT_PARTNER_ST.csv")
    results.extend([
        check(len(content) == len(safe), "CONTENT_COMPLETENESS_AUDIT", len(content)),
        check(len(media) == len(safe), "MEDIA_COMPLETENESS_AUDIT", len(media)),
        check(len(relations) == 1351 and len(partner_relations) == 1222, "PRODUCT_RELATION_AUDIT", f"optimum={len(relations)};partner_st={len(partner_relations)}"),
    ])

    baseline = json.loads((ROOT / "stage3d_migration_baseline_sha256.json").read_text(encoding="utf-8"))
    migration_results = {}
    for filename, expected in baseline["files"].items():
        actual = hashlib.sha256((ROOT / "migrations" / filename).read_bytes()).hexdigest()
        migration_results[filename] = actual == expected
    results.append(check(all(migration_results.values()), "MIGRATIONS_001_011_IMMUTABLE", migration_results))

    summary = (REPORTS / "STAGE6B_SUMMARY.txt").read_text(encoding="utf-8")
    results.extend([
        check("STERBRUST_WRITES = 0" in summary, "STERBRUST_WRITES", 0),
        check("ESOL_IMPORT_RUNS = 0" in summary, "ESOL_IMPORT_RUNS", 0),
        check("PRODUCTS_CREATED_ON_STERBRUST = 0" in summary, "PRODUCTS_CREATED_ON_STERBRUST", 0),
        check("STAGE6 = BLOCKED_IMPORT_CONTRACT_EVIDENCE" in summary, "STAGE6_BLOCKER_PRESERVED"),
    ])
    passed = all(item["result"] == "PASS" for item in results)
    payload = {"stage6b": "CLOSED" if passed else "NOT_CLOSED", "checks": results}
    (REPORTS / "STAGE6B_QA_RESULTS.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for item in results:
        print(f"{item['check']} = {item['result']}")
    print(f"STAGE6B_QA = {'PASS' if passed else 'FAIL'}")
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()
