"""Existing operator UI, one login, real frozen supplier/competitor DB.

All live collection is disabled. Only explicit frozen replay can enqueue.
"""
from pathlib import Path
import json,os,sys,secrets
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
os.environ['STERBRUST_UNIFIED_RC']='YES'
os.environ['CONTROL_PLANE_SKIP_DEFAULT_APP']='YES'
from rc_prepare_database import TARGET,PORT,OUT,connect
from universal_supplier.postgres import PostgresConfig
from universal_supplier.control_plane.store import PostgresControlPlaneStore
from universal_supplier.control_plane.admin_store import PostgresAdminStore
from universal_supplier.control_plane.security import hash_password
from universal_supplier.control_plane.commercial_qa import DisabledCommercialQaService
from universal_supplier.control_plane.unified_jobs import FrozenDispatch
from psycopg.conninfo import make_conninfo
import psycopg

def credentials():return json.loads((OUT/'private-runtime.json').read_text(encoding='utf-8'))

def config(role='app_supplier',db=None):
    from universal_supplier.partner_pilot import enabled,DATABASE
    if enabled():return PostgresConfig(*DATABASE,'rc_owner',None,'disable')
    return PostgresConfig('127.0.0.1',PORT,db or TARGET,role,credentials()[role],'disable')

class FrozenStore(PostgresControlPlaneStore):
    read_only=True
    def _connect(self):
        c=super()._connect();c.read_only=True;return c

def dispatch_connect():
    return psycopg.connect(host='127.0.0.1',port=PORT,dbname=TARGET,user='app_dispatch',password=credentials()['app_dispatch'],autocommit=True)

def initialize_local_login():
    cfg=credentials()
    admin=PostgresAdminStore(config())
    if not admin.users_exist():
        cfg['qa_login_password']=secrets.token_urlsafe(24)
        cfg['session_secret']=secrets.token_urlsafe(48)
        admin.create_user('rc-review','RC review',hash_password(cfg['qa_login_password']),'ADMIN')
        (OUT/'private-runtime.json').write_text(json.dumps(cfg),encoding='utf-8')

def build_app():
    cfg=credentials();os.environ['SESSION_SIGNING_SECRET']=cfg['session_secret']
    from universal_supplier.control_plane.web import create_app
    from competitor_intel.monitoring import MonitoringService,MonitoringRepository
    c=config('app_competitor')
    p=OUT/('private-competitor-'+TARGET+'.json')
    p.write_text(json.dumps({'dsn':make_conninfo(**c.kwargs())}),encoding='utf-8')
    from universal_supplier.partner_pilot import enabled,connect_postgres
    from universal_supplier.control_plane.queue import PostgresJobQueue
    if enabled():
        with connect_postgres(config(),autocommit=True):pass
    return create_app(store=PostgresControlPlaneStore(config()) if enabled() else FrozenStore(config()),queue=PostgresJobQueue(config()) if enabled() else None,admin_store=PostgresAdminStore(config()),
        auth_required=True,session_secret=cfg['session_secret'],
        commercial_qa_service=DisabledCommercialQaService('Сбор отключён: используются сохранённые данные.'),
        competitor_service=MonitoringService(MonitoringRepository(p)),
        release_dispatch=FrozenDispatch(dispatch_connect))

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--work-one',action='store_true');parser.add_argument('--pilot-work-one',action='store_true');args=parser.parse_args()
    if args.pilot_work_one:
        from universal_supplier.partner_pilot import enabled
        if not enabled():raise PermissionError('PILOT_MODE_REQUIRED')
        from universal_supplier.control_plane.orchestrator import Orchestrator
        from universal_supplier.control_plane.queue import PostgresJobQueue
        import time
        q=PostgresJobQueue(config());worker=Orchestrator(config(),queue=q,max_parallel_supplier_jobs=1)
        try:
            until=time.monotonic()+180
            while time.monotonic()<until:
                job=q.claim_next()
                if job:
                    worker._run_job(job);break
                time.sleep(1)
            else:raise TimeoutError('No approved pilot enqueued')
        finally:worker.pool.shutdown(wait=True)
    elif args.work_one:print(FrozenDispatch(dispatch_connect).work_one())
    else:
        import uvicorn
        uvicorn.run(build_app(),host='127.0.0.1',port=58161)
