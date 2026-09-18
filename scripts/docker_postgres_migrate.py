#!/usr/bin/env python3
"""Verify immutable 001-011 and apply all migrations safely/idempotently."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from universal_supplier.postgres import PostgresConfig

MIGRATIONS = tuple(sorted((ROOT / "migrations").glob("[0-9][0-9][0-9]_*.sql")))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def expected_hashes() -> dict[str, str]:
    baseline_path = ROOT / "stage6e_migration_baseline_sha256.json"
    if not baseline_path.exists():
        baseline_path = ROOT / "stage6c1_migration_baseline_sha256.json"
    if not baseline_path.exists():
        baseline_path = ROOT / "stage3d_migration_baseline_sha256.json"
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    return baseline["files"]


def main() -> None:
    import psycopg

    expected = expected_hashes()
    actual = {path.name: sha256(path) for path in MIGRATIONS}
    immutable_actual = {name: actual.get(name) for name in expected}
    if immutable_actual != expected:
        missing = sorted(name for name in expected if name not in actual)
        changed = sorted(name for name in expected if name in actual and actual[name] != expected[name])
        raise SystemExit(f"Immutable migrations mismatch: missing={missing}, changed={changed}")

    config = PostgresConfig.from_env()
    with psycopg.connect(**config.kwargs(), autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_lock(%s)", (390011,))
            try:
                cursor.execute(
                    """CREATE TABLE IF NOT EXISTS schema_migrations (
                         filename text PRIMARY KEY,
                         sha256 char(64) NOT NULL,
                         applied_at timestamptz NOT NULL DEFAULT now()
                       )"""
                )
                cursor.execute("SELECT filename, sha256 FROM schema_migrations")
                applied = dict(cursor.fetchall())
                for path in MIGRATIONS:
                    digest = actual[path.name]
                    if path.name in applied:
                        if applied[path.name] != digest:
                            raise RuntimeError(f"Applied migration checksum mismatch: {path.name}")
                        continue
                    cursor.execute(path.read_text(encoding="utf-8"))
                    cursor.execute(
                        "INSERT INTO schema_migrations(filename, sha256) VALUES (%s,%s)",
                        (path.name, digest),
                    )
            finally:
                cursor.execute("SELECT pg_advisory_unlock(%s)", (390011,))
    print(f"MIGRATIONS_APPLIED_OR_VERIFIED = {len(MIGRATIONS)}")
    print(f"MIGRATIONS_001_{len(expected):03d}_IMMUTABLE = PASS")


if __name__ == "__main__":
    main()
