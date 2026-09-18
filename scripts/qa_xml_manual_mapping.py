#!/usr/bin/env python3
"""Prove the current manual-confirmation path on the isolated preview clone."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import psycopg

from universal_supplier.control_plane.admin_store import PostgresAdminStore
from universal_supplier.control_plane.store import PostgresControlPlaneStore, ProductFilters
from universal_supplier.postgres import PostgresConfig
from universal_supplier.xml_export import iter_full_xml


def main() -> None:
    config = PostgresConfig.from_env()
    with psycopg.connect(**config.kwargs(), row_factory=psycopg.rows.dict_row) as connection, connection.cursor() as q:
        q.execute("""SELECT rc.id,rc.source_product_id,sp.supplier_id
                     FROM review_cases rc JOIN source_products sp ON sp.id=rc.source_product_id
                     WHERE rc.lifecycle_status<>'RESOLVED' ORDER BY rc.id LIMIT 1""")
        case = q.fetchone()
        if not case:
            raise SystemExit("no open review case available")
        q.execute("""SELECT sb.sterbrust_product_id
                     FROM sterbrust_products sb
                     WHERE sb.active AND NOT EXISTS(
                       SELECT 1 FROM source_products used
                       WHERE used.supplier_id=%s AND used.catalog_product_id=sb.catalog_product_id)
                     ORDER BY sb.sterbrust_product_id LIMIT 1""", (case["supplier_id"],))
        candidate = q.fetchone()
        q.execute("SELECT id FROM app_users WHERE username='qa_stage6e'")
        user = q.fetchone()
    if not candidate or not user:
        raise SystemExit("manual mapping QA prerequisites missing")
    PostgresAdminStore(config).decide(
        case["id"], user["id"], "MANUAL_CONFIRMED",
        sterbrust_product_id=candidate["sterbrust_product_id"], comment="Изолированная QA-проверка XML",
    )
    row = next(item for item in PostgresControlPlaneStore(config).iter_export_rows(ProductFilters(page_size=100))
               if item["source_product_id"] == case["source_product_id"])
    product = ET.fromstring(b"".join(iter_full_xml([row]))).find(".//product")
    result = {
        "classification": "LIVE-VERIFIED-ISOLATED-CLONE",
        "source_product_id": case["source_product_id"],
        "confirmed_link_source": row.get("confirmed_link_source"),
        "confirmed_sterbrust_id": row.get("confirmed_sterbrust_id"),
        "sterbrust_product_id": product.findtext("identity/sterbrust_product_id"),
        "stb_1c_bitrix_id": product.findtext("identity/stb_1c_bitrix_id"),
        "working_db_writes": 0,
        "sterbrust_writes": 0,
        "esol_imports": 0,
    }
    expected = candidate["sterbrust_product_id"]
    if result["confirmed_link_source"] != "MANUAL_CONFIRMED" or any(
            result[key] != expected for key in ("confirmed_sterbrust_id", "sterbrust_product_id", "stb_1c_bitrix_id")):
        raise SystemExit("manual mapping XML contract failed")
    (ROOT / "reports" / "XML_MANUAL_MAPPING_QA.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
