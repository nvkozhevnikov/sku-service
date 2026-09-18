#!/usr/bin/env python3
"""Read-only PostgreSQL advisory-lock QA; creates no rows and runs no crawler."""
from __future__ import annotations

import json
from pathlib import Path
import sys

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from universal_supplier.control_plane.queue import advisory_lock_key
from universal_supplier.postgres import PostgresConfig


def main() -> None:
    config = PostgresConfig.from_env()
    same_key = advisory_lock_key("same.stage6c1.invalid")
    other_key = advisory_lock_key("other.stage6c1.invalid")
    with (
        psycopg.connect(**config.kwargs(), autocommit=True) as first,
        psycopg.connect(**config.kwargs(), autocommit=True) as second,
        first.cursor() as first_cursor,
        second.cursor() as second_cursor,
    ):
        first_cursor.execute("SELECT pg_try_advisory_lock(%s)", (same_key,))
        first_same = bool(first_cursor.fetchone()[0])
        second_cursor.execute("SELECT pg_try_advisory_lock(%s)", (same_key,))
        second_same = bool(second_cursor.fetchone()[0])
        second_cursor.execute("SELECT pg_try_advisory_lock(%s)", (other_key,))
        second_other = bool(second_cursor.fetchone()[0])
        second_cursor.execute("SELECT pg_advisory_unlock(%s)", (other_key,))
        first_cursor.execute("SELECT pg_advisory_unlock(%s)", (same_key,))
    result = {
        "same_domain_lock": "PASS" if first_same and not second_same else "FAIL",
        "different_domain_lockability": "PASS" if first_same and second_other else "FAIL",
        "persistent_rows_written": 0,
        "supplier_network_calls": 0,
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    if "FAIL" in result.values():
        raise SystemExit(1)


if __name__ == "__main__":
    main()
