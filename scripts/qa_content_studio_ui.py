"""Explicit isolated PostgreSQL UI rehearsal. Never reads main runtime secrets."""
import argparse,os,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
os.environ['CONTROL_PLANE_SKIP_DEFAULT_APP']='YES'

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--host',default='127.0.0.1');p.add_argument('--database',required=True)
    p.add_argument('--snapshot',type=Path,required=True);p.add_argument('--data',type=Path,required=True)
    p.add_argument('--listen',default='127.0.0.1');p.add_argument('--port',type=int,default=58162)
    a=p.parse_args()
    if os.environ.get('STUDIO_ISOLATED_PG_QA')!='YES' or not a.database.startswith('studio_integration_qa_linux_20261117'):
        raise ValueError('EXPLICIT_ISOLATED_PG_QA_REQUIRED')
    import psycopg
    from psycopg.rows import dict_row
    from content_studio.migration import read_source
    from content_studio.postgres_store import PostgresStore
    from content_studio.secret_store import ServerVault
    from universal_supplier.postgres import PostgresConfig
    from universal_supplier.control_plane.admin_store import PostgresAdminStore
    from universal_supplier.control_plane.store import UnavailableControlPlaneStore
    from universal_supplier.control_plane.web import create_app
    cfg=PostgresConfig(a.host,5432,a.database,'rc_runtime',None,'disable')
    def connect(config=cfg,**kwargs):
        assert config.dbname==a.database and config.user=='rc_runtime'
        return psycopg.connect(**config.kwargs(),**kwargs)
    data=a.data.resolve();(data/'media').mkdir(parents=True,exist_ok=True)
    for name,raw in read_source(a.snapshot)['media'].items():
        target=data/'media'/name
        if target.exists():assert target.read_bytes()==raw
        else:target.write_bytes(raw)
    store=PostgresStore(data,lambda:connect(autocommit=True,row_factory=dict_row))
    admin=PostgresAdminStore(cfg,connection_factory=connect)
    vault=ServerVault(data/'qa-vault')
    app=create_app(store=UnavailableControlPlaneStore('isolated UI rehearsal'),admin_store=admin,auth_required=True,
      session_secret='synthetic-isolated-ui-session-never-deployed',
      content_studio_options={'store':store,'ai_vault':vault,'vk_vault':vault,'external_calls':False})
    import uvicorn
    uvicorn.run(app,host=a.listen,port=a.port,access_log=False)

if __name__=='__main__':main()
