"""Run exactly one authenticated UI job through the existing Orchestrator."""
import argparse
from datetime import datetime, timezone
import json
import os
import time
from .manual_collection import MANUAL_EXECUTION, verify, require
from .runtime_config import runtime_config, guarded_connect
from .queue import PostgresJobQueue


def run(job_id):
    from competitor_intel.main_runtime import session_secret
    from .admin_store import PostgresAdminStore
    from .access_policy import allowed
    from .orchestrator import Orchestrator
    key=os.environ.get('SESSION_SIGNING_SECRET') or session_secret()
    cfg=runtime_config();queue=PostgresJobQueue(cfg,connection_factory=guarded_connect)
    def validate(snapshot):
        proof=verify(snapshot,key)
        user=PostgresAdminStore(cfg,connection_factory=guarded_connect).user_by_id(proof['actor_id'])
        require(user and user.is_active and not user.must_change_password and
            user.role==proof['actor_role'] and user.session_version==proof['actor_sv'] and
            allowed(user,'supplier.manage'),'Доступ пользователя отозван')
        with guarded_connect(cfg,autocommit=True) as c:
            copies=c.execute("SELECT count(*) FROM crawl_jobs WHERE settings_snapshot->'manual_collection'->>'nonce'=%s AND status<>'skipped_already_queued'",(proof['nonce'],)).fetchone()[0]
            require(copies==1,'Повтор подтверждения запуска запрещён')
            actual=c.execute('SELECT id,catalog_product_id FROM source_products WHERE id=ANY(%s) ORDER BY id',
                ([r[0] for r in proof['mappings']],)).fetchall()
        require([list(r) for r in actual]==proof['mappings'],'Существующие связи изменились')
        return proof
    with guarded_connect(cfg,autocommit=True) as c:
        row=c.execute("SELECT settings_snapshot,status FROM crawl_jobs WHERE id=%s",(job_id,)).fetchone()
    if not row or row[1] != 'queued':
        return
    proof=validate(row[0])
    os.environ.update(STERBRUST_UNIFIED_RC='YES',STERBRUST_SOURCE_RUNTIME_ENABLED='YES',
        STERBRUST_ALLOW_SUPPLIER_HTTP='YES',STERBRUST_ALLOW_SOURCE_WRITES='YES',
        STERBRUST_RC_MATCHING_APPLY_APPROVED='NO',SCHEDULER_ENABLED='NO',
        STERBRUST_APPROVED_HOST_POLICIES=json.dumps(proof['policies']))
    job=None
    deadline = datetime.fromisoformat(proof['expires_at'])
    while datetime.now(timezone.utc) < deadline:
        job=queue.claim_next(job_id=job_id,manual_validator=validate,
                             manual_max_parallel=proof.get('max_parallel_suppliers'))
        if job:break
        with guarded_connect(cfg,autocommit=True) as c:
            status = c.execute('SELECT status FROM crawl_jobs WHERE id=%s', (job_id,)).fetchone()
        if status != ('queued',):
            return
        time.sleep(3)
    if not job:
        queue.fail_waiting_manual_job(job_id, 'MANUAL_WAITING_EXPIRED')
        return
    worker=Orchestrator(cfg,queue=queue,max_parallel_supplier_jobs=1)
    token=MANUAL_EXECUTION.set(job.id)
    try:
        worker._run_job(job)
        validate(job.settings_snapshot)
    finally:
        MANUAL_EXECUTION.reset(token)
        worker.pool.shutdown(wait=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--job-id',type=int,required=True);args=p.parse_args()
    try:run(args.job_id)
    except Exception:
        # Do not print private data or change a job claimed by another process.
        try:
            cfg=runtime_config()
            queue = PostgresJobQueue(cfg, connection_factory=guarded_connect)
            queue.fail_waiting_manual_job(args.job_id, 'MANUAL_WORKER_REFUSED')
        finally:raise SystemExit(1)
