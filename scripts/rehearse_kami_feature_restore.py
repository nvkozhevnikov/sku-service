"""Restore the NEW versioned dump into a NEW database; never original RC."""
import argparse,hashlib,json,os,subprocess
from pathlib import Path
import psycopg
from psycopg import sql
from scripts.persist_kami_full_run1 import preflight
from scripts.run2_kami_offline import verify_migrations
from scripts.verify_kami_canary import digest

ROOT=Path(__file__).resolve().parents[1];BASE=ROOT/'reports/KAMI_INTEGRATION_2026-10-05'
OUT=BASE/'RESTORE_REHEARSAL';DB='universal_supplier_kami_restore_20261006'
BIN=Path(os.environ['LOCALAPPDATA'])/'Temp/universal_supplier_stage4_pg17_runtime/bin'
DUMP=ROOT/'release/database/universal_supplier_kami_rc_2026-10-06.dump'
PRIVATE=Path(os.environ['LOCALAPPDATA'])/'UniversalSupplier/kami-rc-20261005'
FINAL=False

def connection(database):
    password=(PRIVATE/'pgpass.conf').read_text().strip().split(':',4)[4]
    return psycopg.connect(host='127.0.0.1',port=55451,dbname=database,user='kami_admin',password=password,connect_timeout=5,autocommit=True)

def snapshot(conn):
    with conn.transaction():
        conn.execute('SET TRANSACTION READ ONLY')
        counts={r[0]:r[1] for r in conn.execute("SELECT s.code,count(p.id) FROM suppliers s LEFT JOIN source_products p ON p.supplier_id=s.id GROUP BY s.code ORDER BY s.code")}
        tables=['suppliers','source_products','offers','product_matches','catalog_offer_selection','catalog_products','supplier_http_captures','offer_commercial_observations','product_properties','product_media']
        sizes={name:conn.execute(sql.SQL('SELECT count(*) FROM {}').format(sql.Identifier(name))).fetchone()[0] for name in tables}
        verified_migrations=verify_migrations(conn)
        assert conn.execute("SELECT count(*) FROM pg_constraint WHERE contype='f' AND NOT convalidated").fetchone()[0]==0
        ids=digest(conn.execute("SELECT s.code,p.external_id,p.id FROM source_products p JOIN suppliers s ON s.id=p.supplier_id ORDER BY s.code,p.external_id").fetchall())
        kami=conn.execute("SELECT count(*),count(DISTINCT external_id),count(*) FILTER(WHERE catalog_product_id IS NOT NULL) FROM source_products WHERE supplier_id=(SELECT id FROM suppliers WHERE code='kami')").fetchone()
        assert kami==(5264,5264,0)
        review=conn.execute("SELECT count(*) FROM product_matches m JOIN source_products p ON p.id=m.source_product_id JOIN suppliers s ON s.id=p.supplier_id WHERE s.code='kami' AND m.is_current AND m.status='REVIEW'").fetchone()[0]
        assert review==5264
        assert conn.execute("SELECT count(*) FROM offers o JOIN suppliers s ON s.id=o.supplier_id WHERE s.code='kami' AND o.active").fetchone()[0]==0
        assert conn.execute("SELECT enabled FROM suppliers WHERE code='kami'").fetchone()[0] is False
        hashes={}
        for (table,) in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"):
            hashes[table]=conn.execute(sql.SQL("SELECT count(*),encode(sha256(convert_to(coalesce(string_agg(md5(to_jsonb(t)::text),'' ORDER BY to_jsonb(t)::text),''),'UTF8')),'hex') FROM {} t").format(sql.Identifier(table))).fetchone()
        sequences=conn.execute("SELECT schemaname,sequencename,last_value FROM pg_sequences WHERE schemaname='public' ORDER BY sequencename").fetchall()
        selection=conn.execute("SELECT cs.catalog_product_id,cs.selected_offer_id,s.code,p.external_id,s.enabled,o.active,p.catalog_product_id FROM catalog_offer_selection cs JOIN offers o ON o.id=cs.selected_offer_id JOIN source_products p ON p.id=o.source_product_id JOIN suppliers s ON s.id=p.supplier_id WHERE cs.selection_status='selected' ORDER BY cs.catalog_product_id").fetchall()
        if FINAL:
            accepted={ (r['source'],str(r['external_id'])):r for r in json.loads((BASE/'MATCHING/MATCHING_ACCEPTED.json').read_bytes())['rows'] }
            for canonical,offer,source,external,enabled,active,linked in selection:
                assert accepted[(source,str(external))]['classification']=='EXISTING_CONFIRMED'
                assert enabled and active and linked==canonical
            from scripts.reconcile_kami_selection import recompute,verify_set
            _,_,expected,diff=recompute(conn);verify_set(conn,expected)
            assert all(r['action']=='KEEP' for r in diff)
            receipt=json.loads((BASE/'SELECTION_RECONCILIATION_2026-10-06/SELECTION_PERSISTED.json').read_bytes())
            assert sorted([[r[2],str(r[3])] for r in selection])==receipt['selected']
        return {'namespaces':counts,'tables':sizes,'source_PK_identity_hash':ids,'FKs_validated':conn.execute("SELECT count(*) FROM pg_constraint WHERE contype='f'").fetchone()[0],'migrations':len(verified_migrations),'KAMI_REVIEW':review,
                'all_public_table_content_hashes':hashes,'sequences':sequences,'selected':selection}

def main():
    global OUT,DB,DUMP,FINAL
    parser=argparse.ArgumentParser();parser.add_argument('--final',action='store_true');args=parser.parse_args();FINAL=args.final
    if FINAL:
        OUT=BASE/'FINAL_RESTORE_REHEARSAL_2026-10-06';DB='universal_supplier_kami_final_restore_20261006'
        DUMP=ROOT/'release/database/universal_supplier_kami_feature_rc_2026-10-06.dump'
        assert json.loads((BASE/'SELECTION_RECONCILIATION_2026-10-06/VERIFIED.json').read_bytes())['status']=='LIVE_VERIFIED_TRANSACTIONAL_SELECTION_RECONCILIATION'
    OUT.mkdir(exist_ok=True);marker=json.loads((BASE/'DATABASE/RESTORE_VERIFICATION.json').read_bytes())
    with connection('universal_supplier_kami_rc') as source:
        preflight(source,marker);expected=snapshot(source)
        if source.execute('SELECT EXISTS(SELECT 1 FROM pg_database WHERE datname=%s)',(DB,)).fetchone()[0]:
            raise RuntimeError('Disposable restore DB exists: no overwrite/drop/restore retry')
        if FINAL:
            if DUMP.exists(): raise RuntimeError('Final dump exists; no overwrite')
            os.environ['PGPASSFILE']=str(PRIVATE/'pgpass.conf')
            with (OUT/'dump.log').open('wb') as log:
                result=subprocess.run([str(BIN/'pg_dump.exe'),'-h','127.0.0.1','-p','55451','-U','kami_admin','-d','universal_supplier_kami_rc','-Fc','-f',str(DUMP)],stdout=log,stderr=subprocess.STDOUT)
            if result.returncode: raise RuntimeError('Final pg_dump failed; preserve partial evidence, no overwrite retry')
        source.execute(sql.SQL('CREATE DATABASE {} OWNER kami_app').format(sql.Identifier(DB)))
    os.environ['PGPASSFILE']=str(PRIVATE/'pgpass.conf')
    for args,log in [(['--list','--file',str(OUT/'DUMP_TOC.list'),str(DUMP)],'list.log'),
                     (['-h','127.0.0.1','-p','55451','-U','kami_admin','-d',DB,'--role','kami_app',
                       '--no-owner','--no-privileges','--exit-on-error','--single-transaction',str(DUMP)],'restore.log')]:
        with (OUT/log).open('wb') as handle:
            result=subprocess.run([str(BIN/'pg_restore.exe'),*args],stdout=handle,stderr=subprocess.STDOUT)
        if result.returncode:raise RuntimeError('pg_restore failed; inspect '+log+'; no repair/retry')
    with connection(DB) as restored:
        identity=restored.execute('SELECT current_database(),inet_server_port(),system_identifier::text FROM pg_control_system()').fetchone()
        assert identity==(DB,55451,marker['system_identifier'])
        actual=snapshot(restored);assert actual==expected
    with connection('universal_supplier_kami_rc') as source:
        preflight(source,marker);assert snapshot(source)==expected
    report={'status':'LIVE_VERIFIED_NEW_DATABASE_RESTORE_REHEARSAL_PASS','source_database':'universal_supplier_kami_rc',
            'restored_database':DB,'port':55451,'same_cluster_new_database':True,'system_identifier':marker['system_identifier'],
            'source_unchanged':True,'dump':str(DUMP),'dump_bytes':DUMP.stat().st_size,
            'dump_SHA256':hashlib.sha256(DUMP.read_bytes()).hexdigest(),'pg_restore_list':'PASS','pg_restore_errors':0,
            'verified':actual,'accepted_readiness_state':'two FULL NEW in versioned accepted package; not fabricated SQL canonical products',
            'selection_state':'FINAL_COMMITTED_SQL_SELECTION_VERIFIED' if FINAL else 'DIAGNOSTIC_LEGACY_SELECTION',
            'source_HTTP':0,'Sterbrust_ESOL_writes':0,'drop_or_cleanup':'NOT_PERFORMED; disposable DB retained as evidence'}
    (OUT/'VERIFIED.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False),flush=True)

if __name__=='__main__':main()
