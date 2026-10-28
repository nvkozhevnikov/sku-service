"""Existing app/cabinet on58097 with exact runtime55461. No worker or migrations."""
from pathlib import Path
import argparse,json,os,sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from universal_supplier.control_plane.runtime_config import runtime_config,guarded_connect

def inspect(config=None):
    import psycopg
    from universal_supplier.control_plane.source_canary import validate_config,validate_connection
    cfg=config or runtime_config();validate_config(cfg)
    with psycopg.connect(**cfg.kwargs(),autocommit=True,connect_timeout=5,
        options='-c default_transaction_read_only=on -c statement_timeout=30000') as c:
        validate_connection(c)
        identity=c.execute('SELECT current_user,rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user').fetchone()
        if identity!=('rc_runtime',False,False):raise PermissionError('PANEL_RUNTIME_ROLE_INVALID')
        schema=c.execute("SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='login_failures' AND column_name='cleared_at')").fetchone()[0]
        users=c.execute("SELECT count(*) FROM app_users WHERE role='ADMIN' AND is_active").fetchone()[0]
        active=c.execute("SELECT id,status FROM crawl_jobs WHERE status IN ('queued','running') ORDER BY id").fetchall()
    return {'target':'127.0.0.1:55461/rc_verify_vi_20261019','system_identifier':'7698346631476390904',
        'role':'rc_runtime','schema018_ready':schema,'existing_admin_count':users,'active_jobs':active,
        'blockers':([] if schema else ['AUTH_SCHEMA018_REQUIRED_FOR_RUNTIME_DML'])+
            ([] if users else ['PANEL_EXISTING_ADMIN_REQUIRED']),
        'HTTP_supplier':0,'enqueue':0,'schema_changes':0,'worker_started':False}

def build_app(*, competitor_service=None, competitor_collection=None):
    cfg=runtime_config();state=inspect(cfg)
    if state['blockers']:raise PermissionError(';'.join(state['blockers']))
    # Web process does not own execution. Only an independently approved bounded
    # worker may enable transport, SQL ingestion and matching in its own process.
    for key in ('STERBRUST_ALLOW_SUPPLIER_HTTP','STERBRUST_ALLOW_SOURCE_WRITES','STERBRUST_RC_MATCHING_APPLY_APPROVED','SCHEDULER_ENABLED'):
        os.environ[key]='NO'
    os.environ.pop('STERBRUST_CABINET_LIVE11_WRITE_CONFIRM',None)
    from universal_supplier.control_plane.web import create_app
    from universal_supplier.control_plane.store import PostgresControlPlaneStore
    from universal_supplier.control_plane.admin_store import PostgresAdminStore
    from universal_supplier.control_plane.queue import PostgresJobQueue
    from universal_supplier.control_plane.cabinet import CabinetService
    from universal_supplier.control_plane.commercial_qa import DisabledCommercialQaService
    store=PostgresControlPlaneStore(cfg,connection_factory=guarded_connect);commercial=DisabledCommercialQaService('Legacy55449 не используется writer55461')
    return create_app(store=store,admin_store=PostgresAdminStore(cfg,connection_factory=guarded_connect),queue=PostgresJobQueue(cfg,connection_factory=guarded_connect),
        commercial_qa_service=commercial,cabinet_service=CabinetService(store,commercial),auth_required=True,competitor_service=competitor_service,competitor_collection=competitor_collection)

def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=('inspect','serve'));p.add_argument('--allow-loopback-http',action='store_true')
    args=p.parse_args()
    if args.action=='inspect':print(json.dumps(inspect()));return
    if args.allow_loopback_http:os.environ['CONTROL_PLANE_INSECURE_HTTP']='YES'
    elif os.environ.get('CONTROL_PLANE_INSECURE_HTTP')!='YES':raise PermissionError('PANEL_LOOPBACK_HTTP_OPT_IN_REQUIRED')
    import uvicorn
    uvicorn.run(build_app(),host='127.0.0.1',port=58097)

if __name__=='__main__':
    try:main()
    except Exception as error:
        text=str(error)
        print(text if text.startswith(('PANEL_','AUTH_','CANARY_','SESSION_SIGNING')) else type(error).__name__,file=sys.stderr)
        raise SystemExit(1)
