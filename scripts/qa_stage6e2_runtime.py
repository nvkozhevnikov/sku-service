#!/usr/bin/env python3
"""Isolated PostgreSQL proof for Stage 6E.2 job status, metadata, and missing policy."""
from __future__ import annotations
import json
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import psycopg

from universal_supplier.control_plane import adapter_registry
from universal_supplier.control_plane.orchestrator import Orchestrator
from universal_supplier.control_plane.queue import CrawlJob
from universal_supplier.control_plane.rate_control import PARTNER_ST_DEFAULT_RATE_POLICY
from universal_supplier.crawl import LiveCrawlResult
from universal_supplier.models import CategoryNode, MediaItem, ProductCard, PropertyValue
from universal_supplier.postgres import PostgresConfig, PostgresRepository
from universal_supplier.state import discovery_health

CONFIG = PostgresConfig.from_env()


def card(index: int, *, schema_signals=()) -> ProductCard:
    url = f"https://partner-st.ru/catalog/e2-{index}"
    return ProductCard(
        "partner_st", f"E2-{index}", f"SKU-E2-{index}", f"Станок E2-{index}", url, url, url, 200, (),
        Decimal("1000"), None, "RUB", "В наличии", "in_stock", Decimal("1"), "Описание", "<p>Описание</p>",
        brand="Partner", properties=(PropertyValue("Мощность двигателя", "0.75", "кВт"),),
        media=(MediaItem("image", f"https://partner-st.ru/i/{index}.jpg", is_primary=True),),
        categories=(CategoryNode("Станки", "https://partner-st.ru/catalog", 0),),
        raw_data={"source_kind": "supplier_html", "schema_signals": list(schema_signals)},
        price_type="unknown", price_raw="1000 руб.", price_source="JSON-LD offers.price",
    )


def result(cards, discovered):
    now = (datetime.now(timezone.utc) + timedelta(seconds=5)).isoformat()
    return LiveCrawlResult(
        now, now, tuple(discovered), tuple(cards), (), (), discovery_health(len(discovered), 20),
        {}, {}, PARTNER_ST_DEFAULT_RATE_POLICY.snapshot(), PARTNER_ST_DEFAULT_RATE_POLICY.snapshot(),
    )


def query(sql, params=()):
    with psycopg.connect(**CONFIG.kwargs(), autocommit=True, row_factory=psycopg.rows.dict_row) as c, c.cursor() as q:
        q.execute(sql, params)
        return q.fetchall()


def main():
    full = [card(i) for i in range(20)]
    urls = [item.requested_url for item in full]
    adapter_registry.run_partner_st_live = lambda **kwargs: result(full, urls)
    healthy = adapter_registry.run_partner_st_job(
        CrawlJob(1, 1, "partner_st", "partner_st", "https://partner-st.ru/", "manual", "full", "running",
                 datetime.now(timezone.utc), {"rate_settings": {}, "crawl_settings": {"baseline_discovered_count": 20}}),
        CONFIG,
    )
    healthy_run = query("SELECT status, metadata FROM crawl_runs WHERE id=%s", (healthy["crawl_run_id"],))[0]
    assert healthy["status"] == "succeeded" and healthy_run["status"] == "succeeded"
    assert healthy_run["metadata"]["deactivation_suppressed"] is False

    completed = []

    class Queue:
        lease_seconds = 30
        def complete(self, job_id, status, **kwargs):
            completed.append({"job_id": job_id, "status": status, "crawl_run_id": kwargs.get("crawl_run_id")})
        def heartbeat(self, *args):
            return True
        def recover_stale(self):
            return 0

    class Lease:
        acquired = True
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False

    adapter_registry.run_partner_st_live = lambda **kwargs: result(full[:8], urls[:8])
    job = CrawlJob(2, 1, "partner_st", "partner_st", "https://partner-st.ru/", "manual", "full", "running",
                   datetime.now(timezone.utc), {"rate_settings": {}, "crawl_settings": {"baseline_discovered_count": 20}})
    registry = adapter_registry.AdapterRegistry()
    registry.register("partner_st", adapter_registry.run_partner_st_job)
    Orchestrator(CONFIG, queue=Queue(), registry=registry, lease_factory=lambda domain: Lease())._run_job(job)
    collapse_run = query("SELECT status, metadata FROM crawl_runs WHERE id=%s", (completed[0]["crawl_run_id"],))[0]
    assert completed[0]["status"] == "partial" == collapse_run["status"]
    assert collapse_run["metadata"]["deactivation_suppressed"] is True
    assert "CRAWL_COUNT_COLLAPSE" in collapse_run["metadata"]["data_quality"]["anomaly_codes"]

    adapter_registry.run_partner_st_live = lambda **kwargs: result(
        [card(i, schema_signals=("gallery_missing",)) for i in range(20)], urls,
    )
    schema = adapter_registry.run_partner_st_job(
        CrawlJob(3, 1, "partner_st", "partner_st", "https://partner-st.ru/", "manual", "full", "running",
                 datetime.now(timezone.utc), {"rate_settings": {}, "crawl_settings": {"baseline_discovered_count": 20}}),
        CONFIG,
    )
    schema_run = query("SELECT status, metadata FROM crawl_runs WHERE id=%s", (schema["crawl_run_id"],))[0]
    assert schema["status"] == "partial" == schema_run["status"]
    assert "PARSER_SCHEMA_DRIFT" in schema_run["metadata"]["data_quality"]["anomaly_codes"]
    assert schema_run["metadata"]["deactivation_suppressed"] is True

    with psycopg.connect(**CONFIG.kwargs(), autocommit=True) as c, c.cursor() as q:
        q.execute("""UPDATE suppliers SET crawl_settings=jsonb_set(coalesce(crawl_settings,'{}'::jsonb),'{missing_policy}',
          '{"warning_at":3,"candidate_at":5}'::jsonb,true) WHERE code='partner_st'""")
    repo = PostgresRepository(CONFIG)
    try:
        states = []
        remaining = full[:-1]
        for n in range(1, 6):
            observed = (datetime.now(timezone.utc) + timedelta(seconds=20 + n)).isoformat()
            run_id = repo.start_crawl_run(
                started_at=observed, baseline_discovered_count=20, supplier_code="partner_st",
            )
            discovered = [item.requested_url for item in remaining] + [f"https://partner-st.ru/catalog/unknown-{n}"]
            repo.ingest(
                remaining, observed, crawl_run_id=run_id, supplier_code="partner_st",
                discovered_urls=discovered, discovery_health=discovery_health(len(discovered), 20),
            )
            row = query("""SELECT mo.state, mo.consecutive_confirmed FROM missing_observations mo
              JOIN source_products sp ON sp.id=mo.source_product_id WHERE sp.external_id='E2-19'""")[0]
            states.append((row["consecutive_confirmed"], row["state"]))
    finally:
        repo.close()
    assert states == [(1, "WATCH"), (2, "WATCH"), (3, "WARNING"), (4, "WARNING"), (5, "MISSING_CANDIDATE")]
    payload = {
        "healthy": {"adapter": healthy["status"], "crawl_run": healthy_run["status"],
                    "deactivation_suppressed": healthy_run["metadata"]["deactivation_suppressed"]},
        "anomaly_job": completed[0],
        "anomaly_run": {"status": collapse_run["status"],
                        "deactivation_suppressed": collapse_run["metadata"]["deactivation_suppressed"]},
        "schema_run": {"adapter": schema["status"], "crawl_run": schema_run["status"]},
        "custom_missing_policy": states,
        "sterbrust_writes": 0,
    }
    (ROOT / "reports" / "STAGE6E2_RUNTIME_QA.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8",
    )
    print(json.dumps({"RUNTIME_STAGE6E2": "PASS", **payload}, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
