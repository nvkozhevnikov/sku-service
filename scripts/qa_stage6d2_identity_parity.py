#!/usr/bin/env python3
"""Independently recompute all packaged Stage 6B decisions with the runtime classifier."""
from __future__ import annotations

import csv
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"
sys.path.insert(0, str(ROOT))

from universal_supplier.identity_service import classify_identity_card, registry_indexes


def csv_rows(name: str) -> list[dict[str, str]]:
    with (REPORTS / name).open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def jsonl_rows(name: str) -> list[dict]:
    with (REPORTS / name).open(encoding="utf-8-sig") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def load_registry() -> dict[str, dict]:
    registry = {}
    with (REPORTS / "STERBRUST_REGISTRY.jsonl").open(encoding="utf-8-sig") as handle:
        for line in handle:
            if line.strip():
                row = json.loads(line); registry[str(row["sterbrust_product_id"])] = row
    return registry


def recompute(supplier: str, cards_name: str, matches_name: str, baseline_name: str,
              registry: dict[str, dict], by_model, by_brand_model) -> tuple[list[dict], list[dict]]:
    cards = jsonl_rows(cards_name)
    matches = {str(row["source_external_id"]): row for row in csv_rows(matches_name)}
    expected = {str(row["supplier_external_id"]): row for row in csv_rows(baseline_name)}
    recomputed, mismatches = [], []
    fields = ("decision", "best_candidate", "auto_accepted", "own_model", "reference_model", "product_kind", "match_method")
    for card in cards:
        external_id = str(card["external_id"]); card["supplier_code"] = supplier
        actual = classify_identity_card(card, matches[external_id], registry, by_model, by_brand_model)
        recomputed.append({"supplier": supplier, "external_id": external_id, **{field: actual.get(field, "") for field in fields}})
        wanted = expected[external_id]
        differences = {field: {"expected": wanted.get(field, ""), "actual": actual.get(field, "")} for field in fields if str(wanted.get(field, "")) != str(actual.get(field, ""))}
        if differences: mismatches.append({"supplier": supplier, "external_id": external_id, "differences": differences})
    if set(matches) != set(expected) or {str(card["external_id"]) for card in cards} != set(expected):
        raise AssertionError(f"source/match/baseline key mismatch for {supplier}")
    return recomputed, mismatches


def main() -> None:
    registry = load_registry(); by_model, by_brand_model = registry_indexes(registry)
    partner, pm = recompute("partner_st", "PARTNER_ST_PARSED_RUN2.jsonl", "PARTNER_ST_FULL_MATCHING_RUN2.csv", "PARTNER_ST_IDENTITY_REGRESSION.csv", registry, by_model, by_brand_model)
    optimum, om = recompute("optimum", "OPTIMUM_PARSED_RUN2.jsonl", "OPTIMUM_MATCHING_RUN2.csv", "OPTIMUM_IDENTITY_AUDIT.csv", registry, by_model, by_brand_model)
    rows = partner + optimum; mismatches = pm + om
    assert len(partner) == 1222 and len(optimum) == 1351 and len(rows) == 2573 and not mismatches
    by_key = {(row["supplier"], row["external_id"]): row for row in rows}
    regressions = {
        "partner_303_lx20_new": by_key[("partner_st","303")]["decision"] == "EXACT_EXISTING" and by_key[("partner_st","303")]["best_candidate"] == "61421",
        "partner_1655_lx20_pro_not_auto": by_key[("partner_st","1655")]["auto_accepted"] == "FALSE",
        "partner_1656_pp13d_new_not_auto": by_key[("partner_st","1656")]["auto_accepted"] == "FALSE",
        "partner_1835_controller_not_auto": by_key[("partner_st","1835")]["auto_accepted"] == "FALSE",
        "partner_1837_handle_not_auto": by_key[("partner_st","1837")]["auto_accepted"] == "FALSE",
        "optimum_561_jaws_not_auto": by_key[("optimum","561")]["auto_accepted"] == "FALSE",
        "optimum_782_tu2304v_not_auto": by_key[("optimum","782")]["auto_accepted"] == "FALSE",
        "pp800f": by_key[("partner_st","297")]["decision"] == "EXACT_EXISTING",
        "mrx3_mrx4": by_key[("partner_st","305")]["decision"] == "CONFLICT",
        "fuzzy_auto_zero": not any(row["auto_accepted"] == "TRUE" and "FUZZY" in row.get("match_method", "").upper() for row in rows),
    }
    assert all(regressions.values())
    print(json.dumps({
        "command": "python scripts/qa_stage6d2_identity_parity.py",
        "database_type": "none; packaged snapshots and registry", "database_fresh": None,
        "network_used": False, "shared_runtime_classifier": "classify_identity_card",
        "total_expected": 2573, "total_recomputed": len(rows), "mismatches": len(mismatches),
        "partner_st": len(partner), "optimum": len(optimum), "regressions": regressions, "result": "PASS",
    }, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
