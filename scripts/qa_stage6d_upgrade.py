#!/usr/bin/env python3
"""Create a new isolated 001-012 database and prove the runner adds 013."""
from __future__ import annotations
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]
DB = os.environ.get("QA_UPGRADE_DB_NAME", "stage6d_upgrade_012_to_013")


def main() -> None:
    admin = dict(host=os.environ["DB_HOST"], port=os.environ.get("DB_PORT", "5432"), dbname="postgres",
                 user=os.environ["POSTGRES_ADMIN_USER"], password=os.environ["POSTGRES_ADMIN_PASSWORD"],
                 sslmode=os.environ.get("DB_SSLMODE", "disable"))
    with psycopg.connect(**admin, autocommit=True) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_database WHERE datname=%s", (DB,))
        if cursor.fetchone(): raise SystemExit(f"Refusing to overwrite existing QA database: {DB}")
        cursor.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(sql.Identifier(DB), sql.Identifier(os.environ["DB_USER"])))
    app = dict(admin, dbname=DB, user=os.environ["DB_USER"], password=os.environ["DB_PASSWORD"])
    with psycopg.connect(**app, autocommit=True) as connection, connection.cursor() as cursor:
        cursor.execute("CREATE TABLE schema_migrations(filename text PRIMARY KEY,sha256 char(64) NOT NULL,applied_at timestamptz NOT NULL DEFAULT now())")
        for path in sorted((ROOT / "migrations").glob("0*.sql")):
            if int(path.name[:3]) > 12: continue
            cursor.execute(path.read_text(encoding="utf-8"))
            cursor.execute("INSERT INTO schema_migrations(filename,sha256) VALUES (%s,%s)", (path.name, hashlib.sha256(path.read_bytes()).hexdigest()))
    subprocess.run([sys.executable, str(ROOT / "scripts/docker_postgres_migrate.py")], check=True, env=dict(os.environ, DB_NAME=DB))
    with psycopg.connect(**app, autocommit=True) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT count(*),max(filename),to_regclass('public.review_cases'),to_regclass('public.app_users') FROM schema_migrations")
        result = cursor.fetchone()
    passed = result == (13, "013_operator_admin_review.sql", "review_cases", "app_users")
    print(f"UPGRADE_012_TO_013 = {'PASS' if passed else 'FAIL'}")
    print(f"MIGRATION_COUNT = {result[0]}")
    if not passed: raise SystemExit(1)


if __name__ == "__main__": main()
