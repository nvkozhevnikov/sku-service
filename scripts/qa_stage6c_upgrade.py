#!/usr/bin/env python3
"""Build an isolated 001-011 database and prove the normal runner adds 012."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess
import sys

import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]
DB = os.environ.get("QA_UPGRADE_DB_NAME", "stage6c_upgrade_qa")


def main() -> None:
    admin = dict(host=os.environ["DB_HOST"], port=os.environ.get("DB_PORT", "5432"),
                 dbname="postgres", user=os.environ["POSTGRES_ADMIN_USER"],
                 password=os.environ["POSTGRES_ADMIN_PASSWORD"], sslmode=os.environ.get("DB_SSLMODE", "disable"))
    with psycopg.connect(**admin, autocommit=True) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s", (DB,))
        cursor.execute(sql.SQL("DROP DATABASE IF EXISTS {} ").format(sql.Identifier(DB)))
        cursor.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(sql.Identifier(DB), sql.Identifier(os.environ["DB_USER"])))
    app = dict(admin, dbname=DB, user=os.environ["DB_USER"], password=os.environ["DB_PASSWORD"])
    with psycopg.connect(**app, autocommit=True) as connection, connection.cursor() as cursor:
        cursor.execute("CREATE TABLE schema_migrations(filename text PRIMARY KEY,sha256 char(64) NOT NULL,applied_at timestamptz NOT NULL DEFAULT now())")
        for path in sorted((ROOT / "migrations").glob("0*.sql")):
            if int(path.name[:3]) > 11: continue
            cursor.execute(path.read_text(encoding="utf-8"))
            cursor.execute("INSERT INTO schema_migrations(filename,sha256) VALUES (%s,%s)", (path.name, hashlib.sha256(path.read_bytes()).hexdigest()))
    environment = dict(os.environ, DB_NAME=DB)
    subprocess.run([sys.executable, str(ROOT / "scripts/docker_postgres_migrate.py")], check=True, env=environment)
    with psycopg.connect(**app, autocommit=True) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT count(*),max(filename),to_regclass('public.crawl_jobs') FROM schema_migrations")
        count, latest, queue_table = cursor.fetchone()
    print(f"UPGRADE_001_011_TO_012 = {'PASS' if (count, latest, queue_table)==(12,'012_web_control_plane_scheduler.sql','crawl_jobs') else 'FAIL'}")
    if count != 12 or queue_table != "crawl_jobs": raise SystemExit(1)


if __name__ == "__main__": main()
