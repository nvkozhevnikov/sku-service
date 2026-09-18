#!/usr/bin/env python3
"""Fresh isolated PostgreSQL runtime/status QA; performs no external request."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
from types import SimpleNamespace
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient
from universal_supplier.control_plane import adapter_registry as adapters
from universal_supplier.control_plane.admin_store import PostgresAdminStore
from universal_supplier.control_plane.queue import PostgresJobQueue
from universal_supplier.control_plane.security import hash_password
from universal_supplier.control_plane.store import PostgresControlPlaneStore
from universal_supplier.control_plane.web import create_app
from universal_supplier.identity_service import RuntimeIdentityService
from universal_supplier.models import CategoryNode, ProductCard
from universal_supplier.postgres import PostgresConfig, PostgresRepository


def card(external_id: str, name: str, sku: str) -> ProductCard:
    url = f"https://www.optimum.su/qa-no-network/{external_id}"
    return ProductCard(
        supplier_code="optimum", external_id=external_id, sku=sku, name=name,
        requested_url=url, final_url=url, canonical_url=url, http_status=200, redirect_urls=(),
        price=Decimal("100"), old_price=None, currency="RUB", availability_raw="Есть",
        availability_normalized="in_stock", quantity=Decimal("1"), description_text="",
        description_html="", brand="Optimum", categories=(CategoryNode("Оснастка", "", 0),),
        raw_data={"qa": "fake-adapter-no-network"},
    )


def fake_result(item: ProductCard):
    now = datetime.now(timezone.utc).isoformat()
    return SimpleNamespace(cards=(item,), rows=({"source_url": item.requested_url, "classification": "FETCHED"},), finished_at=now, metadata={
        "effective_rate_policy": adapters.OPTIMUM_DEFAULT_RATE_POLICY.snapshot(),
        "request_metrics": {"requests_total": 0}, "termination_reason": None,
        "qa_transport": "fake-no-network",
    })


def scalar(config, sql: str, params=()):
    import psycopg
    with psycopg.connect(**config.kwargs(), autocommit=True) as connection, connection.cursor() as cursor:
        cursor.execute(sql, params); return cursor.fetchone()[0]


def main() -> None:
    import psycopg
    config = PostgresConfig.from_env()
    qa_password = os.environ.get("STAGE6D2_QA_ADMIN_PASSWORD")
    if not qa_password or len(qa_password) < 12:
        raise RuntimeError("STAGE6D2_QA_ADMIN_PASSWORD with at least 12 characters is required")

    fresh_tables = ("app_users", "source_products", "product_identity_decisions", "review_cases", "crawl_runs", "crawl_jobs")
    with psycopg.connect(**config.kwargs(), autocommit=True) as connection, connection.cursor() as cursor:
        counts = {}
        for table in fresh_tables:
            cursor.execute(f"SELECT count(*) FROM {table}"); counts[table] = cursor.fetchone()[0]
        if any(counts.values()):
            raise RuntimeError(f"QA database is not fresh: {counts}")

    repository = PostgresRepository(config)
    try:
        supplier_id = repository.ensure_supplier("optimum")
        with repository.connection.transaction(), repository.connection.cursor() as cursor:
            cursor.execute("UPDATE suppliers SET crawl_settings=%s::jsonb WHERE id=%s", (json.dumps({"baseline_discovered_count": 1}), supplier_id))
            for sterbrust_id, model in (("d2-500", "HCV125"), ("d2-600", "ETM16U"), ("d2-unrelated", "ZZ999")):
                cursor.execute("INSERT INTO catalog_products(name) VALUES (%s) RETURNING id", (f"Товар {model}",)); catalog_id = cursor.fetchone()[0]
                cursor.execute("""INSERT INTO sterbrust_products(sterbrust_product_id,catalog_product_id,name,normalized_name,brand_raw,normalized_brand,model_raw,normalized_model,category_path,active,raw_data,snapshot_hash,first_seen_at,last_seen_at,last_synced_at)
                    VALUES (%s,%s,%s,%s,'Optimum','optimum',%s,%s,'Оборудование',true,'{}','stage6d2',now(),now(),now())""",
                    (sterbrust_id, catalog_id, f"Станок Optimum {model}", f"станок optimum {model.lower()}", model, model.lower()))
    finally:
        repository.close()

    admin = PostgresAdminStore(config)
    assert not admin.users_exist()
    admin.create_user("qa_admin", "Администратор QA", hash_password(qa_password), "ADMIN")
    user_id = admin.user_by_username("qa_admin")[0].id
    queue = PostgresJobQueue(config)
    original_crawl, original_post = adapters.run_optimum_live_sync, adapters._post_ingest_identity
    try:
        adapters.run_optimum_live_sync = lambda **_: fake_result(card("stage6d2-hcv", "Л-образные губки для тисков OPTIMUM HCV 125", "3536222"))
        def fail_post(*_): raise RuntimeError("forced identity failure")
        adapters._post_ingest_identity = fail_post
        queued = queue.enqueue(supplier_id, trigger_type="manual", crawl_type="full", requested_by_user_id=user_id)
        job = queue.claim_next(); assert job and job.id == queued.job_id
        failed_result = adapters.run_optimum_job(job, config)
        queue.complete(job.id, failed_result["status"], crawl_run_id=failed_result["crawl_run_id"])

        with psycopg.connect(**config.kwargs(), autocommit=True, row_factory=psycopg.rows.dict_row) as connection, connection.cursor() as cursor:
            cursor.execute("SELECT status FROM crawl_jobs WHERE id=%s", (job.id,)); failed_job_status = cursor.fetchone()["status"]
            cursor.execute("SELECT status,error_count,metadata,discovered_count,fetched_count,new_count FROM crawl_runs WHERE id=%s", (failed_result["crawl_run_id"],)); failed_run = cursor.fetchone()
            cursor.execute("SELECT count(*) AS count FROM crawl_errors WHERE crawl_run_id=%s AND error_type='POST_PROCESSING_FAILED'", (failed_result["crawl_run_id"],)); post_errors = cursor.fetchone()["count"]
        assert failed_job_status == failed_run["status"] == "partial"
        assert failed_run["error_count"] == post_errors == 1
        assert failed_run["metadata"]["identity_post_processing"]["status"] == "POST_PROCESSING_FAILED"
        assert failed_run["discovered_count"] == failed_run["fetched_count"] == failed_run["new_count"] == 1
        assert scalar(config, "SELECT count(*) FROM source_products") == 1
        assert scalar(config, "SELECT count(*) FROM product_identity_decisions") == 0

        runs_html = TestClient(create_app(store=PostgresControlPlaneStore(config), queue=queue, admin_store=admin, auth_required=False)).get("/runs").text
        assert "Частично" in runs_html and "Успешно" not in runs_html

        adapters.run_optimum_live_sync = lambda **_: fake_result(card("stage6d2-etm", "Ручка для манипулятора OPTIMUM ETM-16U", "handle-etm"))
        adapters._post_ingest_identity = original_post
        queued2 = queue.enqueue(supplier_id, trigger_type="manual", crawl_type="full", requested_by_user_id=user_id)
        job2 = queue.claim_next(); assert job2 and job2.id == queued2.job_id
        success_result = adapters.run_optimum_job(job2, config)
        queue.complete(job2.id, success_result["status"], crawl_run_id=success_result["crawl_run_id"])
        assert success_result["status"] == "succeeded"
        assert scalar(config, "SELECT count(*) FROM crawl_errors WHERE crawl_run_id=%s AND error_type='POST_PROCESSING_FAILED'", (success_result["crawl_run_id"],)) == 0
        assert scalar(config, "SELECT status FROM crawl_runs WHERE id=%s", (success_result["crawl_run_id"],)) == "succeeded"
        assert scalar(config, "SELECT status FROM crawl_jobs WHERE id=%s", (job2.id,)) == "succeeded"
    finally:
        adapters.run_optimum_live_sync, adapters._post_ingest_identity = original_crawl, original_post

    cases = {row["external_id"]: row for row in admin.review_cases()}
    assert set(cases) == {"stage6d2-hcv", "stage6d2-etm"}
    hcv = admin.review_case(cases["stage6d2-hcv"]["id"])
    assert hcv["proposed_candidate"]["sterbrust_product_id"] == "d2-500"
    admin.decide(hcv["id"], user_id, "MANUAL_CONFIRMED", sterbrust_product_id="d2-500", comment="QA existing")
    RuntimeIdentityService(config).reconcile("optimum")
    assert admin.review_case(hcv["id"])["lifecycle_status"] == "RESOLVED"
    with psycopg.connect(**config.kwargs(), autocommit=True) as connection, connection.cursor() as cursor:
        cursor.execute("UPDATE offers SET price=price+1 WHERE source_product_id=%s", (hcv["source_product_id"],))
    RuntimeIdentityService(config).reconcile("optimum")
    assert admin.review_case(hcv["id"])["lifecycle_status"] == "RESOLVED"
    with psycopg.connect(**config.kwargs(), autocommit=True) as connection, connection.cursor() as cursor:
        cursor.execute("UPDATE source_products SET name=name || ' PRO' WHERE id=%s", (hcv["source_product_id"],))
    RuntimeIdentityService(config).reconcile("optimum")
    assert admin.review_case(hcv["id"])["lifecycle_status"] == "REOPENED_SOURCE_CHANGED"

    etm = admin.review_case(cases["stage6d2-etm"]["id"])
    assert etm["proposed_candidate"]["sterbrust_product_id"] == "d2-600"
    admin.reject_candidate(etm["id"], user_id, "d2-600", "Неверный кандидат")
    assert admin.review_case(etm["id"])["proposed_candidate"] is None

    result = {
        "command": "python scripts/qa_stage6d2_runtime.py", "database_type": "fresh isolated PostgreSQL",
        "database_fresh": True, "network_used": False, "source_products_persisted_after_failure": 1,
        "post_processing_failure_crawl_job": failed_job_status, "post_processing_failure_crawl_run": failed_run["status"],
        "false_successful_runs": 0, "post_processing_failed_visible": "PASS",
        "successful_post_processing_job": "succeeded", "successful_post_processing_run": "succeeded",
        "identity_decisions": scalar(config, "SELECT count(*) FROM product_identity_decisions"),
        "review_cases": len(cases), "manual_mapping_protected": "PASS",
        "source_identity_reopen": "PASS", "commercial_no_reopen": "PASS",
        "candidate_rejection": "PASS", "rejected_candidate_reappearance": 0, "result": "PASS",
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
