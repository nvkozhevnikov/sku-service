#!/usr/bin/env python3
"""Read-only viewer for packaged traces or live Stage 4 selection state."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect a product trace and selected supplier offer")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--external-id")
    group.add_argument("--sku")
    group.add_argument("--catalog-product-id", type=int)
    parser.add_argument("--database", action="store_true",
                        help="Read current selection from PostgreSQL (never writes)")
    args = parser.parse_args()
    if args.catalog_product_id is not None and not args.database:
        raise SystemExit("--catalog-product-id requires --database")
    if args.database:
        inspect_database(args)
        return
    trace_path = ROOT / "reports" / "STAGE3C_PRODUCT_TRACE.json"
    rows = json.loads(trace_path.read_text(encoding="utf-8"))
    key, wanted = ("source_external_id", args.external_id) if args.external_id else ("SKU", args.sku)
    matches = [row for row in rows if str(row.get(key, "")) == str(wanted)]
    if not matches:
        raise SystemExit(f"No Stage 3C sample product found for {key}={wanted}")
    for row in matches:
        print(json.dumps(row, ensure_ascii=False, indent=2))


def inspect_database(args) -> None:
    import psycopg
    from universal_supplier.postgres import PostgresConfig

    if args.catalog_product_id is not None:
        where, wanted = "sp.catalog_product_id=%s", args.catalog_product_id
    elif args.external_id:
        where, wanted = "sp.external_id=%s", args.external_id
    else:
        where, wanted = "sp.sku=%s", args.sku
    query = f"""SELECT sp.catalog_product_id,sp.id AS source_product_id,sp.external_id,sp.sku,
        sp.name,s.code AS supplier_code,o.id AS source_offer_id,
        cs.selected_offer_id,cs.selection_status,cs.rule_version,
        cs.evidence->>'selection_reason' AS selection_reason,cs.evidence,cs.evaluated_at
        FROM source_products sp JOIN suppliers s ON s.id=sp.supplier_id
        LEFT JOIN offers o ON o.source_product_id=sp.id AND o.offer_kind='default'
        LEFT JOIN catalog_offer_selection cs ON cs.catalog_product_id=sp.catalog_product_id
        WHERE {where} ORDER BY s.code,sp.id"""
    connection = psycopg.connect(**PostgresConfig.from_env().kwargs(), autocommit=True)
    try:
        with connection.cursor() as cursor:
            cursor.execute(query, (wanted,))
            names = [item.name for item in cursor.description]
            found = cursor.fetchall()
        if not found:
            raise SystemExit(f"No database product found for value={wanted}")
        for values in found:
            print(json.dumps(dict(zip(names, values)), ensure_ascii=False, indent=2, default=str))
    finally:
        connection.close()


if __name__ == "__main__":
    main()
