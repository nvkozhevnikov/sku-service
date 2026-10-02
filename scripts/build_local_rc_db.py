"""Restore the pinned historical supplier snapshot into a new local RC database.

This is a one-shot operation for an already-started dedicated PostgreSQL 17.11
cluster on 127.0.0.1:55449. It never starts PostgreSQL or touches source DBs.
"""

from __future__ import annotations

import hashlib
import argparse
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys

import psycopg
from psycopg import sql


ROOT = Path(__file__).resolve().parents[1]
BIN = Path(r"C:\Users\man503\AppData\Local\Temp\universal_supplier_stage4_pg17_runtime\bin")
DATA = ROOT.parent / "universal_supplier_rc_pg17_data"
DUMP = ROOT / "work" / "tmp" / "rc_source_backups" / "main_supplier_snapshot_20260930.dump"
DUMP_SHA = "E565BC5571328497FD6FE6034665D95CCAD907893E655247556E77316D9CC011"
PASSFILE = Path(os.environ["LOCALAPPDATA"]) / "UniversalSupplier" / "rc-local" / "pgpass.conf"
REPORT = ROOT / "reports" / "RC_LOCAL" / "historical_restore.json"
SOURCE_IDS = {7689025282387590508, 7690591088719854484}


def _digest(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            sha.update(chunk)
    return sha.hexdigest().upper()


def _admin_password() -> str:
    if not PASSFILE.is_file():
        raise RuntimeError("local RC passfile is missing")
    for line in PASSFILE.read_text(encoding="utf-8").splitlines():
        fields = line.split(":", 4)
        if len(fields) == 5 and fields[:4] == ["127.0.0.1", "55449", "*", "rc_admin"]:
            return fields[4]
    raise RuntimeError("RC admin passfile entry is missing")


def _connect(port: int, dbname: str, user: str, *, password: str | None = None):
    return psycopg.connect(host="127.0.0.1", port=port, dbname=dbname,
                           user=user, password=password, autocommit=True,
                           options="-c default_transaction_read_only=on" if port != 55449 else None)


def _identity(connection) -> tuple:
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_database(),current_user,current_setting('server_version'),"
                       "pg_is_in_recovery(),system_identifier FROM pg_control_system()")
        return tuple(cursor.fetchone())


def _source_preflight() -> dict:
    checks = {}
    for port, dbname, user, expected_id, code_counts in (
        (55448, "main_supplier_snapshot", "snapshot_admin", 7690591088719854484,
         {"partner_st": 1222, "optimum": 1351}),
        (55447, "stage4_commercial_qa", "stage4_runtime", 7689025282387590508,
         {"intervesp": 58, "beka_mak": 83, "beka_mak_tr": 47}),
    ):
        with _connect(port, dbname, user) as connection:
            identity = _identity(connection)
            if identity != (dbname, user, "17.11", False, expected_id):
                raise RuntimeError(f"source identity changed on port {port}")
            with connection.cursor() as cursor:
                cursor.execute("SELECT s.code,count(sp.id) FROM suppliers s "
                               "LEFT JOIN source_products sp ON sp.supplier_id=s.id "
                               "GROUP BY s.code")
                counts = dict(cursor.fetchall())
            if any(counts.get(code) != value for code, value in code_counts.items()):
                raise RuntimeError(f"source counts changed on port {port}")
            checks[str(port)] = {"identity": identity, "source_products": counts}
    return checks


def _target_preflight(admin_password: str, *, expect_existing: bool = False) -> int:
    if not DATA.is_dir() or not (DATA / "PG_VERSION").is_file():
        raise RuntimeError("dedicated RC data directory does not exist")
    pid_file = DATA / "postmaster.pid"
    if not pid_file.is_file():
        raise RuntimeError("dedicated RC data directory has no server pid file")
    # PostgreSQL on this Windows host writes its data path in the native ANSI
    # code page; reading SHOW data_directory through a UTF-8 client fails on
    # the Cyrillic path. Correlate pid-file path/port and control-file ID with
    # the SQL system ID instead.
    pid_lines = pid_file.read_bytes().decode("cp1251").splitlines()
    if len(pid_lines) < 6 or Path(pid_lines[1]).resolve() != DATA.resolve() or pid_lines[3] != "55449":
        raise RuntimeError("RC postmaster data path or port differs")
    control = subprocess.run([str(BIN / "pg_controldata.exe"), str(DATA)], check=True,
                             capture_output=True, text=True, encoding="cp1251", errors="replace")
    control_match = re.search(r"Database system identifier:\s*(\d+)", control.stdout)
    if not control_match:
        raise RuntimeError("RC pg_control identity unavailable")
    control_id = int(control_match.group(1))
    with _connect(55449, "postgres", "rc_admin", password=admin_password) as connection:
        identity = _identity(connection)
        with connection.cursor() as cursor:
            cursor.execute("SELECT datname FROM pg_database WHERE datname=%s", ("universal_supplier_server",))
            exists = cursor.fetchone()
        if identity[:4] != ("postgres", "rc_admin", "17.11", False) or identity[4] in SOURCE_IDS:
            raise RuntimeError("target is not a new dedicated PostgreSQL 17.11 RC cluster")
        if identity[4] != control_id or bool(exists) != expect_existing:
            raise RuntimeError("target control identity or expected database existence mismatch")
        return identity[4]


def _run(command: list[str], *, password: str) -> None:
    environment = dict(os.environ)
    environment["PGPASSWORD"] = password
    environment["PGPASSFILE"] = str(PASSFILE)
    result = subprocess.run(command, env=environment, cwd=ROOT, check=False,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            encoding="utf-8", errors="replace", timeout=1800)
    if result.returncode:
        raise RuntimeError(f"{Path(command[0]).name} failed ({result.returncode}); "
                           "target remains isolated for inspection. " + result.stderr[-1000:])


def _apply_migrations(password: str) -> None:
    environment = dict(os.environ)
    environment.update(DB_HOST="127.0.0.1", DB_PORT="55449", DB_NAME="universal_supplier_server",
                       DB_USER="rc_admin", DB_PASSWORD=password, DB_SSLMODE="disable")
    result = subprocess.run([sys.executable, str(ROOT / "scripts" / "docker_postgres_migrate.py")],
                            env=environment, cwd=ROOT, check=False, capture_output=True,
                            text=True, encoding="utf-8", errors="replace", timeout=300)
    if result.returncode:
        raise RuntimeError("migration runner failed; isolated DB requires inspection: " + result.stderr[-1000:])


def _create_runtime_roles(password: str) -> None:
    role_passwords = {"rc_runtime": secrets.token_urlsafe(36), "rc_readonly": secrets.token_urlsafe(36)}
    with _connect(55449, "universal_supplier_server", "rc_admin", password=password) as connection:
        with connection.transaction(), connection.cursor() as cursor:
            for role, role_password in role_passwords.items():
                cursor.execute(sql.SQL("CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD {}")
                               .format(sql.Identifier(role), sql.Literal(role_password)))
                cursor.execute(sql.SQL("GRANT CONNECT ON DATABASE universal_supplier_server TO {}")
                               .format(sql.Identifier(role)))
                cursor.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}")
                               .format(sql.Identifier(role)))
            cursor.execute("GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA public TO rc_runtime")
            cursor.execute("GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA public TO rc_runtime")
            cursor.execute("GRANT SELECT ON ALL TABLES IN SCHEMA public TO rc_readonly")
            cursor.execute("ALTER ROLE rc_readonly SET default_transaction_read_only = on")
    # The passfile is outside Git. Never print role secrets or include them in reports.
    with PASSFILE.open("a", encoding="utf-8", newline="\n") as handle:
        for role, role_password in role_passwords.items():
            handle.write(f"127.0.0.1:55449:universal_supplier_server:{role}:{role_password}\n")


def _final_checks(password: str) -> dict:
    with _connect(55449, "universal_supplier_server", "rc_admin", password=password) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT s.code,count(sp.id) FROM suppliers s "
                           "LEFT JOIN source_products sp ON sp.supplier_id=s.id GROUP BY s.code ORDER BY s.code")
            counts = dict(cursor.fetchall())
            cursor.execute("SELECT count(*) FROM schema_migrations")
            migrations = cursor.fetchone()[0]
            cursor.execute("SELECT count(*) FROM catalog_products")
            canonical = cursor.fetchone()[0]
        if counts != {"optimum": 1351, "partner_st": 1222} or migrations != 15 or canonical != 59427:
            raise RuntimeError("restored historical counts/migrations do not match pinned source")
        return {"source_products": counts, "migrations": migrations, "catalog_products": canonical}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--finish-roles-after-verified-restore", action="store_true")
    args = parser.parse_args()
    if not DUMP.is_file() or DUMP.stat().st_size != 80031975 or _digest(DUMP) != DUMP_SHA:
        raise RuntimeError("pinned historical logical backup changed")
    sources = _source_preflight()
    password = _admin_password()
    target_id = _target_preflight(password, expect_existing=args.finish_roles_after_verified_restore)
    if args.finish_roles_after_verified_restore:
        _final_checks(password)
        with _connect(55449, "universal_supplier_server", "rc_admin", password=password) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT rolname FROM pg_roles WHERE rolname IN ('rc_runtime','rc_readonly')")
                if cursor.fetchone():
                    raise RuntimeError("RC roles already exist; refusing to repeat role setup")
    else:
        with _connect(55449, "postgres", "rc_admin", password=password) as connection:
            with connection.cursor() as cursor:
                cursor.execute("CREATE DATABASE universal_supplier_server")
        _run([str(BIN / "pg_restore.exe"), "-h", "127.0.0.1", "-p", "55449", "-U", "rc_admin",
              "-d", "universal_supplier_server", "--no-owner", "--no-privileges", "--exit-on-error",
              "--single-transaction", str(DUMP)], password=password)
        _apply_migrations(password)
    _create_runtime_roles(password)
    result = _final_checks(password)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps({"status": "APPLIED", "target_system_identifier": target_id,
                                  "target_database": "universal_supplier_server", "target_port": 55449,
                                  "source_preflight": sources, "source_dump_sha256": DUMP_SHA,
                                  "restored": result}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"status": "APPLIED", "target_system_identifier": target_id,
                      "restored": result}, ensure_ascii=False))


if __name__ == "__main__":
    main()
