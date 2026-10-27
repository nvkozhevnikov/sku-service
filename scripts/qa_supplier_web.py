"""Disposable QA instance of the SAME app, synthetic database only; no collectors."""
from pathlib import Path
import os,sys,secrets
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from qa_supplier_database import qa_config,DATABASE,MARKER

def make_app():
    for flag in ('STERBRUST_SOURCE_RUNTIME_ENABLED','STERBRUST_ALLOW_SUPPLIER_HTTP','STERBRUST_ALLOW_SOURCE_WRITES',
                 'STERBRUST_RC_MATCHING_APPLY_APPROVED','SCHEDULER_ENABLED'):
        os.environ[flag]='NO'
    os.environ['CONTROL_PLANE_EXPORT_ROOT']=str(ROOT/'reports/qa-xml-previews')
    # Explicit loopback QA only, without changing default secure cookie behavior.
    os.environ['CONTROL_PLANE_INSECURE_HTTP']='YES'
    os.environ['CONTROL_PLANE_QA_INSTANCE']='YES'
    import psycopg
    cfg=qa_config()
    with psycopg.connect(**cfg.kwargs(),autocommit=True,connect_timeout=5) as c:
        assert c.execute("SELECT current_database(),system_identifier::text FROM pg_control_system()").fetchone()==(DATABASE,'7698346631476390904')
        assert c.execute("SELECT shobj_description(oid,'pg_database') FROM pg_database WHERE datname=current_database()").fetchone()==(MARKER,)
    from universal_supplier.control_plane.store import PostgresControlPlaneStore
    from universal_supplier.control_plane.admin_store import PostgresAdminStore
    from universal_supplier.control_plane.queue import InMemoryJobQueue
    from universal_supplier.control_plane.web import create_app
    class NoExecution(InMemoryJobQueue):
        def enqueue(self,*a,**k):raise PermissionError('SYNTHETIC_UI_QA_EXECUTION_FORBIDDEN')
    return create_app(store=PostgresControlPlaneStore(cfg),admin_store=PostgresAdminStore(cfg),queue=NoExecution(),
        session_secret=secrets.token_urlsafe(40),auth_required=True)

if __name__=='__main__':
    import uvicorn
    uvicorn.run(make_app(),host='127.0.0.1',port=58098)
