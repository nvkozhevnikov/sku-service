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

    def preview_source_batch(self):
        from .source_batch import preview
        return preview(self)

    def enqueue_source_batch(self, **approval):
        from .source_batch import enqueue
        return enqueue(self, **approval)

    def source_batch_status(self, batch_id):
        from .source_batch import status
        return status(self, batch_id)

    def stop_source_batch(self, batch_id):
        """Cancel waiting intent and request existing checkpoint STOP for running work."""
        with self._connect() as connection, connection.transaction(), connection.cursor() as cursor:
            from .source_canary import validate_config, validate_connection
            validate_config(self.config)
            validate_connection(connection)
            cursor.execute('SELECT pg_advisory_xact_lock(%s)', (8611011,))
            cursor.execute("""UPDATE crawl_jobs SET status='cancelled',finished_at=now(),
                error_message='OPERATOR_BATCH_STOP',updated_at=now()
                WHERE settings_snapshot->'source_batch'->>'id'=%s AND status='queued'""", (batch_id,))
            cursor.execute("""SELECT supplier_id,id FROM crawl_jobs
                WHERE settings_snapshot->'source_batch'->>'id'=%s AND status='running'""", (batch_id,))
            running = cursor.fetchall()
            cursor.execute("""UPDATE crawl_jobs SET error_message='OPERATOR_BATCH_STOP_PENDING'
                WHERE settings_snapshot->'source_batch'->>'id'=%s AND status='running'""", (batch_id,))
            for supplier_id, job_id in running:
                self.stop_source(supplier_id, job_id)

    def _canary_preview(self,connection,cursor,supplier_id,supplier,limit,mode='REFRESH'):
        from .source_canary import limit_value,validate_config,validate_connection,build_preview
        limit_value(limit);validate_config(self.config);validate_connection(connection)
        cursor.execute('''WITH known AS (
            SELECT DISTINCT ON (coalesce(canonical_url,source_url)) id,external_id,coalesce(canonical_url,source_url) AS url
            FROM source_products WHERE supplier_id=%s AND active AND external_id_is_stable
            ORDER BY coalesce(canonical_url,source_url),id)
            SELECT id,external_id,url,count(*) OVER() FROM known ORDER BY id LIMIT %s''',(supplier_id,limit))
        return build_preview(supplier_id,supplier,cursor.fetchall(),self.config,limit,mode)

    def preview_source_canary(self,supplier_id,limit,mode='REFRESH'):
        from .source_canary import limit_value,validate_config
        limit_value(limit);validate_config(self.config)
        with self._connect() as connection:
            connection.read_only=True
            with connection.transaction(),connection.cursor() as cursor:
                cursor.execute('SELECT code,adapter_name,base_url,enabled,crawl_settings,rate_settings FROM suppliers WHERE id=%s',(supplier_id,))
                supplier=cursor.fetchone()
                if supplier is None:raise ValueError('Поставщик не найден')
                return self._canary_preview(connection,cursor,supplier_id,supplier,limit,mode)

    def stop_source(self,supplier_id,job_id):
        from .source_runtime import runtime_enabled,job_dir
        from .source_jobs import RUNTIME_SOURCES as SOURCES
        if not runtime_enabled():raise PermissionError('SOURCE_RUNTIME_NOT_ENABLED')
        with self._connect() as connection,connection.cursor() as cursor:
            cursor.execute('''SELECT s.code,j.status,j.settings_snapshot FROM crawl_jobs j
                JOIN suppliers s ON s.id=j.supplier_id WHERE j.id=%s AND j.supplier_id=%s''',(job_id,supplier_id))
            row=cursor.fetchone()
        if not row or row[0] not in SOURCES or row[1]!='running' or not (row[2].get('crawl_settings') or {}).get('collection_mode'):raise ValueError('NO_ACTIVE_SOURCE_RUNTIME_JOB')
        path=job_dir(row[0],job_id)/'STOP_REQUESTED'
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open('x',encoding='utf-8') as f:f.write('OPERATOR_STOP')
        except FileExistsError:pass

    def source_resume_status(self,job_id):
        from .source_controls import verify_resume
        from .source_runtime import runtime_enabled,read_input
        if not runtime_enabled():raise PermissionError('SOURCE_RUNTIME_NOT_ENABLED')
        with self._connect() as c,c.cursor() as q:
            q.execute('''SELECT s.code,j.status,r.metadata FROM crawl_jobs j
                JOIN suppliers s ON s.id=j.supplier_id JOIN crawl_runs r ON r.id=j.crawl_run_id WHERE j.id=%s''',(job_id,))
            row=q.fetchone()
        if not row or row[1] not in ('partial','failed','cancelled','stale_failed'):raise ValueError('RESUME_PARENT_NOT_TERMINAL')
        rt=(row[2] or {}).get('source_runtime') or {}
        owner=rt['owner_job_id'];sha=rt['input_sha256']
        original=read_input(row[0],owner,sha)
        verify_resume(row[0],owner,sha,original,self.config)
        return True

    def enqueue(self, supplier_id: int, *, trigger_type: str, crawl_type: str,
                scheduled_for: datetime | None = None, requested_by_user_id: int | None = None, pilot_limit: int | None = None, pilot_profile: str = 'conservative', source_mode: str | None = None, resume_job_id: int | None = None, source_limit: int | None = None, source_preview_sha256: str | None = None) -> EnqueueResult:
        if source_limit is not None:
            from .source_canary import limit_value,validate_config
            limit_value(source_limit);validate_config(self.config)
            if source_mode not in ('REFRESH','DISCOVERY') or trigger_type!='manual' or crawl_type!='full' or pilot_limit is not None or resume_job_id is not None:
                raise ValueError('CANARY_MANUAL_REFRESH_ONLY')
            if not isinstance(source_preview_sha256,str) or len(source_preview_sha256)!=64:raise ValueError('Сначала просмотрите и подтвердите выбранную партию canary')
        elif source_preview_sha256 is not None:raise ValueError('CANARY_LIMIT_REQUIRED')
        from universal_supplier.partner_pilot import enabled,limit_value,validate_config
        if pilot_limit is not None:
            if not enabled():raise PermissionError("PILOT_MODE_NOT_ENABLED")
            limit_value(pilot_limit);validate_config(self.config)
            from universal_supplier.partner_pilot import rate_profile
            rate_profile(pilot_profile)
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
                raise ValueError(f"Поставщик {code} выключен (disabled); запуск заблокирован. Требуется отдельное подтверждение временного включения.")
            from .source_jobs import RUNTIME_SOURCES
            if code in RUNTIME_SOURCES and pilot_limit is None and source_limit is None and source_mode!='RESUME':
                raise PermissionError('EXPLICIT_COLLECTION_SCOPE_PREVIEW_REQUIRED')
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
            if source_limit is not None:
                preview=self._canary_preview(connection,cursor,supplier_id,supplier,source_limit,source_mode)
                if preview['confirmation_sha256']!=source_preview_sha256:
                    raise ValueError('Партия, количество, политика или target изменились; заново откройте preview canary')
                snapshot=preview['snapshot']
            elif source_mode is not None:
                if trigger_type!='manual' or pilot_limit is not None:raise ValueError('SOURCE_MANUAL_ONLY')
                from .source_controls import prepare_snapshot
                known=[];parent=None
                if source_mode=='REFRESH':
                    cursor.execute("SELECT external_id,coalesce(canonical_url,source_url) FROM source_products WHERE supplier_id=%s AND active AND external_id_is_stable ORDER BY id",(supplier_id,))
                    known=cursor.fetchall()
                elif source_mode=='RESUME':
                    if type(resume_job_id) is not int or resume_job_id<1:raise ValueError('RESUME_PARENT_REQUIRED')
                    cursor.execute('''SELECT j.status,j.supplier_id,j.settings_snapshot,r.metadata
                        FROM crawl_jobs j JOIN crawl_runs r ON r.id=j.crawl_run_id WHERE j.id=%s FOR SHARE OF j''',(resume_job_id,))
                    previous=cursor.fetchone()
                    if not previous or previous[1]!=supplier_id:raise ValueError('RESUME_PARENT_NAMESPACE')
                    if previous[2].get('source_batch'):
                        rt = (previous[3] or {}).get('source_runtime') or {}
                        from .source_batch import resume_is_safe
                        if not resume_is_safe(rt):
                            raise PermissionError('BATCH_RESUME_REQUIRES_OPERATOR_DECISION')
                        snapshot = deepcopy(previous[2])
                    parent={'status':previous[0],'supplier_code':code,'settings':previous[2],'result':(previous[3] or {}).get('source_runtime',{})}
                snapshot=prepare_snapshot(snapshot,source_mode,known_rows=known,resume=parent,config=self.config)
                if source_mode=='RESUME' and previous[2].get('source_batch'):
                    from .source_batch import resume_envelope
                    snapshot = resume_envelope(cursor, previous[2], snapshot)
            if pilot_limit is not None:
                if code!='partner_st' or adapter_name!='partner_st' or trigger_type!='manual':raise ValueError('PILOT_PARTNER_MANUAL_ONLY')
                cursor.execute("SELECT sp.id,sp.external_id,coalesce(sp.canonical_url,sp.source_url) FROM source_products sp WHERE sp.supplier_id=%s AND sp.active AND sp.external_id_is_stable ORDER BY sp.id LIMIT %s",(supplier_id,pilot_limit))
                snapshot['partner_pilot']=__import__('universal_supplier.partner_pilot',fromlist=['snapshot']).snapshot(pilot_limit,cursor.fetchall(),pilot_profile)
                snapshot['rate_settings']={**(rate_settings or {}),**rate_profile(pilot_profile)}
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
            # Serialize claims across worker processes, not only each thread pool.
            # Existing host session lease remains the final network guard.
            cursor.execute('SELECT pg_advisory_xact_lock(%s)', (8611011,))
            cursor.execute("SELECT count(*) FROM crawl_jobs WHERE status='running'")
            if cursor.fetchone()[0] >= 2:
                return None
            cursor.execute("""UPDATE crawl_jobs SET status='cancelled',finished_at=now(),
                error_message='BATCH_WINDOW_EXPIRED',updated_at=now()
                WHERE status='queued' AND settings_snapshot ? 'source_batch'
                  AND (settings_snapshot->'source_batch'->>'deadline')::timestamptz<=now()""")
            cursor.execute(
                """SELECT j.id,j.supplier_id,s.code,s.adapter_name,s.base_url,j.trigger_type,
                          j.crawl_type,j.status,j.scheduled_for,j.settings_snapshot
                   FROM crawl_jobs j JOIN suppliers s ON s.id=j.supplier_id
                   WHERE j.status='queued' AND j.scheduled_for<=now() AND s.enabled
                     AND NOT EXISTS (SELECT 1 FROM crawl_jobs running JOIN suppliers rs ON rs.id=running.supplier_id
                         WHERE running.status='running' AND
                         coalesce(running.settings_snapshot->>'domain',regexp_replace(lower(split_part(split_part(rs.base_url,'://',2),'/',1)),'^www[.]', ''))=
                         coalesce(j.settings_snapshot->>'domain',regexp_replace(lower(split_part(split_part(s.base_url,'://',2),'/',1)),'^www[.]', '')))
                     AND (NOT (j.settings_snapshot ? 'source_batch') OR s.code='beka_mak' OR EXISTS (
                         SELECT 1 FROM crawl_jobs gate JOIN suppliers gs ON gs.id=gate.supplier_id
                         WHERE gs.code='beka_mak' AND gate.status='succeeded'
                           AND gate.settings_snapshot->'source_batch'->>'id'=j.settings_snapshot->'source_batch'->>'id'
                           AND gate.settings_snapshot->'source_batch'->>'phase'='DISCOVERY'
                           AND EXISTS (SELECT 1 FROM audit_events a WHERE a.action='SOURCE_BATCH_BEKA_ISOLATION_CONFIRMED'
                               AND a.entity_type='source_batch' AND a.entity_id=j.settings_snapshot->'source_batch'->>'id')))
                   ORDER BY j.scheduled_for,j.requested_at,j.id
                   FOR UPDATE OF j SKIP LOCKED LIMIT 1"""
            )
            row = cursor.fetchone()
            if row is None:
                return None
            if row[9].get('source_batch'):
                from .source_batch import validate_snapshot
                try:
                    validate_snapshot(row[9])
                except (ValueError, PermissionError) as error:
                    cursor.execute("UPDATE crawl_jobs SET status='failed',finished_at=now(),error_message=%s WHERE id=%s",
                                   (str(error), row[0]))
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
        with self._connect() as connection, connection.transaction(), connection.cursor() as cursor:
            cursor.execute('SELECT pg_advisory_xact_lock(%s)', (8611011,))
            cursor.execute(
                """UPDATE crawl_jobs SET status=%s,finished_at=now(),lease_expires_at=NULL,
                          crawl_run_id=coalesce(%s,crawl_run_id),error_message=CASE
                          WHEN error_message='OPERATOR_BATCH_STOP_PENDING' THEN 'OPERATOR_BATCH_STOP' ELSE %s END,updated_at=now()
                   WHERE id=%s AND status='running' RETURNING supplier_id,settings_snapshot,requested_by_user_id,error_message""",
                (status, crawl_run_id, error_message, job_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeError(f"job {job_id} is not running")
            supplier_id, snapshot, user_id, saved_error = cursor.fetchone()
            if status == 'succeeded' and snapshot.get('source_batch') and saved_error != 'OPERATOR_BATCH_STOP':
                from .source_batch import advance
                advance(cursor, supplier_id, snapshot, user_id)

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
