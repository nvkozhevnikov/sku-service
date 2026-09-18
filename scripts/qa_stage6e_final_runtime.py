#!/usr/bin/env python3
"""Read-only Stage 6E XML QA plus a rolled-back legacy timestamp probe."""
from __future__ import annotations

import hashlib
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from universal_supplier.control_plane.store import ProductFilters, PostgresControlPlaneStore
from universal_supplier.postgres import PostgresConfig
from universal_supplier.xml_export import (
    canonical_false_match_violations,
    pp800f_in_artifact,
    supplier_xml_bytes,
)
from universal_supplier.yml_feed import FeedPolicy, PostgresFeedStore, build_yml, validate_yml_bytes


def scalar(connection, query: str):
    with connection.cursor() as cursor:
        cursor.execute(query)
        return cursor.fetchone()[0]


def main() -> None:
    import psycopg

    config = PostgresConfig.from_env()
    policy = FeedPolicy.load(ROOT / "config" / "yml_feed.json")
    connection = psycopg.connect(**config.kwargs(), autocommit=True)
    try:
        table_counts = {
            name: int(scalar(connection, f"SELECT count(*) FROM {name}"))
            for name in ("source_products", "offers", "catalog_products", "catalog_offer_selection")
        }
        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            snapshot = PostgresFeedStore(connection).load_snapshot()
            artifact_a = build_yml(snapshot, policy)
            artifact_b = build_yml(snapshot, policy)

        root = ET.fromstring(artifact_a.data)
        offers = root.findall("./shop/offers/offer")
        categories = {node.get("id") for node in root.findall("./shop/categories/category")}
        currencies = {node.get("id") for node in root.findall("./shop/currencies/currency")}
        ids = [node.get("id") for node in offers]
        validation = validate_yml_bytes(
            artifact_a.data, expected_rows=snapshot.selected_rows, policy=policy
        )
        supplier_store = PostgresControlPlaneStore(config)
        supplier_parity = {}
        with connection.cursor() as cursor:
            cursor.execute("SELECT code FROM suppliers ORDER BY code")
            supplier_codes = [row[0] for row in cursor.fetchall()]
        for code in supplier_codes:
            filters = ProductFilters(supplier=code, page_size=100)
            rows = list(supplier_store.iter_export_rows(filters))
            xml = supplier_xml_bytes(rows)
            xml_count = len(ET.fromstring(xml).findall(".//product"))
            list_count = int(supplier_store.products(filters)["total"])
            supplier_parity[code] = {
                "xml_products": xml_count,
                "list_products": list_count,
                "result": "PASS" if xml_count == list_count else "FAIL",
            }

        legacy = {}
        with connection.cursor() as cursor:
            cursor.execute("BEGIN")
            try:
                cursor.execute(
                    "UPDATE catalog_offer_selection SET evaluated_at=NULL "
                    "WHERE selection_status='selected'"
                )
                legacy_a = PostgresFeedStore(connection).load_snapshot()
                legacy_artifact_a = build_yml(legacy_a, policy)
                legacy_b = PostgresFeedStore(connection).load_snapshot()
                legacy_artifact_b = build_yml(legacy_b, policy)
                legacy = {
                    "snapshot_timestamp": legacy_a.snapshot_timestamp.isoformat()
                    if legacy_a.snapshot_timestamp else None,
                    "second_snapshot_timestamp": legacy_b.snapshot_timestamp.isoformat()
                    if legacy_b.snapshot_timestamp else None,
                    "byte_identical": legacy_artifact_a.data == legacy_artifact_b.data,
                    "sha256": legacy_artifact_a.sha256,
                    "result": "PASS" if (
                        legacy_a.snapshot_timestamp is not None
                        and legacy_a.snapshot_timestamp == legacy_b.snapshot_timestamp
                        and legacy_artifact_a.data == legacy_artifact_b.data
                    ) else "FAIL",
                }
            finally:
                cursor.execute("ROLLBACK")

        checks = {
            "canonical_well_formed": root.tag == "yml_catalog",
            "canonical_validation": validation.valid,
            "canonical_deterministic": artifact_a.data == artifact_b.data,
            "offer_count_matches_selected": len(offers) == snapshot.db_selected_count,
            "offer_ids_unique": len(ids) == len(set(ids)),
            "offer_ids_are_sterbrust_ids": ids == [str(row["sterbrust_product_id"]) for row in artifact_a.trace_rows],
            "category_references_valid": all(node.findtext("categoryId") in categories for node in offers),
            "currency_references_valid": all(node.findtext("currencyId") in currencies for node in offers),
            "known_false_match_leaks_zero": not canonical_false_match_violations(artifact_a),
            "pp800f_present": pp800f_in_artifact(artifact_a),
            "supplier_filter_parity": all(item["result"] == "PASS" for item in supplier_parity.values()),
            "legacy_null_timestamp_fallback": legacy.get("result") == "PASS",
        }
        result = {
            "result": "PASS" if all(checks.values()) else "FAIL",
            "database": config.dbname,
            "table_counts": table_counts,
            "snapshot_timestamp": snapshot.snapshot_timestamp.isoformat() if snapshot.snapshot_timestamp else None,
            "selected_count": snapshot.db_selected_count,
            "offer_count": len(offers),
            "category_count": len(categories),
            "currency_count": len(currencies),
            "sha256": artifact_a.sha256,
            "size_bytes": len(artifact_a.data),
            "checks": checks,
            "validation": validation.as_jsonable(),
            "supplier_filter_parity": supplier_parity,
            "legacy_null_evaluated_at": legacy,
            "sterbrust_write_methods_used": 0,
            "esol_contract_confirmed": False,
        }
        target = ROOT / "reports" / "STAGE6E_FINAL_RUNTIME_QA.json"
        target.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        if result["result"] != "PASS":
            raise SystemExit(1)
    finally:
        connection.close()


if __name__ == "__main__":
    main()
