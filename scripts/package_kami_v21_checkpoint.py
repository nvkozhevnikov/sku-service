"""Snapshot-consistent new dump + NEW DB restore, source business read-only.

Never invokes matching, ingestion, selection or a server start. Credentials stay
in the existing private passfile. Existing files/databases cannot be overwritten.
"""
import hashlib,json,os,subprocess
from datetime import datetime,timezone
from pathlib import Path
from psycopg import sql
from scripts.rehearse_kami_feature_restore import connection,PRIVATE,BIN
from scripts.persist_kami_full_run1 import preflight
from scripts.run2_kami_offline import verify_migrations
from scripts.recover_kami_selection_predelete import fingerprints
from scripts.apply_kami_policy_v2_1 import accepted_map
from scripts.verify_kami_canary import digest

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'reports/KAMI_MATCHING_POLICY_V2_1_FINALIZATION_2026-10-08'
PREV=ROOT/'reports/KAMI_MATCHING_POLICY_V2_2026-10-08'
DB='universal_supplier_kami_rc'
RESTORE='universal_supplier_kami_v21_restore_20261008'
DUMP=ROOT/'release/database/universal_supplier_kami_matching_v2_1_2026-10-08.dump'


def save(name,value):
    OUT.mkdir(exist_ok=True)
    (OUT/name).write_text(json.dumps(value,ensure_ascii=False,indent=2,default=str),encoding='utf8')


def inspect(c,database,expected):
    identity=c.execute("SELECT current_database(),host(inet_server_addr()),inet_server_port(),current_setting('server_version'),system_identifier::text FROM pg_control_system()").fetchone()
    assert identity==(database,'127.0.0.1',55451,'17.11','7693098812512405428')
    migrations=verify_migrations(c);fp=fingerprints(c)
    links=accepted_map(c);assert links==expected
    statuses=dict(c.execute("""SELECT pm.status,count(*) FROM product_matches pm JOIN source_products sp ON sp.id=pm.source_product_id
        JOIN suppliers s ON s.id=sp.supplier_id WHERE s.code='kami' AND pm.is_current GROUP BY pm.status"""))
    assert statuses=={'HIGH_CONFIDENCE_MATCH':717,'REVIEW':4547}
    namespaces=dict(c.execute("SELECT s.code,count(sp.id) FROM suppliers s LEFT JOIN source_products sp ON sp.supplier_id=s.id GROUP BY s.code ORDER BY s.code"))
    assert namespaces=={'partner_st':1225,'optimum':1353,'intervesp':1642,'beka_mak':109,'beka_mak_tr':78,'kami':5264}
    selection=dict(c.execute('SELECT selection_status,count(*) FROM catalog_offer_selection GROUP BY selection_status'))
    assert selection=={'selected':117,'no_eligible_offer':4}
    assert c.execute("SELECT enabled FROM suppliers WHERE code='kami'").fetchone()==(False,)
    assert c.execute("SELECT count(*),count(*) FILTER(WHERE active) FROM offers o JOIN suppliers s ON s.id=o.supplier_id WHERE s.code='kami'").fetchone()==(5264,0)
    assert c.execute("SELECT count(*) FROM pg_constraint WHERE contype='f' AND NOT convalidated").fetchone()[0]==0
    seq=c.execute("SELECT sequencename,last_value,increment_by,cache_size FROM pg_sequences WHERE schemaname='public' ORDER BY sequencename").fetchall()
    return dict(identity=identity,migrations=migrations,fingerprints=fp,namespaces=namespaces,statuses=statuses,
        accepted_identity_sha256=digest(sorted(links.items())),accepted_links=links,selection=selection,sequences=seq)


def main():
    OUT.mkdir(exist_ok=True)
    git=lambda *a:subprocess.check_output(['git',*a],cwd=ROOT,text=True).strip()
    assert git('branch','--show-current')=='feature/universal-supplier-kami-rc1'
    assert git('rev-parse','HEAD')=='c6e26ec242899535dcef06f90a789a0a5c0110d0'
    if DUMP.exists() or DUMP.with_suffix('.dump.partial').exists():raise RuntimeError('New dump target already exists; no overwrite/retry')
    marker=json.loads((ROOT/'reports/KAMI_INTEGRATION_2026-10-05/DATABASE/RESTORE_VERIFICATION.json').read_bytes())
    proposal=json.loads((PREV/'V2_1_PROPOSAL_DATA.json').read_bytes())
    expected={r['external_id']:r['sterbrust_product_id'] for r in proposal['rows'] if r['sterbrust_product_id']}
    baseline=json.loads((PREV/'V2_1_SECOND_RUN_NOOP.json').read_bytes())['after_fingerprints']
    env=os.environ.copy();env.pop('PGPASSWORD',None);env.update(PGPASSFILE=str(PRIVATE/'pgpass.conf'),PGSSLMODE='disable')
    for exe in ('pg_dump.exe','pg_restore.exe'):
        version=subprocess.check_output([str(BIN/exe),'--version'],text=True).strip()
        assert '17.11' in version
    with connection(DB) as c:
        preflight(c,marker)
        with c.transaction():
            c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            before=inspect(c,DB,expected);assert before['fingerprints']==baseline
            save('DB_BEFORE_FINGERPRINTS.json',before['fingerprints'])
            save('MATCHING_STATE.json',dict(total=5264,Existing=717,Review=4547,Conflict=0,
                matched_numeric_public=90,exact_numeric=88,FROM_PRICE=2,supplier_disabled=True,offers_inactive=True,
                namespaces=before['namespaces'],selection=before['selection'],accepted_links=before['accepted_links']))
            save('MATCHING_IDENTITY_HASHES.json',dict(accepted_identity_sha256=before['accepted_identity_sha256'],
                source_table=before['fingerprints']['source_products'],match_table=before['fingerprints']['product_matches'],
                namespaces=before['namespaces'],scope='Exact source namespace/external IDs -> real Sterbrust IDs; full public fingerprints preserve predecessor state'))
            snapshot=c.execute('SELECT pg_export_snapshot()').fetchone()[0]
            locales=c.execute('SELECT datcollate,datctype,datlocprovider FROM pg_database WHERE datname=current_database()').fetchone()
            assert locales[2]=='c','Unexpected locale provider; stop rather than substitute'
            DUMP.parent.mkdir(exist_ok=True)
            partial=DUMP.with_suffix('.dump.partial')
            cmd=[str(BIN/'pg_dump.exe'),'-h','127.0.0.1','-p','55451','-U','kami_admin','-d',DB,
                 '-Fc','--no-owner','--no-privileges','--snapshot='+snapshot,'-f',str(partial)]
            with (OUT/'pg_dump.log').open('wb') as log:
                result=subprocess.run(cmd,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=1200)
            if result.returncode:raise RuntimeError('pg_dump failed; partial preserved, no automatic retry')
            assert partial.stat().st_size>0
            partial.rename(DUMP)
    sha=hashlib.file_digest(DUMP.open('rb'),'sha256').hexdigest()
    with (OUT/'pg_restore_list.log').open('wb') as log:
        result=subprocess.run([str(BIN/'pg_restore.exe'),'--list','--file',str(OUT/'DUMP_TOC.list'),str(DUMP)],env=env,stdout=log,stderr=subprocess.STDOUT,timeout=60)
    assert result.returncode==0
    dump_meta=dict(path=str(DUMP),bytes=DUMP.stat().st_size,sha256=sha,modified_at=datetime.fromtimestamp(DUMP.stat().st_mtime,timezone.utc).isoformat(),
        recorded_at=datetime.now(timezone.utc).isoformat(),database=DB,PG_version='17.11',system_identifier=marker['system_identifier'],
        format='PostgreSQL custom',migrations=before['migrations'],matching={'total':5264,'Existing':717,'Review':4547,'Conflict':0},
        pg_restore_list='PASS',snapshot_consistent=True,source_business_mutation=0,git_tracked=False)
    save('FINAL_DUMP.json',dump_meta)
    with connection('postgres') as maintenance:
        assert maintenance.execute('SELECT inet_server_port(),system_identifier::text FROM pg_control_system()').fetchone()==(55451,marker['system_identifier'])
        if maintenance.execute('SELECT EXISTS(SELECT 1 FROM pg_database WHERE datname=%s)',(RESTORE,)).fetchone()[0]:raise RuntimeError('Restore DB exists: refuse overwrite/drop/retry')
        maintenance.execute(sql.SQL('CREATE DATABASE {} OWNER kami_app TEMPLATE template0 ENCODING {} LC_COLLATE {} LC_CTYPE {}').format(
            sql.Identifier(RESTORE),sql.Literal('UTF8'),sql.Literal(locales[0]),sql.Literal(locales[1])))
    save('CHECKPOINT.json',dict(status='NEW_DUMP_VERIFIED_RESTORE_RUNNING',dump=dump_meta,restore_database=RESTORE,
        original_business_write_count=0,next='Do not repeat dump/CREATE DATABASE. Read restore log/verification and source fingerprints.'))
    with (OUT/'pg_restore.log').open('wb') as log:
        result=subprocess.run([str(BIN/'pg_restore.exe'),'-h','127.0.0.1','-p','55451','-U','kami_admin','-d',RESTORE,
            '--role','kami_app','--no-owner','--no-privileges','--exit-on-error','--single-transaction',str(DUMP)],env=env,stdout=log,stderr=subprocess.STDOUT,timeout=1200)
    if result.returncode:raise RuntimeError('Restore failed; preserve fresh DB/log, no repair/retry')
    with connection(RESTORE) as restored:
        with restored.transaction():
            restored.execute('SET TRANSACTION READ ONLY');actual=inspect(restored,RESTORE,expected)
    for key in ('fingerprints','migrations','namespaces','statuses','accepted_identity_sha256','accepted_links','selection','sequences'):
        assert actual[key]==before[key],'Restore evidence mismatch: '+key
    with connection(DB) as source:
        preflight(source,marker)
        with source.transaction():
            source.execute('SET TRANSACTION READ ONLY');after=inspect(source,DB,expected)
    assert after==before,'Original RC business mutation during finalization'
    save('DB_AFTER_FINGERPRINTS.json',after['fingerprints'])
    verification=dict(status='NEW_DATABASE_RESTORE_EXACT_VERIFIED',database=RESTORE,source_database=DB,
        same_approved_cluster=True,source_unchanged=True,public_tables_sequences_exact_equal=True,accepted_identity_sha256=actual['accepted_identity_sha256'],
        restored_fingerprints=actual['fingerprints'],restored_namespaces=actual['namespaces'],migrations=actual['migrations'],
        selection=actual['selection'],sequences=actual['sequences'],dump_sha256=sha,restore_exit=0,retained_as_evidence=True,
        source_business_writes=0,selection_changes=0,supplier_HTTP=0)
    save('RESTORE_VERIFICATION.json',verification)
    save('CHECKPOINT.json',dict(status='DUMP_RESTORE_VERIFIED_GIT_FINALIZATION_PENDING',dump=dump_meta,restore=verification,
        original_business_write_count=0,starting_SHA=git('rev-parse','HEAD'),next='Focused tests + docs + sanitized feature diff -> feature-only commit/push. Never rerun dump/restore/matching/selection.'))
    print(json.dumps(dict(status=verification['status'],dump=dump_meta['path'],bytes=dump_meta['bytes'],sha256=sha,
        restore_database=RESTORE,source_unchanged=True),ensure_ascii=False))


if __name__=='__main__':main()
