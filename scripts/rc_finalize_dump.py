"""Portable full dump and independent empty-DB restore of this partial RC.

Only own target/staging database names are allowed. Never restore source dumps.
"""
import hashlib,json,os,sys,subprocess
from pathlib import Path
from psycopg import sql
from rc_prepare_database import ROOT,OUT,PORT,TARGET,connect,run,restore
from rc_verify_database import fingerprint

DELIVERY=ROOT/'release/DELIVERY_2026-10-19_RC1'
RESTORE='sterbrust_unified_restore_20261019'

def sequences(c):
    rows=c.execute("SELECT schemaname,sequencename FROM pg_sequences WHERE schemaname IN ('public','competitor') ORDER BY 1,2").fetchall()
    return {s+'.'+n:c.execute(sql.SQL('SELECT last_value,is_called FROM {}.{}').format(sql.Identifier(s),sql.Identifier(n))).fetchone() for s,n in rows}

def db_state(c):
    return {'public':fingerprint(c),'competitor':fingerprint(c,'competitor'),'sequences':sequences(c),
            'schema_migrations':c.execute('SELECT filename,sha256 FROM schema_migrations ORDER BY filename').fetchall(),
            'constraints':c.execute("SELECT n.nspname,t.relname,c.conname,pg_get_constraintdef(c.oid),c.convalidated FROM pg_constraint c JOIN pg_class t ON t.oid=c.conrelid JOIN pg_namespace n ON n.oid=t.relnamespace WHERE n.nspname IN ('public','competitor') ORDER BY 1,2,3").fetchall()}

def main():
    directory=DELIVERY/'database';directory.mkdir(parents=True,exist_ok=True)
    dump=directory/'sterbrust_unified_2026-10-19_RC1.dump'
    if dump.exists():raise RuntimeError('Refuse overwriting a versioned release dump')
    with connect(TARGET) as c:
        assert c.execute('SELECT current_database()').fetchone()[0]==TARGET
        c.execute('SET default_transaction_read_only=on');before=db_state(c)
        namespaces=c.execute('SELECT s.code,count(sp.id),count(sp.id) FILTER(WHERE sp.active) FROM suppliers s LEFT JOIN source_products sp ON sp.supplier_id=s.id GROUP BY s.id ORDER BY s.code').fetchall()
        maximum=c.execute('SELECT max(observed_at) FROM offer_commercial_observations').fetchone()[0]
        system=str(c.execute('SELECT system_identifier FROM pg_control_system()').fetchone()[0])
    run('pg_dump','-h','127.0.0.1','-p',PORT,'-U','rc_owner','-d',TARGET,'-Fc','--no-owner','--no-acl','-f',dump)
    toc=run('pg_restore','--list',dump);(directory/'DUMP_CONTENTS.txt').write_bytes(toc)
    restore(RESTORE,dump)
    with connect(RESTORE) as c,connect(TARGET) as source:
        c.execute('SET default_transaction_read_only=on');source.execute('SET default_transaction_read_only=on')
        restored=db_state(c);after=db_state(source)
        assert restored==before==after,'Restore/current-source fingerprint mismatch'
    # --no-acl is portable; grants provisioned explicitly on restore copy only.
    with connect(RESTORE) as c:
        c.execute('REVOKE ALL ON SCHEMA public FROM PUBLIC')
        c.execute('GRANT USAGE ON SCHEMA public TO app_supplier,reporting_ro,app_dispatch')
        c.execute('GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA public TO app_supplier')
        c.execute('GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA public TO app_supplier')
        c.execute('GRANT USAGE ON SCHEMA competitor TO app_competitor,reporting_ro,app_dispatch')
        c.execute('GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA competitor TO app_competitor')
        c.execute('GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA competitor TO app_competitor')
        c.execute('GRANT SELECT ON ALL TABLES IN SCHEMA public,competitor TO reporting_ro,app_dispatch')
        c.execute('GRANT INSERT,UPDATE ON rc_replay_jobs TO app_dispatch')
        c.execute('GRANT USAGE,SELECT ON SEQUENCE rc_replay_jobs_id_seq TO app_dispatch')
    # Restored application's actual guarded read paths, one login.
    sys.path.insert(0,str(ROOT));import rc_runtime
    old_target=rc_runtime.TARGET
    os.environ['STERBRUST_UNIFIED_RC_RESTORE']='YES';rc_runtime.TARGET=RESTORE
    try:
        from fastapi.testclient import TestClient
        with TestClient(rc_runtime.build_app()) as client:
            response=client.post('/login',data={'username':'rc-review','password':rc_runtime.credentials()['qa_login_password']},headers={'origin':'http://testserver'},follow_redirects=False)
            assert response.status_code==303
            for route in ('/products','/competitor-monitoring/news','/release-jobs'):
                page=client.get(route);assert page.status_code==200 and 'База мониторинга сейчас недоступна' not in page.text
    finally:
        rc_runtime.TARGET=old_target;os.environ.pop('STERBRUST_UNIFIED_RC_RESTORE',None)
    sha=hashlib.file_digest(dump.open('rb'),'sha256').hexdigest()
    receipt={'status':'PASS','scope':'Available-data partial RC; not a complete release','source_db':TARGET,'restore_db':RESTORE,'port':PORT,'source_system_identifier':system,'independent_target':'New empty database in own isolated PG17.11 cluster; CREATE DATABASE template0, no existing DB overwritten','all_table_fingerprints_equal':True,'all_sequences_equal':True,'all_constraints_equal':True,'schema_migrations_equal':True,'source_unchanged_during_dump_restore':True,'restored_application_readback':'PASS: shared login + products + competitor news + replay panel','source_db_writes':0,'http_requests_external':0,'dump':str(dump),'bytes':dump.stat().st_size,'sha256':sha,'grants':'No ACL dump; explicit role/schema/table grants rehearsed on restore target only','known_omissions':['Vekprom and newer Intervesp 55452: BLOCKED_MISSING_VERIFIED_SOURCE_DATA'],'matching_gate':'NOT_VERIFIED_PENDING_LINUX_AND_CHURN_REVIEW'}
    manifest={'db_name':TARGET,'postgres_version':'17.11','snapshot_cutoff':'2026-10-19','created_by_engine_commit':subprocess.check_output(['git','-C',str(ROOT),'rev-parse','HEAD']).decode().strip(),'source_manifest_sha256':hashlib.sha256((OUT/'DB_INPUTS.json').read_bytes()).hexdigest(),'per_namespace':namespaces,'max_observed_at':maximum,'database_state':before,'source_system_identifier':system,'dump_sha256':sha,'dump_bytes':dump.stat().st_size,'known_omissions':receipt['known_omissions'],'release_complete':False,'matching_verified':False}
    for path in (OUT/'DB_RESTORE_RECEIPT.json',directory/'RESTORE_RECEIPT.json'):path.write_text(json.dumps(receipt,indent=2),encoding='utf-8')
    (directory/'DB_MANIFEST.json').write_text(json.dumps(manifest,default=str,indent=2),encoding='utf-8')
    print(json.dumps(receipt,indent=2))
if __name__=='__main__':main()
