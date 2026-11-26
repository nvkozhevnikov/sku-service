"""Permanent main UI and independent native competitor runner.

Legacy frozen-dispatch helpers remain explicit; the serving path uses only
rc_verify_vi_20261019 through separate supplier/competitor runtime credentials.
"""
from pathlib import Path
import json,os,sys,secrets
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
os.environ['STERBRUST_UNIFIED_RC']='YES'
os.environ['CONTROL_PLANE_SKIP_DEFAULT_APP']='YES'
# Legacy frozen-dispatch helpers are explicit; importing the permanent UI must
# not import the Windows-only database preparation script on a Linux server.
TARGET='sterbrust_unified_rc_20261019'
PORT=55461
OUT=ROOT/'reports/integration'
def connect(*args,**kwargs):
    from rc_prepare_database import connect as legacy_connect
    return legacy_connect(*args,**kwargs)
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
    """Main supplier app plus independent native competitor service/collector."""
    from competitor_intel.main_runtime import configure, services, session_secret
    configure()
    os.environ['SESSION_SIGNING_SECRET']=session_secret()
    from supplier_panel import build_app as supplier_app
    monitoring, collection = services()
    return supplier_app(competitor_service=monitoring,competitor_collection=collection)

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--stop-competitors',action='store_true');parser.add_argument('--competitor-preflight',action='store_true');parser.add_argument('--collect-competitors',action='store_true');parser.add_argument('--scheduled-competitors',action='store_true');parser.add_argument('--source',choices=sorted(__import__('competitor_intel.execution',fromlist=['SOURCES']).SOURCES));parser.add_argument('--max-pages',type=int);parser.add_argument('--work-one',action='store_true');parser.add_argument('--pilot-work-one',action='store_true');args=parser.parse_args()
    if sum((args.stop_competitors,args.competitor_preflight,args.collect_competitors,args.scheduled_competitors))>1:parser.error('Choose one competitor action')
    if args.stop_competitors:
        from competitor_intel.main_runtime import configure,services
        configure();_,collection=services();print(json.dumps(collection.request_stop()))
    elif args.competitor_preflight:
        from competitor_intel.main_runtime import configure,services
        configure();_,collection=services();repo=collection.repository()
        try:
            identity=repo.store.query("SELECT current_database() database,current_user role,inet_server_port() port,current_setting('search_path') search_path")[0]
            print(json.dumps({'identity':identity,'enabled_sources':sorted(repo.enabled_sources()),'HTTP':0,'collection_started':False}))
        finally:repo.close()
    elif args.collect_competitors or args.scheduled_competitors:
        from competitor_intel.main_runtime import configure, services
        configure();_, collection=services()
        try:
            result=collection.run('SCHEDULED' if args.scheduled_competitors else 'CLI_MANUAL',[args.source] if args.source else None,args.max_pages)
        except (ValueError,KeyError,FileNotFoundError,json.JSONDecodeError):
            result={'status':'CONFIG_ERROR','exit_code':4,'reason':'EXECUTION_CONFIGURATION_UNAVAILABLE'}
        except Exception:
            result={'status':'FAILED','exit_code':2,'reason':'EXECUTION_UNAVAILABLE'}
        print(json.dumps(result,ensure_ascii=False,default=str));raise SystemExit(result['exit_code'])
    elif args.pilot_work_one:
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
