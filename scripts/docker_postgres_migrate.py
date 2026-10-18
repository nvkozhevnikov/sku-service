#!/usr/bin/env python3
"""Verify immutable 001-011 and apply all migrations safely/idempotently."""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from universal_supplier.postgres import PostgresConfig

MIGRATIONS = tuple(sorted((ROOT / "migrations").glob("[0-9][0-9][0-9]_*.sql")))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


OUTER_BEGIN = re.compile(r"\A(?:\s*--[^\n]*\n)*\s*BEGIN\s*;", re.IGNORECASE)
OUTER_COMMIT = re.compile(r"COMMIT\s*;\s*\Z", re.IGNORECASE)
TOP_LEVEL_TRANSACTION_CONTROL = re.compile(
    r"^\s*(?:BEGIN|COMMIT|ROLLBACK|START\s+TRANSACTION)\s*;", re.IGNORECASE | re.MULTILINE
)


def migration_body(sql: str) -> str:
    """Return the migration SQL without its own outer BEGIN;/COMMIT; wrapper.

    The runner executes the body and its schema_migrations INSERT inside one
    transaction, so a crash can never leave an applied-but-unrecorded file.
    The file on disk (and its checksum) is unchanged.
    """
    begin = OUTER_BEGIN.match(sql)
    commit = OUTER_COMMIT.search(sql)
    if begin and commit and begin.end() <= commit.start():
        sql = sql[begin.end():commit.start()]
    if TOP_LEVEL_TRANSACTION_CONTROL.search(sql):
        raise RuntimeError("Migration contains transaction control outside its outer BEGIN;/COMMIT; wrapper")
    return sql


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
                    body = migration_body(path.read_text(encoding="utf-8"))
                    with connection.transaction():
                        cursor.execute(body)
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
