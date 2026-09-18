#!/usr/bin/env python3
"""Run Stage 4 supplier-offer selection in dry-run or confirmed apply mode."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from universal_supplier.offer_selection import OfferSelectionPolicy, PostgresOfferSelectionStore
from universal_supplier.postgres import PostgresConfig


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate persisted supplier offers")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--policy", type=Path, default=ROOT / "config" / "offer_selection.json")
    args = parser.parse_args()
    if args.apply and os.environ.get("STAGE4_DB_CONFIRM") != "YES":
        raise SystemExit("Apply requires STAGE4_DB_CONFIRM=YES")

    import psycopg
    policy = OfferSelectionPolicy.load(args.policy)
    connection = psycopg.connect(**PostgresConfig.from_env().kwargs(), autocommit=True)
    try:
        store = PostgresOfferSelectionStore(connection)
        decisions = store.evaluate_all(policy)
        writes = {"changed": 0, "unchanged": len(decisions)}
        if args.apply:
            writes = store.apply(decisions)
        counts = Counter(item.selection_status for item in decisions)
        print(json.dumps({
            "mode": "apply" if args.apply else "dry-run",
            "policy_version": policy.version,
            "catalog_products_evaluated": len(decisions),
            "status_counts": dict(sorted(counts.items())),
            "database_rows_changed": writes["changed"],
            "database_rows_unchanged": writes["unchanged"],
        }, ensure_ascii=False, indent=2, sort_keys=True))
    finally:
        connection.close()


if __name__ == "__main__":
    main()
