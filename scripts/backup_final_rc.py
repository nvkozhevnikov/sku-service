"""Read-only custom backup with identity and snapshot-consistent counts.

Credentials use libpq environment/passfile, never command arguments or reports.
No init, migration, restore, service start or source ingestion is performed.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import psycopg
from psycopg import sql

TABLES = ("suppliers", "source_products", "offers", "product_matches",
          "catalog_products", "catalog_offer_selection", "supplier_http_captures",
          "offer_commercial_observations", "schema_migrations")

def backup(args):
    target = args.output.resolve()
    pending = target.with_suffix(".dump.partial")
    if target.exists() or pending.exists():
        raise RuntimeError("Backup target exists; refuse overwrite")
    env = os.environ.copy()
    env.update(PGHOST=args.host, PGPORT=str(args.port), PGDATABASE=args.database,
               PGUSER=args.user, PGCONNECT_TIMEOUT="8")
    with psycopg.connect(host=args.host, port=args.port, dbname=args.database,
                         user=args.user, connect_timeout=8,
                         options="-c default_transaction_read_only=on") as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        identity = conn.execute("SELECT current_database(),current_setting('server_version'),"
                                "system_identifier,pg_is_in_recovery(),inet_server_port() "
                                "FROM pg_control_system()").fetchone()
        expected = (args.database, "17.11", args.expected_system_id, False, args.port)
        if identity != expected:
            raise RuntimeError("Backup identity preflight rejected")
        counts = {}
        for table in TABLES:
            exists = conn.execute("SELECT to_regclass(%s)", ("public." + table,)).fetchone()[0]
            if exists:
                counts[table] = conn.execute(sql.SQL("SELECT count(*) FROM {}").format(
                    sql.Identifier(table))).fetchone()[0]
        namespaces = dict(conn.execute("SELECT s.code,count(p.id) FROM suppliers s "
                                       "LEFT JOIN source_products p ON p.supplier_id=s.id "
                                       "GROUP BY s.code").fetchall())
        expected_ns = {"partner_st":1225,"optimum":1353,"intervesp":1642,
                       "beka_mak":109,"beka_mak_tr":78}
        if namespaces != expected_ns or counts["source_products"] != 4407:
            raise RuntimeError("Backup source-count preflight rejected")
        snapshot = conn.execute("SELECT pg_export_snapshot()").fetchone()[0]
        timestamp = str(conn.execute("SELECT clock_timestamp()").fetchone()[0])
        target.parent.mkdir(parents=True, exist_ok=True)
        command = [str(args.pg_dump), "--format=custom", "--no-owner", "--no-privileges",
                   "--snapshot=" + snapshot, "--file=" + str(pending)]
        result = subprocess.run(command, env=env, capture_output=True, timeout=1800)
        if result.returncode:
            # Do not leak raw libpq diagnostics containing connection credentials.
            raise RuntimeError(f"pg_dump failed, exit={result.returncode}; partial retained")
        inventory = subprocess.run([str(args.pg_restore), "--list", str(pending)],
                                   capture_output=True, timeout=60)
        if inventory.returncode or not pending.stat().st_size:
            raise RuntimeError("Custom dump inventory failed; partial retained")
        digest = hashlib.file_digest(pending.open("rb"), "sha256").hexdigest()
        pending.rename(target)
        target.with_suffix(".dump.sha256").write_text(digest + "  " + target.name + "\n", encoding="ascii")
        metadata = {"status":"READ_ONLY_BACKUP_VERIFIED_NOT_RESTORED", "identity":list(identity),
                    "snapshot_timestamp":timestamp, "counts":counts, "namespaces":namespaces,
                    "bytes":target.stat().st_size, "sha256":digest,
                    "format":"PostgreSQL custom", "restore_rehearsal_executed":False}
        target.with_suffix(".dump.json").write_text(json.dumps(metadata, indent=2)+"\n", encoding="utf-8")
        target.with_suffix(".dump.list").write_bytes(inventory.stdout)
        return metadata

if __name__ == "__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--host",required=True); p.add_argument("--port",required=True,type=int)
    p.add_argument("--database",required=True); p.add_argument("--user",required=True)
    p.add_argument("--expected-system-id",required=True,type=int)
    p.add_argument("--pg-dump",type=Path,default=Path("pg_dump"))
    p.add_argument("--pg-restore",type=Path,default=Path("pg_restore"))
    p.add_argument("--output",type=Path,required=True)
    try:
        print(json.dumps(backup(p.parse_args()),indent=2))
    except Exception as exc:
        print("BACKUP_FAILED: " + type(exc).__name__ + "; no restore or DB mutation performed",file=sys.stderr)
        sys.exit(1)
