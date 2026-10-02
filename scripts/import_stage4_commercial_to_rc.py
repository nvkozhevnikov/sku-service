"""One-shot, transactional commercial-history import into a backed-up RC DB.

Never restores Stage4 PKs. The target must already contain Partner-ST and
Optimum and migrations 001-015. This script is not a migration runner or a
generic database copier; it has not been authorized for any production DB.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "migrations"
SOURCES = ("intervesp", "beka_mak", "beka_mak_tr")
STAGE4_ID = 7689025282387590508
JSON_COLUMNS = {
    "suppliers": {"crawl_settings", "rate_settings"},
    "source_products": {"raw_data"},
    "offers": {"raw_data"},
    "supplier_http_captures": {"redirect_urls", "diagnostics"},
    "offer_commercial_observations": {"extraction_evidence", "unusable_price_reasons"},
}
TABLES = ("source_products", "offers", "supplier_http_captures", "offer_commercial_observations")


def _identity(cursor) -> tuple:
    cursor.execute("SELECT current_database(),current_user,current_setting('server_version'),"
                   "pg_is_in_recovery(),system_identifier FROM pg_control_system()")
    row = cursor.fetchone()
    return tuple(row.values()) if isinstance(row, dict) else tuple(row)


def _backup_verified(path: Path, expected_sha: str, identity_file: Path,
                     *, target_system_id: int, target_port: int) -> dict:
    if not path.is_file() or path.stat().st_size < 5 or not expected_sha or len(expected_sha) != 64:
        raise RuntimeError("a nonempty existing RC backup and SHA-256 are required")
    checksum = hashlib.sha256()
    with path.open("rb") as handle:
        if handle.read(5) != b"PGDMP":
            raise RuntimeError("RC backup is not a pg_dump custom archive")
        handle.seek(0)
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            checksum.update(chunk)
    digest = checksum.hexdigest()
    if digest.lower() != expected_sha.lower():
        raise RuntimeError("RC backup SHA-256 mismatch")
    if not identity_file.is_file():
        raise RuntimeError("RC backup identity sidecar is required")
    identity = json.loads(identity_file.read_text(encoding="utf-8"))
    if (identity.get("source") != "dedicated_local_rc_pg17"
            or identity.get("database") != "universal_supplier_server"
            or identity.get("port") != target_port
            or identity.get("system_identifier") != target_system_id
            or identity.get("dump_sha256", "").lower() != digest.lower()
            or identity.get("dump_bytes") != path.stat().st_size
            or identity.get("migration_count") != 15):
        raise RuntimeError("RC backup is not bound to the pinned target identity")
    return {"path": str(path.resolve()), "sha256": digest, "bytes": path.stat().st_size}


def _migration_checks(cursor) -> None:
    cursor.execute("SELECT filename,sha256 FROM schema_migrations ORDER BY filename")
    applied = [(name, checksum.strip()) for name, checksum in cursor.fetchall()]
    expected = [(path.name, hashlib.sha256(path.read_bytes()).hexdigest())
                for path in sorted(MIGRATIONS.glob("[0-9][0-9][0-9]_*.sql"))]
    if len(expected) != 15 or applied != expected:
        raise RuntimeError("RC target migration 001-015 order/checksum mismatch")


def _target_preflight(cursor, *, system_id: int, partner_count: int, optimum_count: int) -> dict:
    identity = _identity(cursor)
    if (identity[0] != "universal_supplier_server" or identity[2] != "17.11" or identity[3]
            or identity[4] != system_id or system_id in {STAGE4_ID, 7690591088719854484}):
        raise RuntimeError("RC target identity is not the dedicated PostgreSQL 17.11 cluster")
    _migration_checks(cursor)
    cursor.execute("""SELECT s.code,count(sp.id) FROM suppliers s
                   LEFT JOIN source_products sp ON sp.supplier_id=s.id
                   WHERE s.code IN ('partner_st','optimum','intervesp','beka_mak','beka_mak_tr')
                   GROUP BY s.code""")
    counts = dict(cursor.fetchall())
    if counts.get("partner_st") != partner_count or counts.get("optimum") != optimum_count:
        raise RuntimeError("RC historical Partner-ST/Optimum counts changed")
    if any(counts.get(code, 0) for code in SOURCES):
        raise RuntimeError("RC already contains commercial products; one-shot import refused")
    return {"identity": identity, "supplier_counts": counts}


def _source_snapshot(cursor, *, expected_products: int, expected_captures: int) -> dict:
    if _identity(cursor) != ("stage4_commercial_qa", "stage4_runtime", "17.11", False, STAGE4_ID):
        raise RuntimeError("Stage4 source identity changed")
    cursor.execute("SELECT * FROM suppliers WHERE code = ANY(%s) ORDER BY code", (list(SOURCES),))
    suppliers = [dict(row) for row in cursor.fetchall()]
    if {row["code"] for row in suppliers} != set(SOURCES) or any(row["enabled"] for row in suppliers):
        raise RuntimeError("Stage4 commercial supplier namespace or disabled state changed")
    ids = [row["id"] for row in suppliers]
    rows = {"suppliers": suppliers}
    for table in TABLES:
        cursor.execute(sql.SQL("SELECT * FROM {} WHERE supplier_id = ANY(%s) ORDER BY id").format(
            sql.Identifier(table)), (ids,))
        rows[table] = [dict(row) for row in cursor.fetchall()]
    if (len(rows["source_products"]) != expected_products or
            len(rows["offers"]) != expected_products or
            len(rows["supplier_http_captures"]) != expected_captures or
            len(rows["offer_commercial_observations"]) != expected_captures):
        raise RuntimeError("Stage4 source counts changed since the operator preflight")
    if (any(row["catalog_product_id"] is not None for row in rows["source_products"])
            or any(row["active"] for row in rows["offers"])):
        raise RuntimeError("Stage4 has linked products or active commercial offers")
    identities = [(row["supplier_id"], row["external_id"]) for row in rows["source_products"]]
    if len(set(identities)) != len(identities) or any(not row["external_id_is_stable"] for row in rows["source_products"]):
        raise RuntimeError("Stage4 commercial supplier-scoped identity is not unique/stable")
    return rows


def _insert(cursor, table: str, row: dict, *, replace: dict | None = None, exclude: set[str] | None = None) -> int:
    values = {key: value for key, value in row.items() if key not in {"id", *(exclude or set())}}
    values.update(replace or {})
    columns = list(values)
    parameters = [Jsonb(values[key]) if key in JSON_COLUMNS.get(table, set()) else values[key]
                  for key in columns]
    statement = sql.SQL("INSERT INTO {} ({}) VALUES ({}) RETURNING id").format(
        sql.Identifier(table), sql.SQL(",").join(map(sql.Identifier, columns)),
        sql.SQL(",").join(sql.Placeholder() for _ in columns))
    cursor.execute(statement, parameters)
    return cursor.fetchone()[0]


def _copy(cursor, source: dict) -> dict:
    supplier_map = {}
    for row in source["suppliers"]:
        cursor.execute("SELECT id,enabled,base_url,adapter_name FROM suppliers WHERE code=%s", (row["code"],))
        existing = cursor.fetchone()
        if existing:
            if existing[1:] != (False, row["base_url"], row["adapter_name"]):
                raise RuntimeError("existing commercial supplier metadata differs")
            supplier_map[row["id"]] = existing[0]
        else:
            supplier_map[row["id"]] = _insert(cursor, "suppliers", row)
    product_map = {}
    for row in source["source_products"]:
        product_map[row["id"]] = _insert(cursor, "source_products", row,
                                         replace={"supplier_id": supplier_map[row["supplier_id"]]})
    offer_map = {}
    for row in source["offers"]:
        offer_map[row["id"]] = _insert(cursor, "offers", row, replace={
            "supplier_id": supplier_map[row["supplier_id"]],
            "source_product_id": product_map[row["source_product_id"]]})
    capture_map = {}
    for row in source["supplier_http_captures"]:
        diagnostics = dict(row["diagnostics"] or {})
        diagnostics["rc_import_origin"] = "stage4_qa_history"
        if row["id"] in {6, 7, 8, 9, 11}:
            diagnostics["rc_import_evidence_integrity"] = "synthetic_test_hash_not_verified"
        capture_map[row["id"]] = _insert(cursor, "supplier_http_captures", row, replace={
            "supplier_id": supplier_map[row["supplier_id"]],
            "source_product_id": product_map[row["source_product_id"]],
            "diagnostics": diagnostics})
    for row in source["offer_commercial_observations"]:
        _insert(cursor, "offer_commercial_observations", row, replace={
            "supplier_id": supplier_map[row["supplier_id"]],
            "source_product_id": product_map[row["source_product_id"]],
            "offer_id": offer_map[row["offer_id"]], "capture_id": capture_map[row["capture_id"]]})
    return {"supplier_ids_remapped": len(supplier_map), "source_products": len(product_map),
            "offers": len(offer_map), "captures": len(capture_map),
            "observations": len(source["offer_commercial_observations"])}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-host", required=True)
    parser.add_argument("--target-port", type=int, required=True)
    parser.add_argument("--target-user", required=True)
    parser.add_argument("--confirm-target", required=True)
    parser.add_argument("--target-system-id", type=int, required=True)
    parser.add_argument("--expected-partner-count", type=int, required=True)
    parser.add_argument("--expected-optimum-count", type=int, required=True)
    parser.add_argument("--expected-source-products", type=int, required=True)
    parser.add_argument("--expected-source-captures", type=int, required=True)
    parser.add_argument("--target-backup", type=Path, required=True)
    parser.add_argument("--target-backup-sha256", required=True)
    parser.add_argument("--target-backup-identity-file", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if (args.confirm_target != "universal_supplier_server" or args.target_port in {55447, 55448}
            or args.target_host not in {"127.0.0.1", "postgres"}):
        raise SystemExit("dedicated RC target confirmation/endpoint required")
    backup = _backup_verified(args.target_backup, args.target_backup_sha256,
                              args.target_backup_identity_file,
                              target_system_id=args.target_system_id, target_port=args.target_port)
    with psycopg.connect(host="127.0.0.1", port=55447, dbname="stage4_commercial_qa",
                         user="stage4_runtime", autocommit=True,
                         options="-c default_transaction_read_only=on", row_factory=dict_row) as source_connection:
        with source_connection.transaction(), source_connection.cursor() as source_cursor:
            source_cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            source = _source_snapshot(source_cursor, expected_products=args.expected_source_products,
                                      expected_captures=args.expected_source_captures)
    # Stage4 is closed before the target transaction; no source lock is held.
    with psycopg.connect(host=args.target_host, port=args.target_port, dbname="universal_supplier_server",
                         user=args.target_user, password=os.getenv("RC_TARGET_PASSWORD") or None,
                         autocommit=True) as target_connection:
        with target_connection.transaction(), target_connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ("rc-commercial-import-v1",))
            preflight = _target_preflight(cursor, system_id=args.target_system_id,
                                          partner_count=args.expected_partner_count,
                                          optimum_count=args.expected_optimum_count)
            copied = _copy(cursor, source)
            cursor.execute("""SELECT count(*) FROM (SELECT supplier_id,external_id FROM source_products
                           WHERE external_id_is_stable GROUP BY supplier_id,external_id HAVING count(*)>1) q""")
            if cursor.fetchone()[0]:
                raise RuntimeError("source product duplicates after commercial import")
            cursor.execute("""SELECT count(*) FROM (SELECT supplier_id,external_offer_id FROM offers
                           WHERE external_id_is_stable GROUP BY supplier_id,external_offer_id HAVING count(*)>1) q""")
            if cursor.fetchone()[0]:
                raise RuntimeError("offer duplicates after commercial import")
            cursor.execute("""SELECT count(*) FROM offers o JOIN suppliers s ON s.id=o.supplier_id
                           WHERE s.code = ANY(%s) AND o.active""", (list(SOURCES),))
            if cursor.fetchone()[0]:
                raise RuntimeError("commercial offer activated during import")
    report = {"status": "APPLIED", "source_system_identifier": STAGE4_ID,
              "target_system_identifier": args.target_system_id, "backup": backup,
              "source_counts": {table: len(source[table]) for table in TABLES},
              "target_before": preflight, "copied": copied,
              "note": "Stage4 QA history; captures 6,7,8,9,11 have synthetic test hashes and are explicitly tagged"}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, default=str, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "APPLIED", "copied": copied}, ensure_ascii=False))


if __name__ == "__main__":
    main()
