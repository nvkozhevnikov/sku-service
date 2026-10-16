"""Source-ingestion packaging only: readonly source, custom dump, isolated restore.

Never repairs ingestion, runs matching/selection, or publishes Git. Restore DATA
is new and retained stopped; source identity and all baseline fingerprints gate dump.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import psycopg
from scripts import run_ltt_uss_run1 as r
from scripts import run_ltt_uss_run2_offline as o
from scripts import export_ltt_uss_readonly_snapshot as x
from scripts import page_role_seals
from scripts.verify_inverse97_resume import same

OUT = r.BASE / 'SOURCE_INGESTION_FINALIZATION_2026-10-16'
ACCEPTED = r.BASE / 'PAGE_ROLE_REMEDIATION_2026-10-16'
DUMP = OUT / 'LTT_USS_SOURCE_INGESTION_VERIFIED_2026-10-16.dump'
TARGET = OUT / 'PRIVATE' / ('restore_' + uuid.uuid4().hex[:12])
PORT = 55457
DB = 'ltt_uss_checkpoint_restore'


def run(args, name, env=None):
    with (OUT / (name + '.log')).open('wb') as log:
        result = subprocess.run([str(a) for a in args], stdout=log,
                                stderr=subprocess.STDOUT, env=env, timeout=900)
    assert result.returncode == 0, (name, result.returncode)
    return result


def control(data):
    return subprocess.check_output([str(x.BIN / 'pg_controldata.exe'), str(data)], text=True)


def start(data, port, name):
    assert not x.port_listening(port) and not (data / 'postmaster.pid').exists()
    run([x.BIN / 'pg_ctl.exe', 'start', '-D', data, '-l', OUT / (name + '_server.log'),
         '-o', f'-h 127.0.0.1 -p {port}', '-w', '-t', '30'], name + '_start')


def stop(data, port, name):
    if (data / 'postmaster.pid').exists():
        run([x.BIN / 'pg_ctl.exe', 'stop', '-D', data, '-m', 'fast', '-w', '-t', '30'], name + '_stop')
    state = control(data)
    assert 'shut down' in state and not x.port_listening(port) and not (data / 'postmaster.pid').exists()
    result = dict(status='CLEAN_SERVER_STOPPED', port=port, data=str(data.resolve()),
                  port_closed=True, postmaster_pid_absent=True, control=state, checked_at=r.now())
    r.save(OUT / (name + '_SHUTDOWN.json'), result)
    return result


def inspect(c):
    c.execute('SET default_transaction_read_only=on')
    assert c.execute('SHOW transaction_read_only').fetchone()[0] == 'on'
    snap = o.snapshot(c)
    assert snap['totals'] == dict(source_products=2453, offers=2453,
                                supplier_http_captures=2533, offer_commercial_observations=2533)
    active = c.execute('SELECT count(*) FROM source_products WHERE active').fetchone()[0]
    assert active == 2449
    articles = c.execute('SELECT p.id,p.active,p.missed_crawls,o.id,o.active FROM source_products p JOIN offers o ON o.source_product_id=p.id WHERE p.id BETWEEN 225 AND 228 ORDER BY p.id').fetchall()
    assert articles == [(i, False, 0, i, False) for i in range(225,229)]
    checks = {}
    for name, sql in {
        'offers_without_product':'SELECT count(*) FROM offers o LEFT JOIN source_products p ON p.id=o.source_product_id WHERE p.id IS NULL',
        'captures_without_product':'SELECT count(*) FROM supplier_http_captures c LEFT JOIN source_products p ON p.id=c.source_product_id WHERE c.source_product_id IS NOT NULL AND p.id IS NULL',
        'observations_without_offer':'SELECT count(*) FROM offer_commercial_observations b LEFT JOIN offers o ON o.id=b.offer_id WHERE o.id IS NULL',
        'observations_without_capture':'SELECT count(*) FROM offer_commercial_observations b LEFT JOIN supplier_http_captures c ON c.id=b.capture_id WHERE c.id IS NULL',
        'unvalidated_foreign_keys':"SELECT count(*) FROM pg_constraint WHERE contype='f' AND NOT convalidated",
    }.items():
        checks[name] = c.execute(sql).fetchone()[0]
    assert not any(checks.values()), checks
    return snap, dict(active_source_products=active, articles=articles, integrity=checks,
                      clients=o.clients(c))


def execute():
    assert not DUMP.exists(), 'Never overwrite an existing backup'
    assert not TARGET.exists() and not x.port_listening(PORT)
    assert not x.port_listening(55455) and not (x.DATA / 'postmaster.pid').exists()
    assert x.EXPECTED_SYSTEM_ID in control(x.DATA) and 'shut down' in control(x.DATA)
    assert subprocess.check_output([str(x.BIN / 'pg_dump.exe'), '--version'], text=True).strip().endswith('17.11')
    page_role_seals.verify(baseline_seal=page_role_seals.ACCEPTED, proposed_transition=page_role_seals.PROPOSED)
    qa = r.load(ACCEPTED / 'FRESH_FULL_QA/FRESH_400_SEMANTIC_RESULTS.json')
    assert qa['passed'] == qa['reviewed'] == 400 and qa['failed'] == qa['material_blockers'] == 0
    baseline = r.load(ACCEPTED / 'PAGE_ROLE_REVISED_BASELINE.json')
    pinned = {str(p.relative_to(ACCEPTED)): r.digest(p.read_bytes()) for p in ACCEPTED.rglob('*.json')}
    r.save(OUT / 'ACCEPTED_EVIDENCE_PINS.json', pinned)
    source_started = restore_started = False
    try:
        start(x.DATA,55455,'SOURCE'); source_started=True
        with x.connect() as c:
            identity=x.check_identity(x.identity(c))
            snap, facts=inspect(c); same(snap,baseline)
            r.save(OUT/'SOURCE_LIVE_SNAPSHOT.json',snap)
            r.save(OUT/'SOURCE_LIVE_VERIFICATION.json',dict(status='PASS',identity=identity,**facts,
                protected_cohorts='All 2453 full product/offer projections and 51 table fingerprints equal accepted final FULL QA baseline; includes hotspot36,disk10,inverse97,kind29,operator,YUG,SIB',checked_at=r.now()))
        private,cfg=x.config()
        env=dict(os.environ,PGPASSWORD=private['admin_password'],PGCLIENTENCODING='UTF8')
        run([x.BIN/'pg_dump.exe','-h','127.0.0.1','-p','55455','-U','ltt_uss_admin','-d',cfg['dbname'],
             '-Fc','--no-owner','--no-privileges','-f',DUMP], 'PG_DUMP',env)
        sha=r.digest(DUMP.read_bytes()); assert DUMP.stat().st_size>0
        head=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()
        branch=subprocess.check_output(['git','branch','--show-current'],text=True).strip()
        assert branch=='feature/universal-supplier-ltt-uss-ingestion'
        r.save(OUT/'DB_BACKUP_MANIFEST.json',dict(filename=DUMP.name,absolute_path=str(DUMP.resolve()),size=DUMP.stat().st_size,sha256=sha,
             postgres_version='17.11',source_system_id=x.EXPECTED_SYSTEM_ID,database=cfg['dbname'],timestamp=r.now(),
             source_counts=snap['totals'],active_source_products=2449,branch=branch,pre_commit_head=head,
             full_qa='400/400 PASS',format='PostgreSQL custom; no owner/ACL; external artifact, never Git',name='SOURCE-INGESTION VERIFIED CHECKPOINT DATABASE'))
        (OUT/'DB_BACKUP_SHA256.txt').write_text(sha+'  '+DUMP.name+'\n',encoding='utf8')
        TARGET.parent.mkdir(parents=True,exist_ok=True)
        run([x.BIN/'initdb.exe','-D',TARGET,'-U','checkpoint_restore','--auth=trust','--encoding=UTF8','--locale=C'],'RESTORE_INITDB')
        start(TARGET,PORT,'RESTORE'); restore_started=True
        run([x.BIN/'createdb.exe','-h','127.0.0.1','-p',PORT,'-U','checkpoint_restore',DB],'RESTORE_CREATEDB')
        run([x.BIN/'pg_restore.exe','-h','127.0.0.1','-p',PORT,'-U','checkpoint_restore','-d',DB,
             '--no-owner','--no-privileges','--exit-on-error',DUMP],'PG_RESTORE')
        with psycopg.connect(host='127.0.0.1',port=PORT,user='checkpoint_restore',dbname=DB,autocommit=True) as c:
            restored,rfacts=inspect(c);same(restored,snap)
            assert c.execute('SHOW server_version').fetchone()[0]=='17.11'
            restored_system=c.execute('SELECT system_identifier::text FROM pg_control_system()').fetchone()[0]
            assert restored_system!=x.EXPECTED_SYSTEM_ID
        r.save(OUT/'DB_RESTORE_VERIFICATION.json',dict(RESTORE_TEST='PASS',RESTORE_ERRORS=0,
            RESTORE_TARGET=dict(data=str(TARGET.resolve()),port=PORT,database=DB,system_identifier=restored_system),
            RESTORED_DB_COUNTS=restored['totals'],RESTORED_ACTIVE_SOURCE_PRODUCTS=2449,
            RESTORE_FINGERPRINT_RESULT='PASS: all51 tables,40 sequences,all2453 product/offer pairs and special cases identical',
            **rfacts,checked_at=r.now()))
        with x.connect() as c:
            after,afacts=inspect(c);same(after,snap)
            r.save(OUT/'SOURCE_AFTER_BACKUP.json',dict(status='PASS',table_delta=0,sequence_delta=0,**afacts))
        print('LIVE BASELINE PASS; DUMP',DUMP.stat().st_size,sha,'RESTORE PASS',flush=True)
    finally:
        if restore_started or (TARGET/'postmaster.pid').exists(): stop(TARGET,PORT,'RESTORE')
        if source_started or (x.DATA/'postmaster.pid').exists(): stop(x.DATA,55455,'SOURCE')


if __name__=='__main__':
    execute()
