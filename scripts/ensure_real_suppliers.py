#!/usr/bin/env python3
"""Idempotently register the two real supported suppliers; performs no crawl."""
from __future__ import annotations

from pathlib import Path
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from universal_supplier.postgres import PostgresConfig

SUPPLIERS = (
    ("partner_st", "Partner-ST", "https://partner-st.ru", "partner_st",
     {"max_concurrency":4,"min_request_interval_seconds":0.2,"jitter_min_seconds":0.0,"jitter_max_seconds":0.1,"request_timeout_seconds":40,"max_attempts":3,"backoff_base_seconds":0.5,"backoff_max_seconds":8,"respect_retry_after":True,"consecutive_error_threshold":5,"cooldown_seconds":30,"max_run_duration_seconds":7200}),
    ("optimum", "Optimum", "https://www.optimum.su", "optimum",
     {"max_concurrency":3,"min_request_interval_seconds":0.15,"jitter_min_seconds":0.0,"jitter_max_seconds":0.1,"request_timeout_seconds":35,"max_attempts":3,"backoff_base_seconds":0.5,"backoff_max_seconds":8,"respect_retry_after":True,"consecutive_error_threshold":5,"cooldown_seconds":30,"max_run_duration_seconds":7200}),
)


def main() -> None:
    import psycopg
    config = PostgresConfig.from_env()
    with psycopg.connect(**config.kwargs(), autocommit=True) as connection, connection.transaction(), connection.cursor() as cursor:
        for code, name, base_url, adapter, rate in SUPPLIERS:
            cursor.execute("""INSERT INTO suppliers(code,name,base_url,adapter_name,enabled,crawl_settings,rate_settings)
                              VALUES (%s,%s,%s,%s,true,'{"crawl_type":"full"}'::jsonb,%s::jsonb)
                              ON CONFLICT(code) DO UPDATE SET name=excluded.name,base_url=excluded.base_url,
                              adapter_name=excluded.adapter_name,updated_at=now() RETURNING id""",
                           (code, name, base_url, adapter, json.dumps(rate)))
            supplier_id = cursor.fetchone()[0]
            cursor.execute("INSERT INTO supplier_schedules(supplier_id,enabled,timezone,crawl_type) VALUES (%s,false,'UTC','full') ON CONFLICT(supplier_id) DO NOTHING", (supplier_id,))
    print("REAL_SUPPLIERS_REGISTERED = 2")
    print("CRAWLS_EXECUTED = 0")


if __name__ == "__main__": main()
