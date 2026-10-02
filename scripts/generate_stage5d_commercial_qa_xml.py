"""Render a read-only Stage 5D commercial diagnostic XML.

The database mode reads the current passive-commercial projection only.  It
does not run selection, create a Sterbrust identity, or produce an ESOL
payload.  The old report mode remains for offline regression fixtures.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from universal_supplier.xml_export import supplier_xml_bytes


CURRENT_COMMERCIAL_ROWS_SQL = """
SELECT sp.id AS source_product_id, sp.external_id, s.code AS supplier_code,
       sp.source_url, sp.canonical_url, sp.sku, sp.name, sp.brand_raw,
       sp.manufacturer_raw, sp.first_seen_at, sp.last_seen_at,
       sp.last_success_at, sp.last_changed_at, sp.last_http_status, sp.active,
       o.price_type, o.availability_raw, o.availability_normalized, o.quantity,
       co.price_state AS price_source, co.price_raw,
       CASE WHEN co.price_state = 'numeric_public' THEN co.price ELSE NULL END AS price,
       CASE WHEN co.price_state = 'numeric_public' THEN co.old_price ELSE NULL END AS old_price,
       CASE WHEN co.price_state = 'numeric_public' THEN co.currency ELSE NULL END AS currency
FROM suppliers s
JOIN source_products sp ON sp.supplier_id = s.id
JOIN offers o ON o.supplier_id = s.id AND o.source_product_id = sp.id
JOIN LATERAL (
    SELECT co.*
    FROM offer_commercial_observations co
    WHERE co.supplier_id = s.id
      AND co.source_product_id = sp.id
      AND COALESCE(co.region_code, '') = COALESCE(sp.raw_data ->> '_commercial_projection_region', '')
    ORDER BY co.observed_at DESC, co.id DESC
    LIMIT 1
) co ON true
WHERE s.code IN (%s, %s, %s)
ORDER BY s.code, sp.external_id
"""


def rows_from_payloads(payloads: list[dict]) -> list[dict]:
    """Keep only persisted source observations, deduplicated by supplier/ID."""
    rows: dict[tuple[str, str], dict] = {}
    for payload in payloads:
        for item in payload.get("rows", []):
            if item.get("result") != "PERSISTED":
                continue
            external_id = str(item.get("external_id") or "").strip()
            site = str(item.get("site") or "").strip()
            if not site or not external_id:
                raise ValueError("persisted report row has no site/external ID")
            model = item.get("parsed_model") or item.get("expected_model") or ""
            rows[(site, external_id)] = {
                # Stage 5C reports deliberately do not export database primary
                # keys.  Leave this blank instead of inventing one.
                "source_product_id": "",
                "external_id": external_id,
                "supplier_code": site,
                "source_url": item.get("requested_url"),
                "name": model,
                "own_model": model,
                "price": item.get("price"),
                "currency": item.get("currency"),
                "price_source": item.get("price_state"),
                "availability_normalized": item.get("availability") or "unknown",
                "description_text": "",
                "category": "",
                "evidence_ref": item.get("evidence_ref"),
            }
    return [rows[key] for key in sorted(rows)]


def rows_from_reports(paths: list[Path]) -> list[dict]:
    return rows_from_payloads([json.loads(path.read_text(encoding="utf-8")) for path in paths])


def rows_from_database_records(records: list[dict]) -> list[dict]:
    """Project only the current commercial observation without an identity claim."""
    rows: list[dict] = []
    for item in records:
        row = dict(item)
        price_state = str(row.get("price_source") or "")
        if price_state != "numeric_public":
            row["price"] = None
            row["old_price"] = None
            row["currency"] = None
        # No query in this diagnostic path joins matches or Sterbrust registry.
        row["confirmed_sterbrust_id"] = None
        row["decision"] = None
        row["own_model"] = row.get("sku") or row.get("name") or ""
        row["description_text"] = ""
        row["category"] = ""
        rows.append(row)
    return rows


def rows_from_database(*, host: str, port: int, dbname: str, user: str,
                       password: str | None, sslmode: str) -> list[dict]:
    import psycopg

    targets = {
        ("127.0.0.1", 55447, "stage4_commercial_qa", "stage4_runtime"):
            ("stage4_commercial_qa", "stage4_runtime", "17.11", False, 7689025282387590508),
        ("127.0.0.1", 55449, "universal_supplier_server", "rc_readonly"):
            ("universal_supplier_server", "rc_readonly", "17.11", False, 7691270601420084116),
    }
    expected = targets.get((host, port, dbname, user))
    if expected is None:
        raise RuntimeError("diagnostic XML target is not an approved isolated database")

    kwargs = {"host": host, "port": port, "dbname": dbname, "user": user,
              "sslmode": sslmode, "autocommit": True,
              "options": "-c default_transaction_read_only=on"}
    if password:
        kwargs["password"] = password
    with psycopg.connect(**kwargs) as connection:
        with connection.cursor() as cursor:
            cursor.execute("BEGIN READ ONLY")
            cursor.execute("SELECT current_database(),current_user,current_setting('server_version'),"
                           "pg_is_in_recovery(),system_identifier FROM pg_control_system()")
            if tuple(cursor.fetchone()) != expected:
                raise RuntimeError("diagnostic XML target identity changed")
            cursor.execute(CURRENT_COMMERCIAL_ROWS_SQL, ("intervesp", "beka_mak", "beka_mak_tr"))
            columns = [item.name for item in cursor.description]
            records = [dict(zip(columns, values)) for values in cursor.fetchall()]
            cursor.execute("COMMIT")
    return rows_from_database_records(records)


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a read-only Stage 5D commercial QA XML.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--report", type=Path, action="append",
                        help="Offline legacy report input for a regression fixture.")
    source.add_argument("--from-db", action="store_true",
                        help="Read only current passive-commercial rows from an explicit PostgreSQL connection.")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--summary-out", type=Path,
                        help="Optional JSON summary for the generated diagnostic XML.")
    parser.add_argument("--db-host")
    parser.add_argument("--db-port", type=int)
    parser.add_argument("--db-name")
    parser.add_argument("--db-user")
    parser.add_argument("--db-sslmode", default="disable")
    parser.add_argument("--db-password-env", default="DB_PASSWORD")
    parser.add_argument("--db-use-password-file", action="store_true")
    args = parser.parse_args()
    if args.from_db:
        required = {"--db-host": args.db_host, "--db-port": args.db_port,
                    "--db-name": args.db_name, "--db-user": args.db_user}
        missing = [key for key, value in required.items() if value in (None, "")]
        password = os.environ.get(args.db_password_env)
        if missing or (not password and not args.db_use_password_file):
            parser.error("--from-db requires explicit DB host/port/name/user and a password source")
        rows = rows_from_database(host=args.db_host, port=args.db_port, dbname=args.db_name,
                                  user=args.db_user, password=password, sslmode=args.db_sslmode)
    else:
        rows = rows_from_reports(args.report)
    document = supplier_xml_bytes(rows)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(document)
    summary = {
        "products": len(rows), "by_supplier": {site: sum(1 for row in rows if row["supplier_code"] == site)
                                        for site in sorted({row["supplier_code"] for row in rows})},
        "current_price_states": {state: sum(1 for row in rows if row.get("price_source") == state)
                                 for state in sorted({str(row.get("price_source") or "") for row in rows})},
        "sha256": hashlib.sha256(document).hexdigest(), "output": str(args.output),
        "diagnostic_only": True,
    }
    if args.summary_out:
        args.summary_out.parent.mkdir(parents=True, exist_ok=True)
        args.summary_out.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
