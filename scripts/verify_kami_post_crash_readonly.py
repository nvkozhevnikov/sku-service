"""Read-only re-verification of the recovered state; no pipeline resumes."""
import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from psycopg import sql
from scripts.persist_kami_full_run1 import preflight
from scripts.run2_kami_offline import verify_migrations

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'reports/KAMI_INTEGRATION_2026-10-05'
PRIOR=BASE/'SELECTION_PRE_DELETE_RECOVERY_2026-10-06'
OUT=BASE/'POST_CRASH_REVERIFY_2026-10-06'
PRIVATE=Path(os.environ['LOCALAPPDATA'])/'UniversalSupplier/kami-rc-20261005'


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,default=str).encode()).hexdigest()


def main():
    os.environ['GIT_OPTIONAL_LOCKS']='0'
    git=lambda *a:subprocess.check_output(['git',*a],cwd=ROOT,text=True,stderr=subprocess.DEVNULL).strip()
    cp=json.loads((PRIOR/'CHECKPOINT.json').read_bytes())
    previous=json.loads((PRIOR/'VERIFIED.json').read_bytes())
    marker=json.loads((BASE/'DATABASE/RESTORE_VERIFICATION.json').read_bytes())
    assert git('branch','--show-current')==cp['branch']
    assert git('rev-parse','HEAD')==cp['current_SHA']
    assert git('rev-parse','develop')==cp['safety']['develop_after']
    assert git('rev-parse','production')==cp['safety']['production_after']
    for path,sha in cp['changed_files_SHA256'].items():
        assert hashlib.sha256((ROOT/path).read_bytes()).hexdigest()==sha, 'Working file drift: '+path
    for path,sha in cp['evidence_SHA256'].items():
        assert hashlib.sha256((PRIOR/path).read_bytes()).hexdigest()==sha, 'Evidence drift: '+path
    password=(PRIVATE/'pgpass.conf').read_text().strip().split(':',4)[4]
    kwargs=dict(host='127.0.0.1',port=55451,dbname='universal_supplier_kami_rc',user='kami_admin',
                password=password,connect_timeout=5,autocommit=True,
                options='-c default_transaction_read_only=on -c statement_timeout=300000',
                application_name='codex_post_crash_readonly_verify')
    with psycopg.connect(**kwargs) as conn:
        preflight(conn,marker)
        with conn.transaction():
            conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
            identity=conn.execute("SELECT current_database(),host(inet_server_addr()),inet_server_port(),current_setting('server_version'),system_identifier::text,pg_is_in_recovery() FROM pg_control_system()").fetchone()
            assert identity==('universal_supplier_kami_rc','127.0.0.1',55451,'17.11',cp['database']['system_identifier'],False)
            migrations=verify_migrations(conn)
            assert len(migrations)==cp['public_table_counts_after']['schema_migrations']
            selected=[r[0] for r in conn.execute('SELECT to_jsonb(t) FROM catalog_offer_selection t ORDER BY to_jsonb(t)::text')]
            expected=json.loads((PRIOR/'PRE_DELETE_SNAPSHOT.json').read_bytes())['rows']
            assert selected==expected
            assert digest(selected)==cp['VERIFIED']['after_table_SHA256']
            statuses=dict(conn.execute('SELECT selection_status,count(*) FROM catalog_offer_selection GROUP BY 1'))
            assert statuses=={'selected':cp['VERIFIED']['selected'],'no_eligible_offer':cp['VERIFIED']['no_eligible_offer']}
            print('IDENTITY_SELECTION_MIGRATIONS_PASS; hashing ALL public tables',flush=True)
            hashes={}
            for (table,) in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"):
                query=sql.SQL("SELECT count(*),encode(sha256(convert_to(coalesce(string_agg(encode(sha256(convert_to(to_jsonb(t)::text,'UTF8')),'hex'),'' ORDER BY to_jsonb(t)::text),''),'UTF8')),'hex') FROM {} t").format(sql.Identifier(table))
                count,sha=conn.execute(query).fetchone();hashes[table]={'rows':count,'sha256':sha}
                assert hashes[table]==previous['after_fingerprints'][table], 'Business table drift: '+table
            hashes['sequences']={'sha256':digest(conn.execute("SELECT schemaname,sequencename,last_value FROM pg_sequences WHERE schemaname='public' ORDER BY sequencename").fetchall())}
            assert hashes==previous['after_fingerprints'], 'Fingerprint table-set or sequence drift'
            kami=conn.execute("SELECT s.enabled,count(p.id),count(o.id),count(*) FILTER(WHERE o.active),count(*) FILTER(WHERE p.catalog_product_id IS NOT NULL),count(DISTINCT p.external_id) FROM suppliers s JOIN source_products p ON p.supplier_id=s.id JOIN offers o ON o.source_product_id=p.id WHERE s.code='kami' GROUP BY s.enabled").fetchone()
            assert kami==(False,5264,5264,0,0,5264)
            assert conn.execute("SELECT count(*) FROM product_matches m JOIN source_products p ON p.id=m.source_product_id JOIN suppliers s ON s.id=p.supplier_id WHERE s.code='kami' AND m.is_current AND m.status='REVIEW' AND NOT m.auto_accepted").fetchone()[0]==5264
            assert conn.execute("SELECT count(*) FROM pg_constraint WHERE contype='f' AND NOT convalidated").fetchone()[0]==0
    config=json.loads((PRIVATE/'runtime.json').read_bytes())
    assert (config['DB_HOST'],str(config['DB_PORT']),config['DB_NAME'],config['DB_USER'])==('127.0.0.1','55451','universal_supplier_kami_rc','kami_app')
    app_kwargs={**kwargs,'user':'kami_app','password':config['DB_PASSWORD']}
    with psycopg.connect(**app_kwargs) as conn, conn.transaction():
        conn.execute('SET TRANSACTION READ ONLY')
        role=conn.execute("SELECT current_database(),host(inet_server_addr()),inet_server_port(),current_user,(SELECT rolsuper FROM pg_roles WHERE rolname=current_user),current_setting('transaction_read_only')").fetchone()
        assert role==('universal_supplier_kami_rc','127.0.0.1',55451,'kami_app',False,'on')
        assert digest([r[0] for r in conn.execute('SELECT to_jsonb(t) FROM catalog_offer_selection t ORDER BY to_jsonb(t)::text')])==cp['VERIFIED']['after_table_SHA256']
    log=(OUT/'postgres_start.log').read_bytes().decode('cp1251')
    assert not any(token in log for token in ('PANIC:', 'ОШИБКА:', 'ВАЖНО:', 'FATAL:')),'Startup log error; STOP'
    report={'status':'POST_CRASH_RECOVERY_STATE_REVERIFIED','recorded_at':datetime.now(timezone.utc).isoformat(),
            'identity':identity,'application_role':role,'migrations':migrations,'selection_rows':len(selected),
            'selection_status_counts':statuses,'selection_sha256':digest(selected),'all_public_table_sequence_fingerprints':hashes,
            'kami':{'supplier_enabled':False,'products':5264,'offers':5264,'active_offers':0,'canonical_links':0,'current_REVIEW':5264},
            'checkpoint_code_evidence_hashes_match':True,'DB_application_writes_after_restart':0,'recovery_repeated':False,
            'SOURCE_RUN1_RUN2_matching_repeated':False,'HTTP':0,'quarantine_selection_apply':0,'exports_dump':0,'commit_push':0,
            'develop':git('rev-parse','develop'),'production':git('rev-parse','production'),'HEAD':git('rev-parse','HEAD'),
            'postgres_start':'one normal pg_ctl start; native crash REDO/end-of-recovery checkpoint; no manual PID action/repair',
            'GLM_F_P1_01':'NOT_APPLIED; separate operator authoritative export/matching decision required',
            'STOP':'Do not proceed to quarantine, matching persistence, canonical apply, exports, dump or commit/push'}
    (OUT/'VERIFIED.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k not in {'all_public_table_sequence_fingerprints','migrations'}},ensure_ascii=False))


if __name__=='__main__':main()
