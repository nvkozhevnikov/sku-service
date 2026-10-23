"""One explicit operator session around the EXISTING Orchestrator and queue.

Only called by source_batch recover --run --activate-approved-settings after
exact SHA confirmation. Not a scheduler, service, implicit activation or cron.
"""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time
from uuid import uuid4

from psycopg.types.json import Jsonb
from universal_supplier.control_plane import source_batch as batch, source_recovery as recovery
from universal_supplier.control_plane.admin_store import PostgresAdminStore
from universal_supplier.control_plane.orchestrator import Orchestrator


def activated_settings(before, matrix):
    require=recovery.require
    by_code={r['SOURCE']:r for r in matrix}
    require(len(before)==11 and {r[1] for r in before}==set(by_code),'RECOVERY_SETTINGS_SOURCE_SET')
    after=deepcopy(before)
    for row in after:
        approved=by_code[row[1]]
        row[2]=True
        row[3]=json.loads(approved['RATE_POLICY_JSON'])
        row[4]={**(row[4] or {}),'host_policy_approval':{'host':approved['APPROVAL_HOST'],'reference':approved['APPROVAL_REFERENCE']}}
    return after


def save(folder,name,value):
    (folder/name).write_text(json.dumps(value,ensure_ascii=False,indent=2,default=str),encoding='utf-8')


@contextmanager
def settings_session(queue, manifest, folder):
    """Exact before/apply/readback/restore; no overwrite on unexpected drift."""
    expected=manifest['settings_before']
    active=activated_settings(expected,manifest['activation_matrix'])
    with queue._connect() as connection,connection.transaction(),connection.cursor() as q:
        batch.validate_config(queue.config);batch.validate_connection(connection)
        q.execute('SELECT current_user,rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user')
        recovery.require(q.fetchone()==('rc_runtime',False,False),'RECOVERY_ROLE')
        q.execute('SELECT id,code,enabled,rate_settings,crawl_settings FROM suppliers ORDER BY code FOR UPDATE')
        recovery.require(recovery.normal(q.fetchall())==expected,'RECOVERY_SETTINGS_BASELINE_DRIFT')
        q.execute("SELECT id FROM crawl_jobs WHERE status='running' OR (status='queued' AND coalesce(settings_snapshot->'source_batch'->>'id','')<>%s)",(manifest['batch_id'],))
        recovery.require(not q.fetchall(),'RECOVERY_FOREIGN_OR_RUNNING_JOB')
        for old,new in zip(expected,active):
            q.execute('UPDATE suppliers SET enabled=%s,rate_settings=%s,crawl_settings=%s,updated_at=now() WHERE id=%s AND code=%s',
                (new[2],Jsonb(new[3]),Jsonb(new[4]),new[0],new[1]))
            recovery.require(q.rowcount==1,'RECOVERY_SETTINGS_ROW_CHANGED')
            PostgresAdminStore._audit(q,None,'LIVE11_RECOVERY_SETUP','supplier',new[0],{'settings':old},{'settings':new},
                'Explicit exact recovery SHA operator session',manifest['batch_id'])
        q.execute('SELECT id,code,enabled,rate_settings,crawl_settings FROM suppliers ORDER BY code')
        recovery.require(recovery.normal(q.fetchall())==active,'RECOVERY_SETTINGS_APPLY_READBACK')
    save(folder,'SETTINGS_APPLIED.json',{'before':expected,'active':active})
    try:yield
    finally:
        with queue._connect() as connection,connection.transaction(),connection.cursor() as q:
            q.execute("SELECT id FROM crawl_jobs WHERE status='running'")
            recovery.require(not q.fetchall(),'RECOVERY_RESTORE_RUNNING_JOB_STOP')
            q.execute('SELECT id,code,enabled,rate_settings,crawl_settings FROM suppliers ORDER BY code FOR UPDATE')
            recovery.require(recovery.normal(q.fetchall())==active,'RECOVERY_SETTINGS_DRIFT_DO_NOT_OVERWRITE')
            for old in expected:
                q.execute('UPDATE suppliers SET enabled=%s,rate_settings=%s,crawl_settings=%s,updated_at=now() WHERE id=%s AND code=%s',
                    (old[2],Jsonb(old[3]),Jsonb(old[4]),old[0],old[1]))
                PostgresAdminStore._audit(q,None,'LIVE11_RECOVERY_SETUP_RESTORED','supplier',old[0],None,{'settings':old},
                    'Restore exact temporary session settings',manifest['batch_id'])
            q.execute('SELECT id,code,enabled,rate_settings,crawl_settings FROM suppliers ORDER BY code')
            restored=recovery.normal(q.fetchall())
            recovery.require(restored==expected,'RECOVERY_RESTORE_READBACK_FAILED')
        save(folder,'SETTINGS_RESTORED.json',{'select_equal':True,'settings':restored})


def run(queue):
    manifest,sha=recovery.approval()
    recovery.require(manifest['activation_matrix']==batch.matrix_rows(),'RECOVERY_ACTIVATION_MATRIX_CHANGED')
    folder=batch.ROOT/'reports/recovery-sessions'/str(uuid4());folder.mkdir(parents=True)
    save(folder,'SESSION.json',{'batch_id':manifest['batch_id'],'approval_sha256':sha,
        'head':manifest['compatibility']['head'],'started':datetime.now(timezone.utc).isoformat()})
    values={key:'YES' for key in ('STERBRUST_SOURCE_RUNTIME_ENABLED','STERBRUST_ALLOW_SUPPLIER_HTTP','STERBRUST_ALLOW_SOURCE_WRITES','STERBRUST_UNIFIED_RC','STERBRUST_RC_MATCHING_APPLY_APPROVED')}
    values.update(STERBRUST_CABINET_LIVE11_WRITE_CONFIRM=queue.config.dbname,
        STERBRUST_APPROVED_HOST_POLICIES=json.dumps({r['APPROVAL_HOST']:r['APPROVAL_DIGEST_SHA256'] for r in manifest['activation_matrix']}),
        SOURCE_RUNNER_EVIDENCE_ROOT=str(batch.ROOT/'reports/source-runner-evidence'))
    previous={key:os.environ.get(key) for key in values}
    try:
        os.environ.update(values)
        return execute(queue,manifest,folder)
    finally:
        for key,value in previous.items():
            if value is None:os.environ.pop(key,None)
            else:os.environ[key]=value


def execute(queue,manifest,folder):
    with settings_session(queue,manifest,folder):
        worker=None;reason=None
        try:
            admitted=recovery.recover(queue);save(folder,'RECOVERED.json',admitted)
            worker=Orchestrator(queue.config,queue=queue,max_parallel_supplier_jobs=2)
            while True:
                recovery.approval()  # Recheck exact HEAD/engine/deadline/artifacts.
                with queue._connect() as connection,connection.cursor() as q:
                    connection.read_only=True
                    q.execute("SELECT id FROM crawl_jobs WHERE status IN ('queued','running') AND coalesce(settings_snapshot->'source_batch'->>'id','')<>%s",(manifest['batch_id'],))
                    recovery.require(not q.fetchall(),'RECOVERY_FOREIGN_ACTIVE_JOB')
                worker.tick()
                status=queue.source_batch_status(manifest['batch_id'])
                save(folder,'STATUS.json',status)
                if status['complete']:reason='COMPLETED';break
                phases=status['phases']
                if manifest.get('continuation'):
                    # Proven time-budget boundaries reuse the SAME queue;
                    # semantic URL quarantine does not cancel unrelated hosts.
                    pending=[p for p in phases if p.get('job_id') is not None and
                        p.get('state') in ('ошибка','остановлено','требуется решение оператора')]
                    if pending:
                        recovery.recover_continuation(queue,manifest,recovery.file_sha(os.environ[recovery.ENV+'_FILE']))
                        continue
                    if len(phases)==22 and all(p.get('execution_finished') for p in phases):
                        reason='COMPLETED_WITH_QUARANTINE' if any(p.get('quarantined_count') for p in phases) else 'COMPLETED'
                        break
                if any(p.get('job_id') is not None and p.get('state') in ('ошибка','остановлено','требуется решение оператора') for p in phases):
                    # report projects latest attempts; old evidence is retained.
                    raise RuntimeError('RECOVERY_PHASE_REQUIRES_OPERATOR_DECISION')
                beka=[p for p in phases if p['source']=='beka_mak']
                if len(beka)==2 and all(p['state']=='завершено' for p in beka) and not status['beka_gate_released']:
                    reason='BEKA_ISOLATION_CONFIRMATION_REQUIRED';break
                if len(phases)==22 and all(p.get('job_id') is not None and p['state']=='завершено' for p in phases):
                    raise RuntimeError('RECOVERY_INCOMPLETE_DISCOVERY_REQUIRES_OPERATOR')
                time.sleep(2)
        except BaseException as error:
            save(folder,'STOP.json',{'type':type(error).__name__,'reason':str(error)})
            queue.stop_source_batch(manifest['batch_id'])
            raise
        finally:
            if worker:worker.pool.shutdown(wait=True)
    result={'batch_id':manifest['batch_id'],'reason':reason,'evidence':str(folder),'settings_restored':True}
    save(folder,'RESULT.json',result)
    return result
