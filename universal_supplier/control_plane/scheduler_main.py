from __future__ import annotations

import threading
import os

from universal_supplier.postgres import PostgresConfig
from .heartbeat import HeartbeatRecorder
from .orchestrator import Orchestrator
from .queue import PostgresJobQueue
from .scheduler import Scheduler


DEFAULT_MAX_PARALLEL_SUPPLIER_JOBS = 2
MAX_PARALLEL_SUPPLIER_JOBS_UPPER_BOUND = 32


def run_approved(config,approval_path,sha,confirm,*,folder=None,poll_seconds=2.0,reporter=None):
    """An explicit bounded host for the same Scheduler and Orchestrator classes."""
    from pathlib import Path
    from datetime import datetime,timezone
    from uuid import uuid4
    import json,time
    from . import automation_execution as execution
    from .source_autonomy import keep_awake
    from .source_hourly import atomic_text,save_report
    from .automation_report import write
    value=execution.load(approval_path,sha,confirm,for_execution=True)
    output=Path(folder or execution.ROOT/'reports/automation-runtime'/str(uuid4()))
    session=execution.Session(config,value,sha,output,approval_path=approval_path)
    from .runtime_config import guarded_connect
    queue=PostgresJobQueue(config,connection_factory=guarded_connect);worker=None;previous=None;index=0;reason=None
    report=reporter or write
    values={'STERBRUST_SOURCE_RUNTIME_ENABLED':'YES','STERBRUST_ALLOW_SUPPLIER_HTTP':'YES',
        'STERBRUST_ALLOW_SOURCE_WRITES':'YES','STERBRUST_UNIFIED_RC':'YES',
        'STERBRUST_RC_MATCHING_APPLY_APPROVED':'YES',
        'STERBRUST_APPROVED_HOST_POLICIES':json.dumps(value['host_policies']),
        'SOURCE_RUNNER_EVIDENCE_ROOT':str(execution.ROOT/'reports/source-runner-evidence'),
        'PGOPTIONS':os.environ.get('PGOPTIONS','')+' -c statement_timeout=60000 -c lock_timeout=15000'}
    before={k:os.environ.get(k) for k in values}
    try:
        os.environ.update(values)
        with keep_awake(),session.activate():
            worker=Orchestrator(config,queue=queue,max_parallel_supplier_jobs=2)
            scheduler=Scheduler(config,queue,automation=session)
            try:
                previous=report(session,index,worker='STARTING');index+=1
                next_report=time.monotonic()+3600;next_heartbeat=0;exported=set()
                while True:
                    session.check();scheduler.tick();worker.tick()
                    if time.monotonic()>=next_heartbeat:
                        atomic_text(output/'heartbeat.json',json.dumps({'at':datetime.now(timezone.utc).isoformat(),
                            'pid':os.getpid(),'worker':'RUNNING','sha256':sha,'blocked_sources':scheduler.blocked}))
                        next_heartbeat=time.monotonic()+10
                    if time.monotonic()>=next_report:
                        previous=report(session,index,previous=previous,blocked=scheduler.blocked);index+=1
                        next_report=time.monotonic()+3600
                    # Export only after a genuine succeeded job/matching, never synthetic progress.
                    with queue._connect() as c,c.cursor() as q:
                        q.execute('''SELECT j.id,j.settings_snapshot,r.metadata FROM crawl_jobs j
                            JOIN crawl_runs r ON r.id=j.crawl_run_id WHERE j.status='succeeded'
                            AND j.settings_snapshot->'automation'->>'approval_sha256'=%s ORDER BY j.id''',(sha,))
                        complete=q.fetchall()
                    for jid,snapshot,metadata in complete:
                        if jid in exported:continue
                        cfg=snapshot['crawl_settings'].get('automation') or {}
                        phase=snapshot['automation']['phase']
                        if cfg.get('export_updates' if phase=='REFRESH' else 'export_new_products'):
                            identity=(metadata.get('source_runtime') or {}).get('identity_post_processing') or metadata.get('identity_post_processing') or {}
                            if identity.get('status')!='succeeded':continue
                            from universal_supplier.xml_export import XmlExportService
                            from .store import PostgresControlPlaneStore
                            XmlExportService(PostgresControlPlaneStore(config,connection_factory=guarded_connect)).publish_previews(execution.ROOT/'reports/xml-previews')
                        exported.add(jid)
                    time.sleep(poll_seconds)
            except BaseException as error:
                text=str(error);reason=text if text.startswith(('AUTOMATION_','SOURCE_','OPERATOR','SQL_')) else type(error).__name__
                session.fail(reason)
                atomic_text(output/'STOP.json',json.dumps({'at':datetime.now(timezone.utc).isoformat(),'reason':reason,'no_automatic_restart':True}))
            finally:
                try:queue.stop_automation(sha)
                finally:worker.pool.shutdown(wait=True)
                try:
                    final=report(session,index,previous=previous,worker='STOPPED',blocked=scheduler.blocked)
                    atomic_text(output/'FINAL.json',json.dumps({'at':final['at'],'reason':reason,
                        'active_jobs':final['active_jobs'],'original1282_exact':True,'temporary_database_settings_used':False,
                        'no_automatic_restart':True}))
                except Exception as error:
                    failed={'at':datetime.now(timezone.utc).isoformat(),'postgres':'UNVERIFIED','worker':'STOPPED',
                            'original1282_exact':'UNVERIFIED','error':type(error).__name__,'sources':[]}
                    save_report(output/'hourly',failed,index)
                    atomic_text(output/'FINAL_UNVERIFIED.json',json.dumps(failed))
                    raise
    finally:
        for key,old in before.items():
            if old is None:os.environ.pop(key,None)
            else:os.environ[key]=old
    return {'reason':reason,'folder':str(output),'new_batch_created':False,'automatic_restart':False}


def read_max_parallel_supplier_jobs() -> int:
    raw_value = os.environ.get(
        "MAX_PARALLEL_SUPPLIER_JOBS", str(DEFAULT_MAX_PARALLEL_SUPPLIER_JOBS)
    ).strip()
    try:
        value = int(raw_value)
    except ValueError as error:
        raise RuntimeError(
            "MAX_PARALLEL_SUPPLIER_JOBS must be an integer between "
            f"1 and {MAX_PARALLEL_SUPPLIER_JOBS_UPPER_BOUND}; got {raw_value!r}"
        ) from error
    if not 1 <= value <= MAX_PARALLEL_SUPPLIER_JOBS_UPPER_BOUND:
        raise RuntimeError(
            "MAX_PARALLEL_SUPPLIER_JOBS must be between "
            f"1 and {MAX_PARALLEL_SUPPLIER_JOBS_UPPER_BOUND}; got {value}"
        )
    return value


def main() -> None:
    maximum = read_max_parallel_supplier_jobs()
    config = PostgresConfig.from_env()
    queue = PostgresJobQueue(config)
    scheduler = Scheduler(config, queue)
    orchestrator = Orchestrator(
        config,
        queue=queue,
        max_parallel_supplier_jobs=maximum,
    )
    orchestrator.heartbeat = HeartbeatRecorder(config, "orchestrator", orchestrator.poll_seconds)
    scheduler.heartbeat = HeartbeatRecorder(config, "scheduler", scheduler.policy.poll_seconds)
    if os.environ.get("SCHEDULER_ENABLED", "NO").upper() != "YES":
        scheduler.heartbeat.clear()  # report not_running instead of a stale row from an earlier enabled run
    else:
        thread = threading.Thread(target=scheduler.run_forever, name="cron-scheduler", daemon=True)
        thread.start()
    orchestrator.run_forever()


if __name__ == "__main__":
    main()
