"""Deterministic target-only mapping of disjoint LTT/USS namespaces.

KAMI aggregate is restored unchanged as the base. No matching/selection runs.
Competitor staging is moved to its own schema before logical transfer.
"""
import csv
import hashlib
import json
import os
import secrets
import subprocess
from pathlib import Path
import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from rc_prepare_database import connect,run,OUT,ROOT,TARGET,PORT,DATA

TABLES=['suppliers','source_products','offers','supplier_http_captures','offer_commercial_observations']
REFS={'supplier_id':'suppliers','source_product_id':'source_products','offer_id':'offers','capture_id':'supplier_http_captures'}

def digest(rows):
    return hashlib.sha256(json.dumps(rows,default=str,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def migrate(c):
    """Same official migration logic, using immutable Git LF blobs."""
    import sys
    sys.path.insert(0,str(ROOT/'scripts'))
    from docker_postgres_migrate import migration_body,expected_hashes
    expected=expected_hashes()
    for path in sorted((ROOT/'migrations').glob('[0-9][0-9][0-9]_*.sql')):
        data=subprocess.check_output(['git','-C',str(ROOT),'show','58152e4:migrations/'+path.name])
        sha=hashlib.sha256(data).hexdigest()
        if path.name in expected and expected[path.name]!=sha:raise RuntimeError('Immutable migration mismatch')
        prior=c.execute('SELECT sha256 FROM schema_migrations WHERE filename=%s',(path.name,)).fetchone()
        if prior:
            if prior[0]!=sha:raise RuntimeError('Historical migration mismatch: '+path.name)
        else:
            with c.transaction():
                c.execute(migration_body(data.decode()))
                c.execute('INSERT INTO schema_migrations(filename,sha256) VALUES(%s,%s)',(path.name,sha))

def main():
    manifest={};mapping=[]
    with connect(TARGET) as target,connect('rc_stage_ltt_uss') as source:
        assert target.execute('SELECT current_database()').fetchone()[0]==TARGET
        assert target.execute("SELECT count(*) FROM suppliers WHERE code IN ('ltt_msk','ltt_sib','ltt_yug','uss_stanko')").fetchone()[0]==0
        migrate(target)
        source.row_factory=dict_row
        source.execute('SET default_transaction_read_only=on')
        maps={}
        with target.transaction():
            target.execute('CREATE TABLE rc_migration_map(source_db text,source_schema text,table_name text,old_pk bigint,new_pk bigint,PRIMARY KEY(source_db,source_schema,table_name,old_pk))')
            for table in TABLES:
                rows=source.execute(sql.SQL('SELECT * FROM {} ORDER BY id').format(sql.Identifier(table))).fetchall()
                offset=target.execute(sql.SQL('SELECT coalesce(max(id),0) FROM {}').format(sql.Identifier(table))).fetchone()[0]
                maps[table]={row['id']:offset+i+1 for i,row in enumerate(rows)}
                projected=[]
                for row in rows:
                    old=row['id'];record=dict(row);record['id']=maps[table][old]
                    if table=='source_products':assert row['catalog_product_id'] is None
                    for column,parent in REFS.items():
                        if column in row and row[column] is not None:
                            record[column]=maps[parent][row[column]]
                    projected.append(record)
                    mapping.append(('rc_stage_ltt_uss','public',table,old,record['id']))
                columns=list(rows[0]) if rows else []
                if columns:
                    command=sql.SQL('INSERT INTO {} ({}) OVERRIDING SYSTEM VALUE VALUES ({})').format(sql.Identifier(table),sql.SQL(',').join(map(sql.Identifier,columns)),sql.SQL(',').join(sql.Placeholder() for _ in columns))
                    with target.cursor() as cur:
                        cur.executemany(command,[[Jsonb(r[k]) if isinstance(r[k],(dict,list)) else r[k] for k in columns] for r in projected])
                    actual=target.execute(sql.SQL('SELECT * FROM {} WHERE id=ANY(%s) ORDER BY id').format(sql.Identifier(table)),(list(maps[table].values()),)).fetchall()
                    actual_dicts=[dict(zip(columns,r)) for r in actual]
                    if digest(actual_dicts)!=digest(projected):raise RuntimeError('Mapped payload mismatch '+table)
                manifest[table]={'rows':len(rows),'source_sha256':digest(rows),'mapped_sha256':digest(projected),'verified':True}
            with target.cursor() as cur:
                cur.executemany('INSERT INTO rc_migration_map VALUES(%s,%s,%s,%s,%s)',mapping)
            for table in TABLES:
                seq=target.execute('SELECT pg_get_serial_sequence(%s,%s)',(table,'id')).fetchone()[0]
                if seq:
                    target.execute(sql.SQL('SELECT setval(%s,(SELECT max(id) FROM {}),true)').format(sql.Identifier(table)),(seq,))
    # New, isolated staging only: schema rename retains all PK/FK/sequence bytes.
    with connect('rc_stage_competitor') as c:
        c.execute('ALTER SCHEMA public RENAME TO competitor')
    path=OUT/'competitor_schema.dump'
    if path.exists():raise RuntimeError('No overwrite of schema backup')
    run('pg_dump','-h','127.0.0.1','-p',PORT,'-U','rc_owner','-d','rc_stage_competitor','-n','competitor','-Fc','--no-owner','--no-acl','-f',path)
    run('pg_restore','-h','127.0.0.1','-p',PORT,'-U','rc_owner','-d',TARGET,'--no-owner','--no-acl','--exit-on-error',path)
    with connect(TARGET) as c:
        c.execute('CREATE TABLE competitor.rc_identity(marker text PRIMARY KEY)')
        c.execute("INSERT INTO competitor.rc_identity VALUES('STERBRUST_UNIFIED_RC_20261019')")
        c.execute('REVOKE ALL ON SCHEMA public FROM PUBLIC')
        c.execute('REVOKE ALL ON SCHEMA competitor FROM PUBLIC')
        secrets_map={}
        for role in ('app_supplier','app_competitor','reporting_ro'):
            password=secrets.token_urlsafe(36);secrets_map[role]=password
            c.execute(sql.SQL('CREATE ROLE {} LOGIN PASSWORD {}').format(sql.Identifier(role),sql.Literal(password)))
            c.execute(sql.SQL('GRANT CONNECT ON DATABASE {} TO {}').format(sql.Identifier(TARGET),sql.Identifier(role)))
        c.execute('GRANT USAGE ON SCHEMA public TO app_supplier, reporting_ro')
        c.execute('GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA public TO app_supplier')
        c.execute('GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA public TO app_supplier')
        c.execute('GRANT SELECT ON ALL TABLES IN SCHEMA public TO reporting_ro')
        c.execute('GRANT USAGE ON SCHEMA competitor TO app_competitor,reporting_ro')
        c.execute('GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA competitor TO app_competitor')
        c.execute('GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA competitor TO app_competitor')
        c.execute('GRANT SELECT ON ALL TABLES IN SCHEMA competitor TO reporting_ro')
        c.execute('ALTER ROLE app_competitor SET search_path=competitor,pg_catalog')
        c.execute('ALTER ROLE app_supplier SET search_path=public,pg_catalog')
        c.execute('ALTER ROLE reporting_ro SET search_path=public,competitor,pg_catalog')
        manifest['target_system_identifier']=str(c.execute('SELECT system_identifier FROM pg_control_system()').fetchone()[0])
    # Secrets exist only in ignored runtime. Host connections require SCRAM.
    (OUT/'private-runtime.json').write_text(json.dumps(secrets_map),encoding='utf-8')
    hba=DATA/'pg_hba.conf'
    text=hba.read_text();hba.write_text(text.replace('trust','scram-sha-256'))
    # Keep rc_owner bootstrap access loopback-only for backup/rehearsal.
    hba.write_text('host all rc_owner 127.0.0.1/32 trust\n'+hba.read_text())
    run('pg_ctl','-D',DATA,'reload')
    with (OUT/'UNIFIED_DB_MAPPING.csv').open('w',newline='',encoding='utf-8') as f:
        writer=csv.writer(f);writer.writerow(['source_db','source_schema','table','old_pk','target_pk']);writer.writerows(mapping)
    (OUT/'DB_CONSOLIDATION.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    print(json.dumps(manifest,indent=2))

if __name__=='__main__':main()
