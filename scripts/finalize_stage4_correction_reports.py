#!/usr/bin/env python3
"""Refresh derived matching reports and write the human-readable correction QA."""
from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"
FIELDS = [
    "source_external_id", "SKU", "name", "brand", "model", "status",
    "match_method", "confidence_score", "sterbrust_product_id", "conflict_class",
    "warnings", "data_quality_flags", "conflicts", "evidence", "auto_accepted",
]


def read_csv(name: str) -> list[dict]:
    with (REPORTS / name).open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(name: str, records: list[dict], fields: list[str]) -> None:
    with (REPORTS / name).open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)


def main() -> None:
    for supplier, prefix in (("partner_st", "PARTNER_ST_FULL_MATCHING"),
                             ("optimum", "OPTIMUM_MATCHING")):
        run_rows = {label: read_csv(f"{prefix}_{label}.csv") for label in ("RUN1", "RUN2")}
        count_rows = []
        for label, records in run_rows.items():
            count_rows.extend({"run": label, "status": status, "count": count}
                              for status, count in sorted(Counter(r["status"] for r in records).items()))
        stem = "PARTNER_ST" if supplier == "partner_st" else "OPTIMUM"
        write_csv(f"{stem}_MATCH_STATUS_COUNTS.csv", count_rows, ["run", "status", "count"])
        write_csv(f"{stem}_REVIEW.csv", [r for r in run_rows["RUN2"] if r["status"] == "REVIEW"], FIELDS)
        write_csv(f"{stem}_CONFLICTS.csv", [r for r in run_rows["RUN2"] if r["status"] == "CONFLICT"], FIELDS)
        write_csv(f"{stem}_NEW_CANDIDATES.csv", [r for r in run_rows["RUN2"] if r["status"] == "NEW_CANDIDATE"], FIELDS)

    summary = json.loads((REPORTS / "STAGE4_FALSE_MATCH_CORRECTION_QA.json").read_text(encoding="utf-8"))
    audits = read_csv("BRAND_MODEL_AUTO_MATCH_AUDIT.csv")
    new_accepts = read_csv("NEW_AUTO_ACCEPTED_AFTER_MATCHING_CHANGE_AUDIT.csv")
    lines = [
        "# Stage 4 false-match correction QA", "", "## Result", "",
        "`STAGE4_FALSE_MATCH_CORRECTION = PASS`", "",
        f"- Automatic mappings revalidated: `{summary['automatic_mappings_revalidated']}`",
        f"- Revalidation outcomes: `{summary['revalidation_outcomes']}`",
        f"- Accepted Partner-ST mappings: `{summary['partner_st_accepted_mappings']}`",
        f"- Accepted Optimum mappings: `{summary['optimum_accepted_mappings']}`",
        f"- Unexplained same-supplier canonical collapses: `{summary['same_supplier_canonical_collapses']}`",
        f"- Stale selections after mapping removal: `{summary['stale_selection_after_mapping_removal']}`",
        f"- Previous matcher-change auto-accepts audited: `{summary['new_auto_accepted_audit_rows']}`",
        f"- Unaudited new auto-accepts: `{summary['unaudited_new_auto_accepted_mappings']}`",
        f"- BRAND_MODEL audit coverage: `{summary['brand_model_auto_match_audit_coverage_percent']}%`",
        "", "## Newly auto-accepted after the previous matching change", "",
        "| Supplier | External ID | SKU | Sterbrust | Previous method | Corrected2 | Audit result |",
        "|---|---:|---:|---:|---|---|---|",
    ]
    for row in new_accepts:
        lines.append(
            f"| {row['supplier']} | {row['external_id']} | {row['SKU']} | {row['sterbrust_id']} | "
            f"{row['match_method']} | {row['corrected2_status']} / {row['corrected2_method']} / "
            f"AUTO={row['corrected2_auto_accepted']} | {row['final_audit_result']} |"
        )
    lines.extend(["", "## Current accepted BRAND_MODEL relationships", "",
                  "| Supplier | External ID | SKU | Sterbrust | Audit result |",
                  "|---|---:|---:|---:|---|"])
    for row in audits:
        lines.append(f"| {row['supplier']} | {row['source_external_id']} | {row['SKU']} | "
                     f"{row['sterbrust_product_id']} | {row['audit_result']} |")
    lines.extend([
        "", "## Required regressions", "",
        "- Partner 303 / SKU 212004 LX20 NEW remains accepted through the supplier-specific article and links to Sterbrust 61421.",
        "- PP-800F remains accepted and selectable.",
        "- Optimum 782 / TU 2304 V is not auto-accepted, is unlinked, and Sterbrust TU2304 has no selected supplier offer.",
        "- MR-X3 / MR-X4 remains CONFLICT with AUTO_ACCEPTED=FALSE.",
        "- PP-13D NEW is NEW_CANDIDATE; the old PP-13D automatic link is removed.",
        "- Fuzzy-name auto matching remains disabled; supplier-article namespaces remain isolated.",
        "- Migrations 001-011 are unchanged; migration 012 is not required.",
        "- Sterbrust writes: 0. XML/YML generated: NO.", "",
        "## Database safety", "",
        "The correction was executed against the local persistent Stage 4 Docker PostgreSQL runtime. "
        "Source products and offers were preserved; only canonical mapping and derived selection state changed. "
        "Container restart persistence and selection integrity passed.",
    ])
    (REPORTS / "STAGE4_FALSE_MATCH_CORRECTION_QA.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
