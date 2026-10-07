"""New empty scratch DB + unchanged migrations only, no RC business mutation."""
import hashlib,json,os
from pathlib import Path
import psycopg
from psycopg import sql
from scripts.persist_kami_full_run1 import preflight
from scripts.reconcile_kami_selection import connection,PRIVATE,BASE,ROOT

DB='universal_supplier_kami_lifecycle_test_20261006'
OUT=BASE/'CANONICAL_AUTHORITY_RC_2026-10-06'


def main():
    OUT.mkdir(exist_ok=True)
    marker=json.loads((BASE/'DATABASE/RESTORE_VERIFICATION.json').read_bytes())
    with connection() as source:
        preflight(source,marker)
        assert not source.execute('SELECT EXISTS(SELECT 1 FROM pg_database WHERE datname=%s)',(DB,)).fetchone()[0], 'Scratch exists; no overwrite/drop'
        source.execute(sql.SQL('CREATE DATABASE {} OWNER kami_app TEMPLATE template0').format(sql.Identifier(DB)))
    password=(PRIVATE/'pgpass.conf').read_text().strip().split(':',4)[4]
    with psycopg.connect(host='127.0.0.1',port=55451,dbname=DB,user='kami_admin',password=password,autocommit=True) as c:
        assert c.execute('SELECT current_database(),system_identifier::text FROM pg_control_system()').fetchone()==(DB,marker['system_identifier'])
        c.execute('SET ROLE kami_app')
        c.execute('CREATE TABLE schema_migrations(filename text PRIMARY KEY,sha256 char(64) NOT NULL,applied_at timestamptz NOT NULL DEFAULT now())')
        for path in sorted((ROOT/'migrations').glob('[0-9][0-9][0-9]_*.sql')):
            c.execute(path.read_text(encoding='utf-8'))
            c.execute('INSERT INTO schema_migrations(filename,sha256) VALUES(%s,%s)',(path.name,hashlib.sha256(path.read_bytes()).hexdigest()))
        assert c.execute('SELECT count(*) FROM schema_migrations').fetchone()[0]==15
    receipt={'status':'NEW_SCRATCH_DB_READY','database':DB,'port':55451,'system_identifier':marker['system_identifier'],'migrations':15,'RC_business_writes':0,'scratch_only':True,'no_drop_restore':True}
    (OUT/'SCRATCH_READY.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')
    print(json.dumps(receipt))


if __name__=='__main__':main()
