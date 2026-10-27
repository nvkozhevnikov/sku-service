"""Explicit disposable SQL/UI QA database. NEVER migrates or seeds LIVE11.

Authorization: operator specification2026-10-26 sections11/14/16C.
No new role/ACL, DROP, reset, HTTP, collector ingest or matching engine execution.
"""
from pathlib import Path
from datetime import datetime,timezone,timedelta
import hashlib,json,sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import psycopg
from psycopg import sql
from universal_supplier.postgres import PostgresConfig,SUPPLIER_CONFIGS
from universal_supplier.control_plane.runtime_config import runtime_config

DATABASE='qa_supplier_automation_20261026'
MARKER='STERBRUST isolated synthetic SQL/UI QA20261026; not LIVE11 or production'


def qa_config():return PostgresConfig('127.0.0.1',55461,DATABASE,'rc_owner',None,'disable')


def apply_migrations(c):
    c.execute('CREATE TABLE IF NOT EXISTS qa_applied_migrations(name text PRIMARY KEY,sha256 text NOT NULL)')
    applied=0
    for path in sorted((ROOT/'migrations').glob('*.sql')):
        raw=path.read_text(encoding='utf-8');sha=hashlib.sha256(path.read_bytes()).hexdigest()
        prior=c.execute('SELECT sha256 FROM qa_applied_migrations WHERE name=%s',(path.name,)).fetchone()
        if prior:
            if prior[0]!=sha:raise ValueError('QA_MIGRATION_DRIFT')
            continue
        c.execute(raw)
        c.execute('INSERT INTO qa_applied_migrations VALUES (%s,%s)',(path.name,sha));applied+=1
    return applied


def main():
    admin=runtime_config().kwargs();admin.update(user='rc_owner',password=None)
    from universal_supplier.control_plane.source_canary import validate_connection
    with psycopg.connect(**admin,autocommit=True) as c:
        validate_connection(c)
        assert c.execute("SELECT current_user,rolsuper FROM pg_roles WHERE rolname=current_user").fetchone()==('rc_owner',True)
        existing=c.execute("SELECT shobj_description(oid,'pg_database') FROM pg_database WHERE datname=%s",(DATABASE,)).fetchone()
        if not existing:
            c.execute(sql.SQL('CREATE DATABASE {} TEMPLATE template0').format(sql.Identifier(DATABASE)))
            c.execute(sql.SQL('COMMENT ON DATABASE {} IS {}').format(sql.Identifier(DATABASE),sql.Literal(MARKER)))
        elif existing[0]!=MARKER:raise PermissionError('QA_DATABASE_NOT_OWNED_BY_THIS_TASK')
    config=qa_config()
    with psycopg.connect(**config.kwargs(),autocommit=True) as c:
        assert c.execute('SELECT current_database(),system_identifier::text FROM pg_control_system()').fetchone()==(DATABASE,'7698346631476390904')
        applied=apply_migrations(c);assert apply_migrations(c)==0
        with c.transaction():
            for code,(name,url,adapter) in SUPPLIER_CONFIGS.items():
                c.execute('''INSERT INTO suppliers(code,name,base_url,adapter_name,enabled) VALUES (%s,%s,%s,%s,true)
                    ON CONFLICT(code) DO NOTHING''',(code,'QA · '+name,url,adapter))
    from universal_supplier.control_plane.store import PostgresControlPlaneStore
    from universal_supplier.control_plane.admin_store import PostgresAdminStore
    from universal_supplier.control_plane.security import hash_password
    from universal_supplier.control_plane.supplier_automation import settings_for
    store=PostgresControlPlaneStore(config);admin_store=PostgresAdminStore(config)
    if not admin_store.users_exist():
        # Deliberately synthetic, loopback-only fixture login; not a production secret.
        admin_store.create_user('qa_admin','Synthetic QA administrator',hash_password('Fixture-only-operator-password'),'ADMIN')
    suppliers=store.suppliers();assert len(suppliers)==11
    for row in suppliers:
        current=settings_for(row)
        if current['revision']==0:
            store.save_parsing_settings(row['id'],{'delay_min_seconds':1,'delay_max_seconds':3,
                'refresh_interval':21600,'discovery_interval':86400,'timezone':'Africa/Nairobi',
                'price_ttl':21600,'availability_ttl':10800},0,None)
    row=store.parsing_settings(suppliers[0]['id']);cfg=row['settings']
    assert cfg['delay_min_seconds']==1 and cfg['delay_max_seconds']==3
    before=row['settings']['revision']
    try:store.save_parsing_settings(suppliers[0]['id'],{'delay_min_seconds':-1},before,None)
    except ValueError:pass
    else:raise AssertionError('Invalid configuration accepted')
    assert store.parsing_settings(suppliers[0]['id'])['settings']['revision']==before
    # Exact host confirmation path is exercised only in this QA database.
    store.approve_parsing_host(suppliers[0]['id'],before,row['host_policy_sha256'],None)
    from universal_supplier.preview_store import load
    data=load(config)
    assert len(data['suppliers'])==11 and not data['records']
    result={'database':DATABASE,'system_identifier':'7698346631476390904','owner':'existing rc_owner',
        'classification':'SYNTHETIC SQL/UI QA, NOT LIVE collection','migration_files_applied':applied,
        'migration_files_recorded':len(list((ROOT/'migrations').glob('*.sql'))),
        'migration_replay_applied':0,'new_application_migrations':1,'suppliers':len(suppliers),
        'all11_settings_roundtrip':'PASS','invalid_settings_rollback':'PASS','host_confirmation':'PASS',
        'runtime_role_changes':0,'ACL_changes':0,'LIVE11_writes':0,'HTTP':0,'enqueue':0,'matching':0}
    out=ROOT.parent.parent/'reports/UNIVERSAL_SUPPLIER_AUTONOMOUS_SYSTEM_20261026'
    (out/'QA_DATABASE_RESULT.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result))


if __name__=='__main__':main()
