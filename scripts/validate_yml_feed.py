#!/usr/bin/env python3
"""Validate Stage 5 YML structurally and, by default, against PostgreSQL."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from universal_supplier.postgres import PostgresConfig
from universal_supplier.yml_feed import (
    FeedPolicy,
    FeedValidationResult,
    PostgresFeedStore,
    selected_row_violations,
    validate_yml_bytes,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate a Stage 5 YML feed")
    parser.add_argument("feed", type=Path)
    parser.add_argument("--policy", type=Path, default=ROOT / "config" / "yml_feed.json")
    parser.add_argument(
        "--structural-only", action="store_true",
        help="skip current PostgreSQL selected-state comparison",
    )
    args = parser.parse_args()
    policy = FeedPolicy.load(args.policy)
    data = args.feed.read_bytes()
    snapshot = None
    if args.structural_only:
        result = validate_yml_bytes(data, policy=policy)
    else:
        import psycopg
        connection = psycopg.connect(**PostgresConfig.from_env().kwargs(), autocommit=True)
        try:
            store = PostgresFeedStore(connection)
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
                snapshot = store.load_snapshot()
        finally:
            connection.close()
        result = validate_yml_bytes(
            data, expected_rows=snapshot.selected_rows, policy=policy
        )
        db_violations = selected_row_violations(
            snapshot.selected_rows, policy, snapshot.db_selected_count
        )
        if db_violations:
            result = FeedValidationResult(
                False,
                tuple(db_violations) + result.violations,
                {
                    **result.metrics,
                    "db_selected_count": snapshot.db_selected_count,
                    "db_selected_integrity_violations": len(db_violations),
                },
            )
        else:
            result = FeedValidationResult(
                result.valid,
                result.violations,
                {
                    **result.metrics,
                    "db_selected_count": snapshot.db_selected_count,
                    "db_selected_integrity_violations": 0,
                },
            )
    output = result.as_jsonable()
    output["validation_scope"] = "structural_only" if args.structural_only else "structural_and_current_db"
    output["file"] = str(args.feed)
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
    if not result.valid:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
