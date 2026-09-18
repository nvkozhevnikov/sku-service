#!/usr/bin/env python3
"""Destructive only to the explicitly selected QA database; never crawls."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from universal_supplier.control_plane.queue import PostgresDomainLease, PostgresJobQueue
from universal_supplier.postgres import PostgresConfig


def main() -> None:
    config = PostgresConfig.from_env()
    with psycopg.connect(**config.kwargs(), autocommit=True) as connection, connection.cursor() as cursor:
        cursor.execute("DELETE FROM crawl_jobs WHERE supplier_id IN (SELECT id FROM suppliers WHERE code LIKE 'stage6c_qa_%')")
        cursor.execute("DELETE FROM suppliers WHERE code LIKE 'stage6c_qa_%'")
        cursor.execute("""INSERT INTO suppliers(code,name,base_url,adapter_name,rate_settings)
            VALUES ('stage6c_qa_a','QA A','https://same.qa.invalid','fake','{"max_concurrency":2}'),
                   ('stage6c_qa_b','QA B','https://same.qa.invalid','fake','{}'),
                   ('stage6c_qa_c','QA C','https://other.qa.invalid','fake','{}') RETURNING id""")
        ids = [row[0] for row in cursor.fetchall()]
    queue = PostgresJobQueue(config, lease_seconds=1)
    first = queue.enqueue(ids[0], trigger_type="schedule", crawl_type="full")
    duplicate = queue.enqueue(ids[0], trigger_type="manual", crawl_type="full")
    claimed = queue.claim_next()
    with PostgresDomainLease(config, "same.qa.invalid") as one:
        with PostgresDomainLease(config, "same.qa.invalid") as two:
            same_domain = one.acquired and not two.acquired
        with PostgresDomainLease(config, "other.qa.invalid") as other:
            different_domains = one.acquired and other.acquired
    with psycopg.connect(**config.kwargs(), autocommit=True) as connection, connection.cursor() as cursor:
        cursor.execute("UPDATE crawl_jobs SET lease_expires_at=now()-interval '1 second' WHERE id=%s", (claimed.id,))
    recovered = queue.recover_stale()
    with psycopg.connect(**config.kwargs(), autocommit=True) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT status,settings_snapshot FROM crawl_jobs WHERE id=%s", (claimed.id,))
        status, snapshot = cursor.fetchone()
        cursor.execute("SELECT count(*) FROM schema_migrations")
        migrations = cursor.fetchone()[0]
    result = {
        "migration_count": migrations,
        "schedule_dedup": first.status == "queued" and duplicate.status == "skipped_already_queued",
        "manual_schedule_collision_guard": duplicate.deduplicated_job_id == first.job_id,
        "same_domain_lock": same_domain,
        "different_domain_parallel_lockability": different_domains,
        "settings_snapshot": bool(snapshot.get("rate_settings") is not None),
        "stale_job_recovery": recovered == 1 and status == "stale_failed",
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    if not all(value is True for key, value in result.items() if key != "migration_count") or migrations != 12:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
