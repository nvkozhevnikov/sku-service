"""Bounded post-test RC verification. Read-only SQL; evidence files only."""
import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
import xml.etree.ElementTree as ET

import psycopg
from scripts.persist_kami_full_run1 import preflight
from scripts.run2_kami_offline import verify_migrations
from scripts.recover_kami_selection_predelete import fingerprints, digest

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'reports/KAMI_INTEGRATION_2026-10-05'
PRIOR=BASE/'CANONICAL_AUTHORITY_RC_2026-10-06'
OUT=BASE/'MANUAL_OVERLAY_GUARD_FIX_2026-10-06'
PRIVATE=Path(os.environ['LOCALAPPDATA'])/'UniversalSupplier/kami-rc-20261005'


def main():
    os.environ['GIT_OPTIONAL_LOCKS']='0'
    git=lambda *args:subprocess.check_output(['git',*args],cwd=ROOT,text=True).strip()
    cp=json.loads((PRIOR/'CHECKPOINT.json').read_bytes())
    assert git('branch','--show-current')==cp['branch']
    assert git('rev-parse','HEAD')==cp['current_SHA']
    for branch in ('develop','production'):
        assert git('rev-parse',branch)==cp['safety'][branch+'_after']
    changed={'universal_supplier/postgres.py','tests/test_kami_lifecycle_db.py'}
    for path,sha in cp['changed_files_SHA256'].items():
        if path not in changed:
            assert hashlib.sha256((ROOT/path).read_bytes()).hexdigest()==sha,path
    totals={}
    for name in ('LIFECYCLE_DB.xml','FOCUSED.xml'):
        cases=list(ET.parse(OUT/name).iter('testcase'))
        counts={'PASS':0,'FAIL':0,'ERROR':0,'SKIP':0}
        for case in cases:
            key='FAIL' if case.find('failure') is not None else 'ERROR' if case.find('error') is not None else 'SKIP' if case.find('skipped') is not None else 'PASS'
            counts[key]+=1
        totals[name]=counts
    assert sum(totals['LIFECYCLE_DB.xml'].values())==26
    assert totals['FOCUSED.xml']['PASS']==98
    prior=json.loads((PRIOR/'RC_UNCHANGED.json').read_bytes())['fingerprints']
    marker=json.loads((BASE/'DATABASE/RESTORE_VERIFICATION.json').read_bytes())
    password=(PRIVATE/'pgpass.conf').read_text().strip().split(':',4)[4]
    with psycopg.connect(host='127.0.0.1',port=55451,dbname='universal_supplier_kami_rc',user='kami_admin',password=password,
                         autocommit=True,connect_timeout=5,options='-c default_transaction_read_only=on -c statement_timeout=300000') as c:
        preflight(c,marker)
        with c.transaction():
            c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
            identity=c.execute("SELECT current_database(),host(inet_server_addr()),inet_server_port(),current_setting('server_version'),system_identifier::text FROM pg_control_system()").fetchone()
            assert identity==('universal_supplier_kami_rc','127.0.0.1',55451,'17.11','7693098812512405428')
            migrations=verify_migrations(c)
            assert len(migrations)==15
            print('READ_ONLY_IDENTITY_PASS; comparing all public tables/sequences',flush=True)
            after=fingerprints(c)
            assert after==prior,'RC fingerprints differ: STOP'
            selection=[r[0] for r in c.execute('SELECT to_jsonb(t) FROM catalog_offer_selection t ORDER BY to_jsonb(t)::text')]
            selection_sha=digest(selection)
            assert selection_sha=='d66a5f0ab4579593c0c55f04674d1b65374d86478e903ee3b36745e0fea69004'
            statuses=dict(c.execute('SELECT selection_status,count(*) FROM catalog_offer_selection GROUP BY 1'))
            assert statuses=={'selected':117,'no_eligible_offer':4} and len(selection)==121
    runtime=json.loads((PRIVATE/'runtime.json').read_bytes())
    with psycopg.connect(host='127.0.0.1',port=55451,dbname='universal_supplier_kami_rc',user='kami_app',password=runtime['DB_PASSWORD'],
                         autocommit=True,connect_timeout=5,options='-c default_transaction_read_only=on') as c:
        role=c.execute("SELECT current_user,(SELECT rolsuper FROM pg_roles WHERE rolname=current_user),current_setting('transaction_read_only')").fetchone()
        assert role==('kami_app',False,'on')
    status='MANUAL_CONFIRMED_OVERLAY_GUARD_FIXED_VERIFIED' if all(not t['FAIL'] and not t['ERROR'] and not t['SKIP'] for t in totals.values()) else 'STOP_P2_MANUAL_PROTECTED_SELECTION_FEED_CONTRACT'
    result={'status':status,'identity':identity,'role':role,
            'tests':totals,'before_fingerprints':prior,'after_fingerprints':after,'selection_sha256':selection_sha,
            'selection_rows':121,'selection_statuses':statuses,'RC_DB_writes':0,'HTTP':0,'commit_push':0}
    save=lambda name,data:(OUT/name).write_text(json.dumps(data,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    save('VERIFIED.json',result)
    new_files=['universal_supplier/postgres.py','tests/test_kami_lifecycle_db.py','scripts/verify_manual_overlay_guard_rc.py','docs/MANUAL_MAPPING_OVERLAY_GUARD.md']
    updated={**cp['changed_files_SHA256'],**{p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in new_files}}
    checkpoint={'status':result['status'],'recorded_at':datetime.now(timezone.utc).isoformat(),'resume_from':str(OUT/'CHECKPOINT.json'),
                'predecessor':str(PRIOR/'CHECKPOINT.json'),'branch':cp['branch'],'starting_SHA':cp['current_SHA'],'current_SHA':git('rev-parse','HEAD'),
                'DONE':['manual ledger lifecycle analysis','bounded guard','scratch and focused regressions','read-only RC fingerprint verification'],
                'APPLIED':{'code':new_files,'scratch_only_test_writes':True,'RC_DB_writes':0},
                'VERIFIED':{'tests':totals,'same_target':'PASS REVIEW/CONFLICT/repeat','different_target':'PASS','superseded':'PASS','automatic_quarantine':'PASS REVIEW/CONFLICT/NEW_CANDIDATE','rollback':'PASS business rows; normal PostgreSQL scratch sequence gaps are not rolled back','RC_all_tables_sequences_unchanged':True,'selection_sha256':selection_sha},
                'CURRENT_P2':'Manual-protected physical link remains selected after automatic REVIEW/CONFLICT but build_yml rejects NO_CURRENT_ACCEPTED_MATCH. Reproduced on second scratch pack run; no feed fix authorized/applied.',
                'NOT_APPLIED':['RC matching/quarantine/apply','exports','dump/restore','commit/push','F-P1 export implementation'],
                'safety':{**cp['safety'],'RC_DML_DDL':0},'changed_files_SHA256':updated,
                'credential_diagnostic_incident':cp['credential_diagnostic_incident'],
                'NEXT_ACTION':'STOP. Await separate operator scope; do not resume release pipeline automatically.'}
    checkpoint['evidence_SHA256']={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in OUT.iterdir() if p.is_file() and p.name!='CHECKPOINT.json'}
    save('CHECKPOINT.json',checkpoint)
    print(json.dumps({'status':result['status'],'tests':totals,'RC_all_hashes_unchanged':True,'selection':[121,117,4],'RC_writes':0}))


if __name__=='__main__':main()
