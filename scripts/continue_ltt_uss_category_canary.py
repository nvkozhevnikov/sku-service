"""Explicit operator decision transition; retained DB, pending rows only, never RUN1."""
from pathlib import Path
from datetime import datetime, timezone
import copy
import hashlib
import json
import sys
import psycopg
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import run_ltt_uss_live_canary as runner
from scripts.run_ltt_uss_offline_canary import fingerprints, save
from universal_supplier.adapters.ltt_yug_role import classify_yug_page, DECISION_REF
from universal_supplier.commercial_persistence import HttpCapture

BASE = runner.BASE
OLD = BASE / 'UNICODE_RESUME_2026-10-14'
OUT = BASE / 'CATEGORY_RESUME_2026-10-14'


def config():
    private = json.loads((BASE / 'PRIVATE/connection.json').read_text('utf-8'))
    runtime = private['runtime']
    assert (runtime['host'], runtime['port'], runtime['dbname']) == ('127.0.0.1', 55455, 'universal_supplier_ltt_uss')
    return private, runtime


def verify_system():
    private, runtime = config()
    admin = dict(runtime, user='ltt_uss_admin', password=private['admin_password'])
    with psycopg.connect(**admin, autocommit=True) as c:
        c.execute('SET default_transaction_read_only=on')
        version = c.execute('SHOW server_version').fetchone()[0]
        system = str(c.execute('SELECT system_identifier FROM pg_control_system()').fetchone()[0])
        assert version == '17.11' and system == '7696153366151515704'
        return dict(version=version, system_identifier=system)


def reconcile(state, initial=False):
    system = verify_system()
    private, runtime = config()
    baseline = json.loads((OLD / 'FINAL_DB_VERIFICATION.json').read_text('utf-8'))['fingerprints']
    with psycopg.connect(**runtime, autocommit=True) as c:
        c.execute('SET default_transaction_read_only=on')
        c.execute("SET application_name='ltt_uss_category_readonly_verification'")
        after = fingerprints(c)
        changed = [n for n in baseline if baseline[n] != after[n] and (initial or n not in runner.ALLOWED)]
        assert not changed, changed
        if initial:
            assert (after['source_products']['count'], after['offers']['count'], after['offer_commercial_observations']['count']) == (51,51,58)
        ids = c.execute("""SELECT p.external_id,p.id,o.id,o.price,o.availability_normalized
            FROM source_products p JOIN suppliers s ON s.id=p.supplier_id
            JOIN offers o ON o.source_product_id=p.id
            WHERE s.code='ltt_yug' AND p.external_id IN ('1','312','297') ORDER BY p.external_id""").fetchall()
        assert len(ids)==2 and {r[0] for r in ids}=={'312','297'} and ids[0][1]!=ids[1][1] and ids[0][2]!=ids[1][2]
        assert not c.execute('SELECT supplier_id,external_id FROM source_products GROUP BY supplier_id,external_id HAVING count(*)>1').fetchall()
        class Repo: pass
        repo=Repo();repo.connection=c
        verified=[]
        for row in state['rows']:
            if row['outcome']!='PERSISTED': continue
            evidence_root=BASE/row.get('evidence_root','CATEGORY_RESUME_2026-10-14/EVIDENCE')
            body=(evidence_root/row['supplier']/(row['evidence_sha256']+'.html')).read_bytes()
            assert hashlib.sha256(body).hexdigest()==row['evidence_sha256']
            product=runner.parse(row['supplier'],body,row['final_url'])
            metadata=dict(row['capture']);metadata['observed_at']=datetime.fromisoformat(metadata['observed_at'])
            metadata['redirects']=tuple(metadata['redirects']);metadata['diagnostics']=tuple(metadata['diagnostics'])
            assert runner.verify_persisted(repo,product,HttpCapture(**metadata))==row['db_verification']
            verified.append(dict(supplier=row['supplier'],external_id=row['external_id'],source_product_id=row['db_verification']['source_product_id']))
        # The retained MSK/SIB tables and counts must not change while other suppliers continue.
        counts=c.execute("""SELECT s.code,count(DISTINCT p.id),count(DISTINCT o.id),count(DISTINCT c.id),count(DISTINCT b.id)
            FROM suppliers s LEFT JOIN source_products p ON p.supplier_id=s.id
            LEFT JOIN offers o ON o.source_product_id=p.id LEFT JOIN supplier_http_captures c ON c.source_product_id=p.id
            LEFT JOIN offer_commercial_observations b ON b.capture_id=c.id GROUP BY s.code ORDER BY s.code""").fetchall()
        by_supplier={r[0]:r[1:] for r in counts}
        assert by_supplier['ltt_msk']==(20,20,22,22) and by_supplier['ltt_sib']==(20,20,22,22)
        result=dict(status='READ_ONLY_VERIFIED',**system, fingerprints=after,protected_delta=[],
            source_products=after['source_products']['count'],offers=after['offers']['count'],
            captures=after['supplier_http_captures']['count'],observations=after['offer_commercial_observations']['count'],
            yug_rows=ids,yug_id1_present=False,yug_identity_collapse=False,counts=counts,verified_rows=verified,
            checked_at=datetime.now(timezone.utc).isoformat(),successful_rows_replayed=0)
        save(OUT/('RESUME_BASELINE.json' if initial else 'FINAL_DB_VERIFICATION.json'),result)
        print(json.dumps({k:result[k] for k in ['status','source_products','offers','observations','yug_id1_present']},default=str),flush=True)
        return result


def main():
    if (OUT/'LIVE_CANARY.json').exists():
        raise RuntimeError('Transition already exists; do not replay. Inspect journal/checkpoint.')
    state=json.loads((OLD/'LIVE_CANARY.json').read_text('utf-8'))
    assert state['status']=='LIVE_CANARY_BLOCKED'
    for row in state['rows']:
        row.setdefault('evidence_root','UNICODE_RESUME_2026-10-14/EVIDENCE')
    reconcile(state, initial=True)
    for row in state['rows']:
        row.setdefault('evidence_root','UNICODE_RESUME_2026-10-14/EVIDENCE')
    bad=[r for r in state['rows'] if r['outcome']=='PARSE_ERROR']
    assert len(bad)==1 and bad[0]['supplier']=='ltt_yug'
    blocked=bad[0]
    frozen=(BASE/blocked['evidence_root']/'ltt_yug'/(blocked['evidence_sha256']+'.html')).read_bytes()
    assert hashlib.sha256(frozen).hexdigest()==blocked['evidence_sha256']
    assert classify_yug_page(frozen.decode('utf8'),blocked['requested_url'])=='CATEGORY_PAGE'
    state.setdefault('prior_attempt_history',[]).append(copy.deepcopy(blocked))
    blocked.update(outcome='NON_PRODUCT_CATEGORY_PAGE',page_role='CATEGORY_PAGE',role_provenance='OPERATOR-CONFIRMED',
        decision=DECISION_REF,nonpersisted_metadata_only=True,role_resolved_at=datetime.now(timezone.utc).isoformat())
    blocked.pop('error',None)
    for supplier in runner.SOURCES:
        body=(BASE/'MANIFESTS'/(supplier+'.json')).read_bytes()
        state['plans'][supplier]['manifest_sha256']=hashlib.sha256(body).hexdigest()
    assert len(state['rows'])==57 and len({(r['supplier'],r['requested_url']) for r in state['rows']})==57
    state['sources']={s:runner.gate([r for r in state['rows'] if r['supplier']==s],s) for s in ('ltt_msk','ltt_sib')}
    assert all(g['status']=='LIVE_CANARY_PASS' for g in state['sources'].values())
    state.update(status='RUNNING',transition='OPERATOR_CONFIRMED_YUG_CATEGORY_PENDING_ONLY',completed_rows_replayed=0,
        run1_started=False,run2_started=False,full_qa_started=False,decision=DECISION_REF,supplier_gets_before=state['supplier_gets'])
    for key in ('source','stage','blocker','root_cause','stopped_at'):state.pop(key,None)
    save(OUT/'LIVE_CANARY.json',state)
    runner.OUT=OUT
    exit_code=runner.main()
    finished=json.loads((OUT/'LIVE_CANARY.json').read_text('utf-8'))
    proof=reconcile(finished)
    finished.update(source_products=proof['source_products'],offers=proof['offers'],observations=proof['observations'],
        current_session_supplier_gets=finished['supplier_gets']-finished['supplier_gets_before'],yug_id1_persisted=False)
    save(OUT/'LIVE_CANARY.json',finished)
    return exit_code


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    raise SystemExit(main())
