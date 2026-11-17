"""Run exactly one authenticated UI job through the existing Orchestrator."""
import argparse
import json
import os
import time
from .manual_collection import verify, require
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
    require(row and row[1]=='queued','Задание уже обработано или отсутствует')
    proof=validate(row[0])
    os.environ.update(STERBRUST_UNIFIED_RC='YES',STERBRUST_SOURCE_RUNTIME_ENABLED='YES',
        STERBRUST_ALLOW_SUPPLIER_HTTP='YES',STERBRUST_ALLOW_SOURCE_WRITES='YES',
        STERBRUST_RC_MATCHING_APPLY_APPROVED='NO',SCHEDULER_ENABLED='NO',
        STERBRUST_APPROVED_HOST_POLICIES=json.dumps(proof['policies']))
    job=None
    for _ in range(30):
        job=queue.claim_next(job_id=job_id,manual_validator=validate)
        if job:break
        time.sleep(1)
    if not job:
        queue.complete(job_id,'failed',error_message='MANUAL_WORKER_BUSY: исполнитель занят; повторите запуск позже')
        return
    worker=Orchestrator(cfg,queue=queue,max_parallel_supplier_jobs=1)
    try:
        worker._run_job(job)
        validate(job.settings_snapshot)
    finally:worker.pool.shutdown(wait=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--job-id',type=int,required=True);args=p.parse_args()
    try:run(args.job_id)
    except Exception:
        # Never print credentials or a raw database exception. Complete only our
        # own queued/running manual job; native terminal outcomes are immutable.
        try:
            cfg=runtime_config()
            with guarded_connect(cfg,autocommit=True) as c:
                c.execute("UPDATE crawl_jobs SET status='failed',finished_at=now(),updated_at=now(),error_message='MANUAL_WORKER_REFUSED: запуск не прошёл проверку' WHERE id=%s AND status IN ('queued','running') AND settings_snapshot ? 'manual_collection'",(args.job_id,))
        finally:raise SystemExit(1)
