from __future__ import annotations

from universal_supplier.partner_pilot import connect_postgres

from dataclasses import dataclass
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import threading
from typing import Any
from urllib.parse import urlparse

from universal_supplier.postgres import PostgresConfig


ACTIVE_STATUSES = {"queued", "running"}
FINAL_STATUSES = {"succeeded", "partial", "failed", "cancelled", "skipped_locked", "skipped_already_queued", "stale_failed"}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def normalized_domain(base_url: str) -> str:
    hostname = (urlparse(base_url).hostname or "").lower().rstrip(".")
    if hostname.startswith("www."):
        hostname = hostname[4:]
    if not hostname:
        raise ValueError(f"base_url has no hostname: {base_url}")
    return hostname.encode("idna").decode("ascii")


def advisory_lock_key(domain: str) -> int:
    raw = hashlib.sha256(domain.encode("utf-8")).digest()[:8]
    return int.from_bytes(raw, "big", signed=True)


@dataclass(frozen=True)
class CrawlJob:
    id: int
    supplier_id: int
    supplier_code: str
    adapter_name: str
    base_url: str
    trigger_type: str
    crawl_type: str
    status: str
    scheduled_for: datetime
    settings_snapshot: dict[str, Any]
    lease_expires_at: datetime | None = None

    @property
    def domain(self) -> str:
        return normalized_domain(self.base_url)


@dataclass(frozen=True)
class EnqueueResult:
    job_id: int
    status: str
    deduplicated_job_id: int | None = None

    @property
    def enqueued(self) -> bool:
        return self.status == "queued"


class PostgresJobQueue:
    def __init__(self, config: PostgresConfig, *, lease_seconds: int = 300) -> None:
        self.config = config
        self.lease_seconds = lease_seconds

    def _connect(self):
        import psycopg
        return connect_postgres(self.config, autocommit=True)

    def enqueue(self, supplier_id: int, *, trigger_type: str, crawl_type: str,
                scheduled_for: datetime | None = None, requested_by_user_id: int | None = None, pilot_limit: int | None = None) -> EnqueueResult:
        from universal_supplier.partner_pilot import enabled,limit_value,validate_config
        if pilot_limit is not None:
            if not enabled():raise PermissionError("PILOT_MODE_NOT_ENABLED")
            limit_value(pilot_limit);validate_config(self.config)
        if enabled() and pilot_limit is None:raise PermissionError('PILOT_FULL_CRAWL_DISABLED')
        scheduled_for = scheduled_for or utcnow()
        with self._connect() as connection, connection.transaction(), connection.cursor() as cursor:
            cursor.execute(
                """SELECT code, adapter_name, base_url, enabled, crawl_settings, rate_settings
                   FROM suppliers WHERE id=%s FOR UPDATE""", (supplier_id,),
            )
            supplier = cursor.fetchone()
            if supplier is None:
                raise KeyError(f"unknown supplier_id={supplier_id}")
            code, adapter_name, base_url, enabled, crawl_settings, rate_settings = supplier
            if not enabled:
                raise ValueError(f"supplier {code} is disabled")
            cursor.execute(
                """SELECT id FROM crawl_jobs
                   WHERE supplier_id=%s AND status IN ('queued','running')
                   ORDER BY id LIMIT 1""", (supplier_id,),
            )
            active = cursor.fetchone()
            snapshot = {
                "supplier_code": code, "adapter_name": adapter_name,
                "domain": normalized_domain(base_url), "crawl_type": crawl_type,
                "crawl_settings": crawl_settings or {}, "rate_settings": rate_settings or {},
            }
            if pilot_limit is not None:
                if code!='partner_st' or adapter_name!='partner_st' or trigger_type!='manual':raise ValueError('PILOT_PARTNER_MANUAL_ONLY')
                cursor.execute("SELECT sp.id,sp.external_id,coalesce(sp.canonical_url,sp.source_url) FROM source_products sp WHERE sp.supplier_id=%s AND sp.active AND sp.external_id_is_stable ORDER BY sp.id LIMIT %s",(supplier_id,pilot_limit))
                snapshot['partner_pilot']=__import__('universal_supplier.partner_pilot',fromlist=['snapshot']).snapshot(pilot_limit,cursor.fetchall())
                snapshot['rate_settings']={**(rate_settings or {}),'max_concurrency':1,'min_request_interval_seconds':2.0,'max_attempts':1,'request_timeout_seconds':20.0,'max_run_duration_seconds':300.0}
            if active:
                cursor.execute(
                    """INSERT INTO crawl_jobs
                       (supplier_id, trigger_type, crawl_type, status, scheduled_for,
                        settings_snapshot, error_message, deduplicated_job_id, finished_at, requested_by_user_id)
                       VALUES (%s,%s,%s,'skipped_already_queued',%s,%s::jsonb,
                        'SKIPPED_ALREADY_QUEUED',%s,now(),%s) RETURNING id""",
                    (supplier_id, trigger_type, crawl_type, scheduled_for,
                     json.dumps(snapshot), active[0], requested_by_user_id),
                )
                return EnqueueResult(cursor.fetchone()[0], "skipped_already_queued", active[0])
            cursor.execute(
                """INSERT INTO crawl_jobs
                       (supplier_id, trigger_type, crawl_type, status, scheduled_for, settings_snapshot, requested_by_user_id)
                   VALUES (%s,%s,%s,'queued',%s,%s::jsonb,%s) RETURNING id""",
                (supplier_id, trigger_type, crawl_type, scheduled_for, json.dumps(snapshot), requested_by_user_id),
            )
            return EnqueueResult(cursor.fetchone()[0], "queued")

    def claim_next(self) -> CrawlJob | None:
        with self._connect() as connection, connection.transaction(), connection.cursor() as cursor:
            cursor.execute(
                """SELECT j.id,j.supplier_id,s.code,s.adapter_name,s.base_url,j.trigger_type,
                          j.crawl_type,j.status,j.scheduled_for,j.settings_snapshot
                   FROM crawl_jobs j JOIN suppliers s ON s.id=j.supplier_id
                   WHERE j.status='queued' AND j.scheduled_for<=now() AND s.enabled
                   ORDER BY j.scheduled_for,j.requested_at,j.id
                   FOR UPDATE OF j SKIP LOCKED LIMIT 1"""
            )
            row = cursor.fetchone()
            if row is None:
                return None
            from universal_supplier.partner_pilot import enabled,validate_snapshot
            if enabled():
                if row[2]!='partner_st' or row[3]!='partner_st' or not row[9].get('partner_pilot'):raise PermissionError('PILOT_WORKER_OTHER_JOB')
                batch=validate_snapshot(row[9]['partner_pilot'])
                for item in batch:
                    cursor.execute('SELECT sp.external_id,coalesce(sp.canonical_url,sp.source_url),s.code FROM source_products sp JOIN suppliers s ON s.id=sp.supplier_id WHERE sp.id=%s',(item['source_product_id'],))
                    if cursor.fetchone()!=(item['external_id'],item['url'],'partner_st'):raise PermissionError('PILOT_REGISTERED_IDENTITY_CHANGED')
            lease = utcnow() + timedelta(seconds=self.lease_seconds)
            cursor.execute(
                """UPDATE crawl_jobs SET status='running',started_at=now(),heartbeat_at=now(),
                          lease_expires_at=%s,updated_at=now() WHERE id=%s""", (lease, row[0]),
            )
            return CrawlJob(*row[:7], "running", row[8], row[9], lease)

    def heartbeat(self, job_id: int) -> bool:
        lease = utcnow() + timedelta(seconds=self.lease_seconds)
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """UPDATE crawl_jobs SET heartbeat_at=now(),lease_expires_at=%s,updated_at=now()
                   WHERE id=%s AND status='running'""", (lease, job_id),
            )
            return cursor.rowcount == 1

    def complete(self, job_id: int, status: str, *, crawl_run_id: int | None = None,
                 error_message: str | None = None) -> None:
        if status not in FINAL_STATUSES:
            raise ValueError(f"not a final status: {status}")
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """UPDATE crawl_jobs SET status=%s,finished_at=now(),lease_expires_at=NULL,
                          crawl_run_id=coalesce(%s,crawl_run_id),error_message=%s,updated_at=now()
                   WHERE id=%s AND status='running'""",
                (status, crawl_run_id, error_message, job_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeError(f"job {job_id} is not running")

    def recover_stale(self) -> int:
        """Fail uncertain jobs; never rerun them blindly after a crash."""
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """UPDATE crawl_jobs SET status='stale_failed',finished_at=now(),
                          error_message='ORCHESTRATOR_LEASE_EXPIRED_UNCERTAIN_RESULT',updated_at=now()
                   WHERE status='running' AND lease_expires_at<now()"""
            )
            return cursor.rowcount


class PostgresDomainLease:
    """Session-scoped advisory lock keyed by normalized remote domain."""

    def __init__(self, config: PostgresConfig, domain: str) -> None:
        self.config = config
        self.domain = normalized_domain(f"https://{domain}")
        self.connection = None
        self.acquired = False

    def __enter__(self):
        import psycopg
        self.connection = connect_postgres(self.config, autocommit=True)
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(%s)", (advisory_lock_key(self.domain),))
            self.acquired = bool(cursor.fetchone()[0])
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.connection is not None:
            if self.acquired:
                with self.connection.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_unlock(%s)", (advisory_lock_key(self.domain),))
            self.connection.close()


class InMemoryJobQueue:
    """Deterministic QA double with the same dedup semantics."""

    def __init__(self) -> None:
        self.jobs: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def enqueue(self, supplier_id: int, *, trigger_type: str, crawl_type: str,
                scheduled_for: datetime | None = None, settings_snapshot: dict | None = None,
                requested_by_user_id: int | None = None) -> EnqueueResult:
        with self._lock:
            active = next((job for job in self.jobs if job["supplier_id"] == supplier_id and job["status"] in ACTIVE_STATUSES), None)
            job_id = len(self.jobs) + 1
            if active:
                self.jobs.append({"id": job_id, "supplier_id": supplier_id, "status": "skipped_already_queued", "deduplicated_job_id": active["id"], "requested_by_user_id": requested_by_user_id})
                return EnqueueResult(job_id, "skipped_already_queued", active["id"])
            self.jobs.append({"id": job_id, "supplier_id": supplier_id, "status": "queued", "trigger_type": trigger_type, "crawl_type": crawl_type, "settings_snapshot": deepcopy(settings_snapshot or {}), "requested_by_user_id": requested_by_user_id})
            return EnqueueResult(job_id, "queued")
