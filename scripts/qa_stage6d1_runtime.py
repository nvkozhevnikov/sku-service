#!/usr/bin/env python3
"""Isolated runtime pipeline QA with fake crawl cards and no external network."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from universal_supplier.control_plane.admin_store import PostgresAdminStore
from universal_supplier.control_plane.security import hash_password
from universal_supplier.identity_service import RuntimeIdentityService
from universal_supplier.models import CategoryNode, ProductCard
from universal_supplier.postgres import PostgresConfig, PostgresRepository


def card(external_id: str, name: str, sku: str) -> ProductCard:
    return ProductCard(
        supplier_code="optimum", external_id=external_id, sku=sku, name=name,
        requested_url=f"https://www.optimum.su/fake/{external_id}", final_url=f"https://www.optimum.su/fake/{external_id}",
        canonical_url=f"https://www.optimum.su/fake/{external_id}", http_status=200, redirect_urls=(),
        price=Decimal("100"), old_price=None, currency="RUB", availability_raw="Есть",
        availability_normalized="in_stock", quantity=Decimal("1"), description_text="", description_html="",
        brand="Optimum", categories=(CategoryNode("Оснастка", "", 0),), raw_data={"qa":"fake-adapter"},
    )


def main() -> None:
    import psycopg
    config = PostgresConfig.from_env()
    repo = PostgresRepository(config)
    observed = datetime.now(timezone.utc).isoformat()
    try:
        with repo.connection.transaction(), repo.connection.cursor() as q:
            for sterbrust_id, model in (("d1-500", "HCV125"), ("d1-600", "ETM16U"), ("d1-unrelated", "ZZ999")):
                q.execute("INSERT INTO catalog_products(name) VALUES (%s) RETURNING id", (f"Товар {model}",))
                catalog_id = q.fetchone()[0]
                q.execute("""INSERT INTO sterbrust_products(sterbrust_product_id,catalog_product_id,name,normalized_name,brand_raw,normalized_brand,model_raw,normalized_model,category_path,active,raw_data,snapshot_hash,first_seen_at,last_seen_at,last_synced_at)
                             VALUES (%s,%s,%s,%s,'Optimum','optimum',%s,%s,'Оборудование',true,'{}','stage6d1',now(),now(),now())""",
                          (sterbrust_id, catalog_id, f"Станок Optimum {model}", f"станок optimum {model.lower()}", model, model.lower()))
        fake_cards = [
            card("stage6d1-hcv", "Л-образные губки для тисков OPTIMUM HCV 125", "3536222"),
            card("stage6d1-etm", "Ручка для манипулятора OPTIMUM ETM-16U", "handle-etm"),
        ]
        repo.ingest(fake_cards, observed, supplier_code="optimum")
    finally:
        repo.close()

    with psycopg.connect(**config.kwargs(), autocommit=True) as c, c.cursor() as q:
        q.execute("SELECT count(*) FROM product_identity_decisions")
        assert q.fetchone()[0] == 0
    first = RuntimeIdentityService(config).reconcile("optimum")
    admin = PostgresAdminStore(config)
    if not admin.users_exist():
        qa_password = os.environ.get("STAGE6D1_QA_ADMIN_PASSWORD")
        if not qa_password:
            raise RuntimeError("STAGE6D1_QA_ADMIN_PASSWORD is required for isolated QA")
        admin.create_user("qa_admin", "Администратор QA", hash_password(qa_password), "ADMIN")
    user_id = admin.user_by_username("qa_admin")[0].id
    cases = {row["external_id"]: row for row in admin.review_cases()}
    assert set(cases) == {"stage6d1-hcv", "stage6d1-etm"}

    hcv = admin.review_case(cases["stage6d1-hcv"]["id"])
    assert hcv["proposed_candidate"]["sterbrust_product_id"] == hcv["proposed_sterbrust_id"] == "d1-500"
    admin.decide(hcv["id"], user_id, "MANUAL_CONFIRMED", sterbrust_product_id="d1-500", comment="QA existing")
    RuntimeIdentityService(config).reconcile("optimum")
    assert admin.review_case(hcv["id"])["lifecycle_status"] == "RESOLVED"
    with psycopg.connect(**config.kwargs(), autocommit=True) as c, c.cursor() as q:
        q.execute("UPDATE offers SET price=price+1 WHERE source_product_id=%s", (hcv["source_product_id"],))
    RuntimeIdentityService(config).reconcile("optimum")
    assert admin.review_case(hcv["id"])["lifecycle_status"] == "RESOLVED"
    with psycopg.connect(**config.kwargs(), autocommit=True) as c, c.cursor() as q:
        q.execute("UPDATE source_products SET name='Л-образные губки для тисков OPTIMUM HCV 125 PRO' WHERE id=%s", (hcv["source_product_id"],))
    RuntimeIdentityService(config).reconcile("optimum")
    reopened = admin.review_case(hcv["id"])
    assert reopened["lifecycle_status"] == "REOPENED_SOURCE_CHANGED" and reopened["history"]
    with psycopg.connect(**config.kwargs(), autocommit=True) as c, c.cursor() as q:
        q.execute("SELECT sp.catalog_product_id=sb.catalog_product_id FROM source_products sp JOIN sterbrust_products sb ON sb.sterbrust_product_id='d1-500' WHERE sp.id=%s", (hcv["source_product_id"],))
        assert q.fetchone()[0]

    etm = admin.review_case(cases["stage6d1-etm"]["id"])
    assert etm["proposed_candidate"]["sterbrust_product_id"] == "d1-600"
    admin.reject_candidate(etm["id"], user_id, "d1-600", "QA неверный кандидат")
    refreshed = admin.review_case(etm["id"])
    assert refreshed["proposed_candidate"] is None
    assert "d1-600" not in {str(row["sterbrust_product_id"]) for row in refreshed["candidates"]}
    assert "d1-unrelated" not in {str(row["sterbrust_product_id"]) for row in refreshed["candidates"]}

    result = {
        "runtime_backfill": "PASS", "source_products": first["source_products"],
        "identity_decisions": first["identity_decisions"], "review_cases": len(cases),
        "manual_mapping_protected": "PASS", "manual_source_change_reopened": "PASS",
        "commercial_change_no_reopen": "PASS", "review_history_preserved": "PASS",
        "proposed_candidate_exact": "PASS", "rejected_candidate_reappearance": 0,
        "arbitrary_candidates": 0, "network_calls": 0,
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
